"""Ekran „Ustawienia konkursu” ``/coordinator/competition/``.

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran jest **wyłączony** w konkursie z domyślnymi przełącznikami – Olimpiada Kwantowa po
  wdrożeniu ma mieć menu i adresy dokładnie takie, jak przed nim (§ 0.5),
- pola operatora platformy (``slug``, witryna, tryb adresowania, prefiks) do formularza nie
  wchodzą, więc nie da się ich podstawić w POST (§ 8, D7),
- zapis zostawia ślad ``competition.updated`` z **nazwami** zmienionych pól, a zapis bez zmiany
  nie zostawia śladu w ogóle,
- przełącznik ``memberships_enforced`` da się z panelu wyłącznie **włączyć** (audyt W4): wyłączenie
  obok drugiego aktywnego konkursu dawałoby role z globalnych grup i awarię wdrożenia na
  ``tenancy.E001``; ekran mówi wprost, co przełącznik zmienia – i to zdanie jest tu asercją,
- listy obrazów nie pokazują kolekcji innych konkursów.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership
from apps.web.competition_forms import EDITABLE_FIELDS, FLAG_PREFIX
from apps.web.views.coordinator_competition import FEATURE

pytestmark = pytest.mark.django_db

URL = "/coordinator/competition/"


def enable(competition) -> None:
    """Włącza ekran – tak, jak zrobi to operator platformy przed wydaniem C."""
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])


@pytest.fixture
def coordinator_client(client_for, competition):
    """Zalogowany koordynator tego konkursu, pod jego domeną, z **włączonym** ekranem."""
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    enable(competition)
    client = client_for(competition)
    client.force_login(user)
    return client


def form_payload(competition, **overrides) -> dict:
    """Komplet pól formularza w stanie „bez zmian”, plus to, co test chce podmienić.

    Wysyłamy **wszystkie** pola, bo formularz jest jeden i niewysłane pole tekstowe znaczy dla
    Django „puste”, a nie „bez zmian”. Test podmieniający jedno pole ma zmieniać jedno pole.
    """
    payload = {}
    for name in EDITABLE_FIELDS:
        value = getattr(competition, name if name not in ("logo", "favicon") else f"{name}_id")
        payload[name] = "" if value is None else value
    for name in ("memberships_enforced", "supervisor_role", "appeals", "certificates", FEATURE):
        if competition.has_feature(name):
            payload[f"{FLAG_PREFIX}{name}"] = "on"
    payload.update(overrides)
    return payload


# --- przełącznik ekranu -------------------------------------------------------------------------


def test_screen_is_off_for_a_competition_with_default_flags(client_for, competition):
    """Konkurs #1 po wdrożeniu: adres nie istnieje, dopóki nikt świadomie go nie włączy."""
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)

    assert not competition.has_feature(FEATURE)
    assert client.get(URL).status_code == 404


def test_navigation_has_no_entry_while_the_screen_is_off(client_for, competition):
    """Menu panelu też ma zostać bez zmian: pozycja prowadząca do 404 jest gorsza niż jej brak."""
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)

    content = client.get("/coordinator/").content.decode()

    assert "Ustawienia konkursu" not in content


def test_navigation_shows_the_entry_once_enabled(coordinator_client):
    content = coordinator_client.get("/coordinator/").content.decode()

    assert "Ustawienia konkursu" in content
    assert URL in content


def test_wrong_role_gets_403_not_404(client_for, competition):
    """Zła rola to 403 **niezależnie od stanu flagi** – inaczej kod odpowiedzi zdradzałby flagę."""
    enable(competition)
    participant = ParticipantFactory(competition=competition)
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)
    client = client_for(competition)
    client.force_login(participant.user)

    assert client.get(URL).status_code == 403


def test_anonymous_is_redirected_to_login(client_for, competition):
    enable(competition)

    response = client_for(competition).get(URL)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


# --- zakres formularza --------------------------------------------------------------------------


def test_operator_fields_are_not_part_of_the_form(coordinator_client, competition):
    """``slug``, witryna, tryb adresowania i prefiks należą do operatora (§ 8, D7)."""
    content = coordinator_client.get(URL).content.decode()

    for name in ("slug", "site", "routing_mode", "path_prefix", "primary_domain"):
        assert f'name="{name}"' not in content


def test_operator_fields_cannot_be_smuggled_in_a_post(coordinator_client, competition):
    before = competition.slug

    coordinator_client.post(URL, form_payload(competition, slug="przejete", path_prefix="przejete"))

    competition.refresh_from_db()
    assert competition.slug == before
    assert competition.path_prefix == ""


def test_the_screen_shows_the_domain_read_only(coordinator_client, competition):
    """„Pod jakim adresem stoi mój konkurs” pada właśnie tutaj – odpowiedź ma być na ekranie."""
    content = coordinator_client.get(URL).content.decode()

    assert competition.primary_domain in content


# --- zapis i audyt ------------------------------------------------------------------------------


def test_saving_brand_fields_updates_the_competition(coordinator_client, competition):
    response = coordinator_client.post(
        URL,
        form_payload(
            competition,
            short_name="OK",
            genitive_name="Olimpiady Testowej",
            contact_phone="600 100 200",
        ),
    )

    assert response.status_code == 302
    competition.refresh_from_db()
    assert competition.short_name == "OK"
    assert competition.genitive == "Olimpiady Testowej"
    assert competition.contact_phone == "600 100 200"


def test_printed_identifier_prefixes_are_editable(coordinator_client, competition):
    """Prefiks kodu uczestnika i numeru dyplomu zmienia organizator, a nie wydanie aplikacji.

    Oba były stałymi modułu (``PUBLIC_CODE_PREFIX``, ``CERTIFICATE_NUMBER_PREFIX``), a są
    identyfikatorami **jednego** konkursu (§ 3.3): dwie olimpiady pod jednym prefiksem dawałyby
    w tabeli wyników kod bez informacji, czyj to wynik. Konkurs #1 zostaje przy „OLM-” i „OK”,
    więc żaden istniejący kod ani numer się nie zmienia – zmiana dotyczy wyłącznie nowych.
    """
    response = coordinator_client.post(
        URL, form_payload(competition, public_code_prefix="FIZ-", certificate_prefix="OF")
    )

    assert response.status_code == 302
    competition.refresh_from_db()
    assert (competition.public_code_prefix, competition.certificate_prefix) == ("FIZ-", "OF")


def test_the_prefix_help_text_says_existing_codes_do_not_change(coordinator_client):
    """Ekran ma to powiedzieć wprost: pytanie „czy przenumerujecie mi dyplomy” pada tutaj."""
    content = coordinator_client.get(URL).content.decode()

    assert "Kody już nadane się nie zmienią" in content
    assert "Numery już wystawionych" in content


def test_changing_a_prefix_is_recorded_in_the_audit_entry(coordinator_client, competition):
    coordinator_client.post(URL, form_payload(competition, certificate_prefix="OF"))

    entry = AuditLog.objects.filter(action="competition.updated").latest("at")
    assert entry.diff == {"fields": ["certificate_prefix"]}


def test_saving_leaves_an_audit_entry_with_field_names_only(coordinator_client, competition):
    coordinator_client.post(URL, form_payload(competition, short_name="OK"))

    entry = AuditLog.objects.filter(action="competition.updated").latest("at")
    assert entry.target_type == "tenancy.competition"
    assert entry.target_id == str(competition.pk)
    assert entry.diff == {"fields": ["short_name"]}


def test_saving_without_a_change_leaves_no_audit_entry(coordinator_client, competition):
    coordinator_client.post(URL, form_payload(competition))

    assert not AuditLog.objects.filter(action="competition.updated").exists()


def test_invalid_accent_colour_is_rejected_by_the_model_validator(coordinator_client, competition):
    response = coordinator_client.post(URL, form_payload(competition, accent_colour="granatowy"))

    assert response.status_code == 400
    competition.refresh_from_db()
    assert competition.accent_colour == ""


# --- przełączniki --------------------------------------------------------------------------------


def test_the_page_warns_what_memberships_enforced_does(coordinator_client):
    content = coordinator_client.get(URL).content.decode()

    assert "Role z członkostw w konkursie" in content
    assert "zmienia, kto ma dostęp do paneli" in content


def test_enabling_memberships_enforced_is_allowed_and_audited(
    coordinator_client, competition, other_competition
):
    """Włączenie jest drogą organizatora do domknięcia migracji członkostw – także przy dwóch konkursach."""
    competition.feature_flags = {**competition.feature_flags, "memberships_enforced": False}
    competition.save(update_fields=["feature_flags"])
    assert other_competition.is_active
    payload = form_payload(competition)
    payload[f"{FLAG_PREFIX}memberships_enforced"] = "on"

    coordinator_client.post(URL, payload)

    competition.refresh_from_db()
    assert competition.has_feature("memberships_enforced") is True
    entry = AuditLog.objects.filter(action="competition.updated").latest("at")
    assert entry.diff == {"fields": [f"{FLAG_PREFIX}memberships_enforced"]}


def test_disabling_memberships_enforced_from_the_panel_is_ignored(
    coordinator_client, competition, other_competition
):
    """Audyt W4: POST bez pola (albo z pustym) nie wyłącza flagi i nie zostawia wpisu o zmianie.

    Dwa aktywne konkursy to dokładnie stan, w którym wyłączenie otwierałoby panele tego konkursu
    koordynatorom wszystkich pozostałych, a następne ``migrate`` zatrzymywałoby się na
    ``tenancy.E001``.
    """
    competition.feature_flags = {**competition.feature_flags, "memberships_enforced": True}
    competition.save(update_fields=["feature_flags"])
    assert other_competition.is_active
    payload = form_payload(competition)
    payload.pop(f"{FLAG_PREFIX}memberships_enforced")

    coordinator_client.post(URL, payload)
    coordinator_client.post(URL, {**payload, f"{FLAG_PREFIX}memberships_enforced": ""})

    competition.refresh_from_db()
    assert competition.has_feature("memberships_enforced") is True
    assert not AuditLog.objects.filter(action="competition.updated").exists()


def test_an_enabled_memberships_switch_is_shown_locked_with_the_operator_note(
    coordinator_client, competition
):
    competition.feature_flags = {**competition.feature_flags, "memberships_enforced": True}
    competition.save(update_fields=["feature_flags"])

    response = coordinator_client.get(URL)

    field = response.context["form"].fields[f"{FLAG_PREFIX}memberships_enforced"]
    assert field.disabled is True
    assert "/admin/" in response.content.decode()


def test_saving_writes_every_flag_explicitly(coordinator_client, competition):
    """Po pierwszym zapisie stan konkursu jest **zapisany**, a nie dziedziczony z wartości domyślnych.

    Gdyby zostawał pusty słownik, zmiana domyślnej wartości w kodzie zmieniłaby kiedyś zachowanie
    konkursu bez decyzji organizatora.
    """
    coordinator_client.post(URL, form_payload(competition))

    competition.refresh_from_db()
    for name in ("memberships_enforced", "supervisor_role", "appeals", "certificates"):
        assert name in competition.feature_flags


def test_turning_the_screen_off_sends_the_coordinator_to_the_dashboard(coordinator_client, competition):
    payload = form_payload(competition)
    payload.pop(f"{FLAG_PREFIX}{FEATURE}")

    response = coordinator_client.post(URL, payload)

    assert response.status_code == 302
    assert response["Location"] == "/coordinator/"
    competition.refresh_from_db()
    assert competition.has_feature(FEATURE) is False
    assert coordinator_client.get(URL).status_code == 404


def test_routing_flag_stays_with_the_operator(coordinator_client, competition):
    """``path_prefix_routing`` nie jest na ekranie i zapis go nie kasuje."""
    competition.feature_flags = {**competition.feature_flags, "path_prefix_routing": True}
    competition.save(update_fields=["feature_flags"])

    coordinator_client.post(URL, form_payload(competition, short_name="OK"))

    competition.refresh_from_db()
    assert competition.has_feature("path_prefix_routing") is True


# --- izolacja ------------------------------------------------------------------------------------


def test_the_screen_always_edits_the_competition_of_the_request(client_for, competition, other_competition):
    """Nie ma adresu, pod którym dałoby się otworzyć cudzy konkurs – konkurs wskazuje domena."""
    enable(competition)
    enable(other_competition)
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)

    client.post(URL, form_payload(competition, short_name="Tylko A"))

    competition.refresh_from_db()
    other_competition.refresh_from_db()
    assert competition.short_name == "Tylko A"
    assert other_competition.short_name != "Tylko A"


# --- obrazy z biblioteki --------------------------------------------------------------------------


def _image_in(collection, title: str):
    from wagtail.images import get_image_model
    from wagtail.images.tests.utils import get_test_image_file

    return get_image_model().objects.create(title=title, file=get_test_image_file(), collection=collection)


def test_image_lists_hide_the_collections_of_other_competitions(
    coordinator_client, competition, other_competition
):
    """Konkurs bez zawężenia ``/cms/`` (Konkurs #1) widzi bibliotekę poza kolekcjami innych konkursów."""
    from wagtail.models import Collection

    from apps.cms.permissions import ensure_collection

    own = _image_in(Collection.get_first_root_node(), "Znak w korzeniu")
    foreign = _image_in(ensure_collection(other_competition), "Znak sąsiada")

    response = coordinator_client.get(URL)
    choices = set(response.context["form"].fields["logo"].queryset.values_list("pk", flat=True))
    assert own.pk in choices
    assert foreign.pk not in choices

    invalid = coordinator_client.post(URL, form_payload(competition, logo=foreign.pk))
    assert invalid.status_code == 400
    competition.refresh_from_db()
    assert competition.logo_id is None

    coordinator_client.post(URL, form_payload(competition, logo=own.pk))
    competition.refresh_from_db()
    assert competition.logo_id == own.pk


def test_a_scoped_competition_sees_only_its_own_collection(
    coordinator_client, competition, other_competition
):
    """Konkurs z zawężonym ``/cms/``: tylko jego kolekcja (tam trafia każdy plik wgrany pod jego adresem)."""
    from wagtail.models import Collection

    from apps.cms.permissions import ensure_collection

    competition.feature_flags = {**competition.feature_flags, "scoped_cms_permissions": True}
    competition.save(update_fields=["feature_flags"])
    mine = _image_in(ensure_collection(competition), "Nasz znak")
    root = _image_in(Collection.get_first_root_node(), "Znak w korzeniu")
    foreign = _image_in(ensure_collection(other_competition), "Znak sąsiada")

    choices = set(
        coordinator_client.get(URL).context["form"].fields["favicon"].queryset.values_list("pk", flat=True)
    )

    assert mine.pk in choices
    assert root.pk not in choices
    assert foreign.pk not in choices


def test_an_already_pinned_foreign_image_does_not_block_saving(
    coordinator_client, competition, other_competition
):
    """Obraz przypięty przed tą regułą zostaje na liście – zapis innego pola nie kończy się błędem."""
    from apps.cms.permissions import ensure_collection

    foreign = _image_in(ensure_collection(other_competition), "Znak sąsiada")
    type(competition).objects.filter(pk=competition.pk).update(logo=foreign)
    competition.refresh_from_db()

    response = coordinator_client.post(URL, form_payload(competition, short_name="Krótko"))

    assert response.status_code == 302
    competition.refresh_from_db()
    assert competition.short_name == "Krótko"
    assert competition.logo_id == foreign.pk
