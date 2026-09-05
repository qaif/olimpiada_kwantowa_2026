"""CSP i storage po wprowadzeniu Wagtaila (T-09).

Reguły, które muszą zostać domknięte testem:

1. luźniejsza polityka CSP obowiązuje **wyłącznie** w panelu (``/cms/``, ``/admin/``) – strony
   publiczne nadal nie mają ``'unsafe-inline'`` w ``script-src``,
2. ``Problem.statement_pdf`` nie leży na ``default`` storage, bo ten jest w produkcji publicznym
   bucketem Wagtaila; treść zadania serwuje widok aplikacji i dopiero po ``opens_at``,
3. origin publicznego bucketu jest w ``img-src``/``media-src`` – inaczej produkcja blokuje każdy
   obraz redakcyjny, a dev tego nie pokaże, bo tam storage jest lokalny (przegląd Critica, f. 1),
4. dokumenty Wagtaila idą przez widok (``serve_view``) i respektują ograniczenia kolekcji (f. 3),
5. ``EmbedBlock`` przyjmuje wyłącznie YouTube i Vimeo – tych samych, co ``frame-src`` (f. 4).
"""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from wagtail.embeds.exceptions import EmbedUnsupportedProviderException
from wagtail.embeds.finders import get_finders

from apps.accounts.tests.factories import DEFAULT_PASSWORD
from apps.cms.models import NewsIndexPage, NewsPage
from apps.competitions.models import Problem
from apps.competitions.storage import PRIVATE_MEDIA_ALIAS

pytestmark = pytest.mark.django_db

STORAGE_ORIGIN = "https://s3.example.test"


def directive(policy: str, name: str) -> str:
    return next(part for part in policy.split("; ") if part.startswith(f"{name} "))


def script_src(policy: str) -> str:
    return directive(policy, "script-src")


def test_public_pages_keep_nonce_only_script_policy(web_client):
    for url in ("/", "/aktualnosci/", "/zadania/", "/wyniki/"):
        policy = web_client.get(url).headers["Content-Security-Policy"]

        assert "'unsafe-inline'" not in script_src(policy), url
        assert "'unsafe-eval'" not in script_src(policy), url
        assert "'nonce-" in script_src(policy), url
        assert "frame-ancestors 'none'" in policy, url


def test_admin_paths_get_the_relaxed_policy(web_client, coordinator):
    web_client.login(email=coordinator.email, password=DEFAULT_PASSWORD)

    for url in ("/cms/", "/admin/"):
        policy = web_client.get(url).headers["Content-Security-Policy"]

        assert "'unsafe-inline'" in script_src(policy), url
        # Nonce i 'unsafe-inline' wzajemnie się znoszą – w polityce panelu nonce'a być nie może.
        assert "'nonce-" not in policy, url
        assert "frame-ancestors 'self'" in policy, url


@override_settings(S3_PUBLIC_ENDPOINT_URL=STORAGE_ORIGIN)
def test_public_policy_allows_images_from_the_public_media_bucket(web_client):
    """Obrazy i renditions Wagtaila stoją w buckecie, nie na naszym origin (przegląd, finding 1)."""
    policy = web_client.get("/").headers["Content-Security-Policy"]

    assert STORAGE_ORIGIN in directive(policy, "img-src")
    assert STORAGE_ORIGIN in directive(policy, "media-src")
    # ``connect-src`` miał ten origin już wcześniej (pdf.js) – tu pilnujemy, że nie zniknął.
    assert STORAGE_ORIGIN in directive(policy, "connect-src")


@override_settings(S3_PUBLIC_ENDPOINT_URL=STORAGE_ORIGIN)
def test_admin_policy_allows_images_from_the_public_media_bucket(web_client, coordinator):
    """Biblioteka mediów w ``/cms/`` pokazuje miniatury prosto z bucketu."""
    web_client.login(email=coordinator.email, password=DEFAULT_PASSWORD)

    policy = web_client.get("/cms/").headers["Content-Security-Policy"]

    assert STORAGE_ORIGIN in directive(policy, "img-src")
    assert STORAGE_ORIGIN in directive(policy, "media-src")


def test_public_policy_limits_frames_to_the_allowed_embed_providers(web_client):
    frame_src = directive(web_client.get("/").headers["Content-Security-Policy"], "frame-src")

    assert "https://www.youtube.com" in frame_src
    assert "https://www.youtube-nocookie.com" in frame_src
    assert "https://player.vimeo.com" in frame_src
    # Zamknięta lista, nie „https:” – to jest cała różnica między tą polityką a polityką panelu.
    assert "https:" not in frame_src.replace("https://", "")


def test_namespaced_admin_view_gets_the_admin_policy(web_client, coordinator):
    """Polityka idzie za ``resolver_match``/prefiksem panelu, a nie za literałem w kodzie.

    ``/cms/images/`` ma własną przestrzeń nazw (``wagtailimages``), a nie ``wagtailadmin`` –
    to jest ten przypadek, na którym samo dopasowanie po przestrzeni nazw by poległo.
    """
    web_client.login(email=coordinator.email, password=DEFAULT_PASSWORD)

    policy = web_client.get("/cms/images/").headers["Content-Security-Policy"]

    assert "'unsafe-inline'" in script_src(policy)


def test_statement_pdf_uses_the_private_storage_not_wagtail_default():
    field_storage = Problem._meta.get_field("statement_pdf").storage

    assert field_storage is storages[PRIVATE_MEDIA_ALIAS]
    assert field_storage is not storages["default"]


def test_statement_pdf_lands_outside_the_default_storage_tree(open_stage, tmp_path):
    """Alias to za mało: plik ma **fizycznie** leżeć poza drzewem storage ``default``.

    W produkcji rozdziela je bucket, lokalnie i w testach – podkatalog ``private/``. Bez tej
    asercji test „statement_pdf nie idzie przez default” sprawdzałby wyłącznie nazwę aliasu.
    """
    problem = Problem.objects.create(
        stage=open_stage,
        number=7,
        title="Zadanie 7",
        statement_pdf=SimpleUploadedFile("z7.pdf", b"%PDF-1.4 tresc", content_type="application/pdf"),
    )

    path = problem.statement_pdf.path
    default_root = storages["default"].path("")

    assert storages[PRIVATE_MEDIA_ALIAS].exists(problem.statement_pdf.name)
    assert not storages["default"].exists(problem.statement_pdf.name)
    assert not path.startswith(default_root.rstrip("/\\") + "/problems")


def test_wagtail_documents_are_served_by_the_view_not_by_a_bucket_redirect(settings):
    assert settings.WAGTAILDOCS_SERVE_METHOD == "serve_view"


def test_restricted_document_is_hidden_from_anonymous_and_served_to_a_member(web_client, coordinator):
    """Ograniczenie widoczności kolekcji działa tylko dlatego, że plik idzie przez widok.

    Przy ``redirect`` (domyślne dla zdalnego storage) Wagtail oddałby 302 na adres bucketu.
    UWAGA: obiekt w ``public-media`` i tak jest anonimowo czytelny pod bezpośrednim URL-em –
    to ograniczenie chowa dokument, ale go nie utajnia (PROJEKT.md 1.4).
    """
    from wagtail.documents.models import Document
    from wagtail.models import Collection, CollectionViewRestriction

    root = Collection.get_first_root_node()
    collection = root.add_child(name="Materiały wewnętrzne")
    CollectionViewRestriction.objects.create(
        collection=collection, restriction_type=CollectionViewRestriction.LOGIN
    )
    document = Document.objects.create(
        title="Protokół komisji",
        collection=collection,
        file=SimpleUploadedFile("protokol.pdf", b"%PDF-1.4 protokol", content_type="application/pdf"),
    )
    url = f"/documents/{document.pk}/{document.filename}"

    anonymous = web_client.get(url)
    assert anonymous.status_code in (302, 403)

    web_client.login(email=coordinator.email, password=DEFAULT_PASSWORD)
    allowed = web_client.get(url)

    assert allowed.status_code == 200
    assert b"".join(allowed.streaming_content) == b"%PDF-1.4 protokol"


def test_embed_finders_accept_only_youtube_and_vimeo():
    finders = get_finders()

    assert len(finders) == 1
    assert finders[0].accept("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert finders[0].accept("https://vimeo.com/123456789")
    assert not finders[0].accept("https://twitter.com/ktos/status/1")
    assert not finders[0].accept("https://www.tiktok.com/@ktos/video/1")


def test_embed_from_an_unlisted_provider_is_rejected():
    """Adres spoza listy kończy się wyjątkiem już w edytorze – nie cichym pustym blokiem."""
    from wagtail.embeds.embeds import get_embed

    with pytest.raises(EmbedUnsupportedProviderException):
        get_embed("https://www.tiktok.com/@ktos/video/1")


@pytest.mark.parametrize("slug", ["login", "me", "results", "cms", "admin", "api", "healthz"])
def test_editor_cannot_create_a_page_that_shadows_an_application_url(slug):
    """Slug zarezerwowany jest odrzucany przez walidację modelu, a nie dopiero przez urlconf.

    Bez tego redaktor publikuje stronę ``/login/``, dostaje „opublikowano”, a czytelnik i tak
    widzi formularz logowania: ``config/urls.py`` dopasowuje aplikację przed drzewem stron.
    """
    with pytest.raises(ValidationError) as error:
        NewsIndexPage(title="Podszywacz", slug=slug).clean()

    assert "slug" in error.value.message_dict


def test_reserved_slug_is_allowed_deeper_in_the_tree():
    """Ograniczenie dotyczy wyłącznie drugiego poziomu: ``/aktualnosci/login/`` nikomu nie wadzi."""
    NewsPage(title="Jak się zalogować", slug="login").clean()


def test_statement_is_served_by_the_view_only_after_opens_at(web_client, open_stage):
    problem = Problem.objects.create(
        stage=open_stage,
        number=1,
        title="Zadanie 1",
        statement_pdf=SimpleUploadedFile("z1.pdf", b"%PDF-1.4 tresc", content_type="application/pdf"),
    )

    response = web_client.get(f"/api/competitions/problems/{problem.pk}/statement/")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/pdf"
    # Treść leci strumieniem z aplikacji; nie ma przekierowania na URL bucketu.
    assert b"".join(response.streaming_content) == b"%PDF-1.4 tresc"
