"""Ekran „Regiony” ``/coordinator/regions/`` (etap 2, T20).

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran jest za flagą ``custom_regions`` i bez niej daje **404**, a nie 403 (§ 2.1); uczestnik
  dostaje 403 niezależnie od stanu flagi,
- drzewo jest drzewem: kraj stoi wyżej niż województwa, a wcięcie liczy widok, nie szablon,
- region, którym ktoś startował, **nie da się usunąć** – odmowa mówi, ilu ludzi to dotyczy,
  i wskazuje drogę wycofania (wyłączenie), zamiast kończyć się pięćsetką,
- „przywróć domyślne województwa” uzupełnia braki i **nie nadpisuje** nazw poprawionych ręcznie,
  a powtórzone kliknięcie nie mnoży wierszy,
- panel „kto jest gdzie” liczy uczestników bieżącej edycji i komitet konkursu, a profile bez
  regionu pokazuje osobnym wierszem, zamiast je gubić,
- podgląd konfliktu interesów grupuje wpisy po **tej samej** wartości, którą przydziałowi podaje
  ``participant_conflict_region``, i milczy na etapie, którego reguła nie dotyczy,
- koszt ekranu nie rośnie z liczbą regionów ani uczestników.

Adresy są w ``apps/web/urls.py``: montaż wydania G rozwinął tam ``urls_regions.urlpatterns``,
więc testy chodzą po mapie produkcyjnej. Ścieżki się nie zmieniły.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import CompetitionRole, Region, RegionLevel
from apps.accounts.regions import STARTING_SET_SIZE
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
)
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.models import AuditLog
from apps.grading.services import participant_conflict_region
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/regions/"
FEATURE = "custom_regions"


def enable(competition):
    """Włącza flagę obszaru tą samą drogą, którą zrobi to wdrożenie: wartością w bazie."""
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def coordinator_for(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def coordinator_client(client_for, competition):
    enable(competition)
    return coordinator_for(client_for, competition)


def region(competition, code: str) -> Region:
    """Region konkursu o tym kodzie – zestaw startowy wpisuje migracja ``accounts.0026``."""
    return Region.objects.for_competition(competition).get(code=code)


def payload(**overrides) -> dict:
    data = {
        "code": "okreg-polnocny",
        "name": "Okręg północny",
        "level": RegionLevel.REGION,
        "parent": "",
        "position": 5,
        "is_active": "on",
        "counts_for_conflict": "on",
    }
    data.update(overrides)
    return data


# --- przełącznik ekranu ---------------------------------------------------------------------------


def test_screen_is_off_for_a_competition_with_default_flags(client_for, competition):
    client = coordinator_for(client_for, competition)
    assert not competition.has_feature(FEATURE)

    assert client.get(LIST_URL).status_code == 404
    assert client.post(f"{LIST_URL}new/", payload()).status_code == 404
    assert client.post(f"{LIST_URL}defaults/").status_code == 404


def test_participant_gets_403_regardless_of_the_flag(client_for, competition):
    client = client_for(competition)
    client.force_login(ParticipantFactory().user)

    assert client.get(LIST_URL).status_code == 403

    enable(competition)

    assert client.get(LIST_URL).status_code == 403


# --- drzewo ---------------------------------------------------------------------------------------


def test_the_tree_puts_the_voivodeships_under_the_country(coordinator_client, competition):
    response = coordinator_client.get(LIST_URL)

    rows = {row["region"].code: row for row in response.context["rows"]}
    assert response.status_code == 200
    assert len(rows) == STARTING_SET_SIZE
    assert rows["pl"]["depth"] == 0
    assert rows["mazowieckie"]["depth"] == 1
    assert rows["mazowieckie"]["indent"]
    assert rows["poza-polska"]["depth"] == 0


def test_a_region_of_another_competition_is_404(coordinator_client, other_competition):
    # Konkurs założony w teście nie przeszedł przez migrację zestawu startowego (``accounts.0026``
    # objęła konkursy stojące w bazie w chwili jej wykonania), więc region wpisujemy wprost.
    foreign = Region.objects.create(competition=other_competition, code="mazowieckie", name="Cudze")

    assert coordinator_client.get(f"{LIST_URL}{foreign.pk}/").status_code == 404
    assert coordinator_client.post(f"{LIST_URL}{foreign.pk}/delete/").status_code == 404
    assert Region.objects.filter(pk=foreign.pk).exists()


# --- słownik --------------------------------------------------------------------------------------


def test_region_is_created_with_an_audit_entry(coordinator_client, competition):
    country = region(competition, "pl")

    response = coordinator_client.post(f"{LIST_URL}new/", payload(parent=country.pk))

    created = region(competition, "okreg-polnocny")
    assert response.status_code == 302
    assert (created.name, created.parent_id, created.position) == ("Okręg północny", country.pk, 5)
    diff = AuditLog.objects.get(action="region.created").diff
    assert diff["code"] == "okreg-polnocny"
    assert diff["parent"] == "pl"


def test_duplicate_code_is_refused_under_the_field(coordinator_client, competition):
    response = coordinator_client.post(f"{LIST_URL}new/", payload(code="mazowieckie"))

    assert response.status_code == 400
    assert response.context["form"].errors["code"]
    assert Region.objects.for_competition(competition).filter(code="mazowieckie").count() == 1


def test_the_same_code_belongs_to_every_organizer_separately(
    coordinator_client, competition, other_competition
):
    response = coordinator_client.post(f"{LIST_URL}new/", payload(code="okreg-polnocny"))

    assert response.status_code == 302
    Region.objects.create(competition=other_competition, code="okreg-polnocny", name="Cudzy")
    assert Region.objects.filter(code="okreg-polnocny").count() == 2


def test_the_parent_choices_skip_the_region_and_its_children(coordinator_client, competition):
    country = region(competition, "pl")

    response = coordinator_client.get(f"{LIST_URL}{country.pk}/")

    codes = {row.code for row in response.context["form"].fields["parent"].queryset}
    assert response.status_code == 200
    assert "pl" not in codes
    assert "mazowieckie" not in codes
    assert "poza-polska" in codes


def test_region_can_be_edited(coordinator_client, competition):
    row = region(competition, "mazowieckie")

    response = coordinator_client.post(
        f"{LIST_URL}{row.pk}/",
        payload(code="mazowieckie", name="Mazowsze", parent=row.parent_id, position=1),
    )

    row.refresh_from_db()
    assert response.status_code == 302
    assert row.name == "Mazowsze"
    diff = AuditLog.objects.get(action="region.updated").diff
    assert diff["before"]["name"] == "mazowieckie"
    assert diff["after"]["name"] == "Mazowsze"


# --- kolejność ------------------------------------------------------------------------------------


def test_move_swaps_two_siblings(coordinator_client, competition):
    first = region(competition, "dolnoslaskie")
    second = region(competition, "kujawsko-pomorskie")
    assert first.position < second.position

    response = coordinator_client.post(f"{LIST_URL}{second.pk}/move/up/")

    first.refresh_from_db()
    second.refresh_from_db()
    assert response.status_code == 302
    assert second.position < first.position
    assert AuditLog.objects.filter(action="region.updated").exists()


def test_move_at_the_edge_changes_nothing(coordinator_client, competition):
    first = region(competition, "dolnoslaskie")
    before = first.position

    response = coordinator_client.post(f"{LIST_URL}{first.pk}/move/up/", follow=True)

    first.refresh_from_db()
    assert first.position == before
    assert "na skraju" in response.content.decode()


def test_an_unknown_direction_is_404(coordinator_client, competition):
    row = region(competition, "mazowieckie")

    assert coordinator_client.post(f"{LIST_URL}{row.pk}/move/sideways/").status_code == 404


# --- wycofanie ------------------------------------------------------------------------------------


def test_deactivation_takes_the_region_off_the_choices(coordinator_client, competition):
    row = region(competition, "mazowieckie")

    response = coordinator_client.post(f"{LIST_URL}{row.pk}/deactivate/")

    row.refresh_from_db()
    assert response.status_code == 302
    assert row.is_active is False
    assert row not in list(Region.objects.for_competition(competition).active())
    assert AuditLog.objects.get(action="region.deactivated").diff == {"code": "mazowieckie"}


def test_activation_brings_the_region_back(coordinator_client, competition):
    row = region(competition, "poza-polska")
    assert row.is_active is False

    response = coordinator_client.post(f"{LIST_URL}{row.pk}/activate/")

    row.refresh_from_db()
    assert response.status_code == 302
    assert row.is_active is True


def test_an_unused_region_is_deleted(coordinator_client, competition):
    row = region(competition, "poza-polska")

    response = coordinator_client.post(f"{LIST_URL}{row.pk}/delete/")

    assert response.status_code == 302
    assert not Region.objects.filter(pk=row.pk).exists()
    assert AuditLog.objects.get(action="region.deleted").diff["code"] == "poza-polska"


def test_a_region_in_use_is_protected_and_the_message_counts_the_people(coordinator_client, competition):
    row = region(competition, "mazowieckie")
    ParticipantFactory(region=row)
    ActiveReviewerFactory(region=row)

    response = coordinator_client.post(f"{LIST_URL}{row.pk}/delete/", follow=True)

    page = response.content.decode()
    assert Region.objects.filter(pk=row.pk).exists()
    assert "uczestników 1" in page
    assert "członków komitetu 1" in page
    assert "wyłącz go" in page


# --- zestaw startowy ------------------------------------------------------------------------------


def test_defaults_fill_the_gaps_without_overwriting_the_names(coordinator_client, competition):
    region(competition, "poza-polska").delete()
    renamed = region(competition, "mazowieckie")
    renamed.name = "Mazowsze"
    renamed.save(update_fields=["name"])

    response = coordinator_client.post(f"{LIST_URL}defaults/")

    renamed.refresh_from_db()
    assert response.status_code == 302
    assert Region.objects.for_competition(competition).count() == STARTING_SET_SIZE
    assert renamed.name == "Mazowsze"
    assert AuditLog.objects.get(action="region.created").diff["codes"] == ["poza-polska"]


def test_defaults_are_idempotent(coordinator_client, competition):
    response = coordinator_client.post(f"{LIST_URL}defaults/", follow=True)

    assert Region.objects.for_competition(competition).count() == STARTING_SET_SIZE
    assert "już jest w całości" in response.content.decode()
    assert not AuditLog.objects.filter(action="region.created").exists()


# --- kto jest gdzie -------------------------------------------------------------------------------


def test_the_panel_counts_people_of_the_current_edition(coordinator_client, competition, elim_stage):
    mazowieckie = region(competition, "mazowieckie")
    StageEntryFactory(participant=ParticipantFactory(region=mazowieckie), stage=elim_stage)
    StageEntryFactory(participant=ParticipantFactory(region=None), stage=elim_stage)
    ActiveReviewerFactory(region=mazowieckie)

    response = coordinator_client.get(LIST_URL)

    counts = {row["region"].code: row for row in response.context["counts"]}
    assert counts["mazowieckie"]["participants"] == 1
    assert counts["mazowieckie"]["members"] == 1
    assert counts["pomorskie"]["participants"] == 0
    assert response.context["counts_unassigned"]["participants"] == 1


# --- podgląd konfliktu interesów ------------------------------------------------------------------


def test_the_preview_is_silent_for_a_stage_the_rule_does_not_cover(
    coordinator_client, competition, elim_stage
):
    response = coordinator_client.get(f"{LIST_URL}?stage={elim_stage.pk}")

    assert response.context["preview"]["applies"] is False
    assert "nie jest etapem wojewódzkim" in response.content.decode()


def test_the_preview_groups_by_the_same_region_the_rule_reads(
    coordinator_client, competition, interview_stage
):
    mazowieckie = region(competition, "mazowieckie")
    participant = ParticipantFactory(region=mazowieckie)
    StageEntryFactory(participant=participant, stage=interview_stage)
    ActiveReviewerFactory(region=mazowieckie)
    ActiveReviewerFactory(region=region(competition, "pomorskie"))

    response = coordinator_client.get(f"{LIST_URL}?stage={interview_stage.pk}")

    preview = response.context["preview"]
    rows = {row["region"].code: row for row in preview["rows"]}
    # Klucz grupowania jest tą samą wartością, którą przydziałowi podaje reguła konfliktu.
    assert participant_conflict_region(interview_stage, participant) == mazowieckie
    assert preview["applies"] is True
    assert rows["mazowieckie"]["participants"] == 1
    assert rows["mazowieckie"]["members"] == 1
    # Dwóch wierszy komitetu, jeden odpada dla prac z Mazowsza.
    assert preview["reviewers"] == 2
    assert rows["mazowieckie"]["available"] == 1
    assert rows["pomorskie"]["available"] == 1


def test_a_region_outside_the_conflict_takes_nobody_away(coordinator_client, competition, interview_stage):
    abroad = region(competition, "poza-polska")
    StageEntryFactory(participant=ParticipantFactory(region=abroad), stage=interview_stage)
    ActiveReviewerFactory(region=abroad)

    response = coordinator_client.get(f"{LIST_URL}?stage={interview_stage.pk}")

    preview = response.context["preview"]
    rows = {row["region"].code: row for row in preview["rows"]}
    assert rows["poza-polska"]["available"] == preview["reviewers"]


def test_a_stage_of_another_edition_is_not_previewed(coordinator_client, competition):
    assert coordinator_client.get(f"{LIST_URL}?stage=999999").context["preview"] is None


# --- koszt ----------------------------------------------------------------------------------------


def test_the_screen_costs_the_same_with_more_regions_and_people(coordinator_client, competition, elim_stage):
    """Liczniki idą zapytaniami grupującymi, więc koszt nie zależy od liczby wierszy."""
    mazowieckie = region(competition, "mazowieckie")
    StageEntryFactory(participant=ParticipantFactory(region=mazowieckie), stage=elim_stage)
    # Rozgrzewka: liczniki menu mają minutową pamięć podręczną, więc pierwsze wejście na
    # **którykolwiek** ekran panelu kosztuje o pięć zapytań agregujących więcej niż następne.
    coordinator_client.get(LIST_URL)

    with CaptureQueriesContext(connection) as small:
        assert coordinator_client.get(LIST_URL).status_code == 200

    for index in range(5):
        Region.objects.create(
            competition=competition, code=f"okreg-{index}", name=f"Okręg {index}", position=index
        )
        StageEntryFactory(participant=ParticipantFactory(region=mazowieckie), stage=elim_stage)
        ActiveReviewerFactory(region=mazowieckie)

    with CaptureQueriesContext(connection) as large:
        assert coordinator_client.get(LIST_URL).status_code == 200

    assert len(large) == len(small)
