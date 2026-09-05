"""Kryterium 8 z T-08 oraz nagłówek CSP: strony publiczne bez logowania."""

from datetime import UTC, datetime

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from apps.results.models import Anonymization, ResultsPublication

pytestmark = pytest.mark.django_db

SNAPSHOT = [
    {
        "rank": 1,
        "display": "OLM-AAAAAA",
        "district": "mazowieckie",
        "points": {"1": 6, "2": 5},
        "total": 11,
        "qualified": True,
    },
    {
        "rank": 2,
        "display": "OLM-BBBBBB",
        "district": "małopolskie",
        "points": {"1": 2, "2": 0},
        "total": 2,
        "qualified": False,
    },
]


@pytest.fixture
def publication(elim_stage):
    elim_stage.results_published_at = timezone.now()
    elim_stage.save(update_fields=["results_published_at"])
    return ResultsPublication.objects.create(
        stage=elim_stage, anonymization=Anonymization.CODE, snapshot=SNAPSHOT
    )


def test_public_results_render_snapshot_without_login(web_client, publication):
    response = web_client.get(f"/results/{publication.stage_id}/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "OLM-AAAAAA" in content
    assert "OLM-BBBBBB" in content
    assert ">11<" in content


def test_results_of_unpublished_stage_are_404(web_client, elim_stage):
    assert web_client.get(f"/results/{elim_stage.pk}/").status_code == 404


def test_csp_header_is_present_and_has_no_unsafe_inline_scripts(web_client, elim_stage):
    response = web_client.get("/")
    policy = response.headers["Content-Security-Policy"]

    assert "script-src" in policy
    script_src = next(part for part in policy.split("; ") if part.startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src
    assert "https://cdnjs.cloudflare.com" in script_src
    assert "https://cdn.jsdelivr.net" in script_src
    assert "'nonce-" in script_src
    # Style mają świadomy wyjątek – patrz apps/web/middleware.py.
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


def test_home_page_loads_pinned_cdn_scripts_with_sri(web_client, elim_stage):
    content = web_client.get("/").content.decode()

    assert "cdnjs.cloudflare.com/ajax/libs/htmx/2.0.4/htmx.min.js" in content
    assert "cdn.jsdelivr.net/npm/@alpinejs/csp@3.17.1/dist/cdn.min.js" in content
    assert content.count('integrity="sha384-') >= 2
    assert "<script>" not in content  # żadnego skryptu inline


def test_login_page_is_public(web_client):
    assert web_client.get("/login/").status_code == 200


def test_registration_creates_participant(web_client):
    response = web_client.post(
        "/register/",
        {
            "email": "nowy@example.test",
            "password": "Poprawne-Haslo-2026",
            "first_name": "Nowy",
            "last_name": "Uczestnik",
            "school": "LO nr 7",
            "district": "mazowieckie",
            "birth_year": 2008,
            "gdpr_consent": "on",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/login/"


def test_registration_without_gdpr_consent_shows_domain_error(web_client):
    response = web_client.post(
        "/register/",
        {
            "email": "brak@example.test",
            "password": "Poprawne-Haslo-2026",
            "first_name": "Brak",
            "last_name": "Zgody",
            "school": "LO nr 7",
            "district": "mazowieckie",
            "birth_year": 2008,
        },
    )

    assert response.status_code == 200
    assert "Zgoda na przetwarzanie danych osobowych jest wymagana." in response.content.decode()


def test_script_src_has_strict_dynamic_with_cdn_fallback(web_client, elim_stage):
    """``'strict-dynamic'`` (przegląd T-08, ustalenie 3).

    Nowa przeglądarka ufa wyłącznie nonce'owi i temu, co zaufany skrypt sam doładuje –
    to obejmuje dynamiczny ``import()`` pdf.js z modułu ``review-annotations.js``. Stara
    przeglądarka ignoruje nieznane słowo kluczowe i zostaje przy liście hostów, więc oba
    CDN-y muszą w polityce zostać.
    """
    policy = web_client.get("/").headers["Content-Security-Policy"]
    script_src = next(part for part in policy.split("; ") if part.startswith("script-src"))

    assert "'strict-dynamic'" in script_src
    assert "'nonce-" in script_src
    assert "https://cdnjs.cloudflare.com" in script_src
    assert "https://cdn.jsdelivr.net" in script_src
    # Fallback musi stać przed 'strict-dynamic' – inaczej kolejność myli stare parsery.
    assert script_src.index("cdnjs.cloudflare.com") < script_src.index("'strict-dynamic'")


def test_csp_middleware_sits_above_whitenoise(settings):
    """Kolejność middleware jest kontraktem: pliki statyczne też mają dostać nagłówek."""
    order = settings.MIDDLEWARE

    assert order[0] == "django.middleware.security.SecurityMiddleware"
    assert order[1] == "apps.web.middleware.ContentSecurityPolicyMiddleware"
    assert order.index("apps.web.middleware.ContentSecurityPolicyMiddleware") < order.index(
        "whitenoise.middleware.WhiteNoiseMiddleware"
    )


@override_settings(WHITENOISE_USE_FINDERS=True, WHITENOISE_AUTOREFRESH=True)
def test_static_file_served_by_whitenoise_gets_the_csp_header():
    """WhiteNoise odpowiada sam, bez wołania dalszych warstw – CSP musi być nad nim.

    Findery włączamy jawnie, bo testy nie robią ``collectstatic``; sam fakt, że odpowiedź
    ma status 200 i typ ``text/css``, dowodzi, że plik oddał WhiteNoise, a nie 404 z Wagtaila.
    """
    response = Client().get("/static/css/app.css")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/css")
    assert "script-src" in response.headers["Content-Security-Policy"]


def test_results_page_shows_local_time_not_utc(web_client, publication):
    """Etykiety czasu (przegląd T-08, ustalenie 4): czas polski, bez dopisku „(UTC)”."""
    published_at = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    ResultsPublication.objects.filter(pk=publication.pk).update(published_at=published_at)

    content = web_client.get(f"/results/{publication.stage_id}/").content.decode()

    # 10:00 UTC w lipcu to 12:00 w Europe/Warsaw.
    assert "15 lipca 2026, 12:00 (czas polski)" in content
    assert "(UTC)" not in content
