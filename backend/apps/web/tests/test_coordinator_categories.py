"""Ekran „Kategorie” ``/coordinator/categories/`` (etap 2, T27).

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran jest za flagą ``categories`` i bez niej daje **404**, a nie 403 (§ 2.1); uczestnik dostaje
  403 niezależnie od stanu flagi,
- kod jest unikalny **w konkursie**, a zakres klas odwrócony („od 5 do 2”) odpada pod polem,
- kategoria z wpisami nie da się usunąć i odmowa mówi o drodze wycofania (odznaczenie „aktywna”),
  a nie kończy się pięćsetką,
- przypisanie automatyczne **uzupełnia puste**: wpisu z kategorią wpisaną ręcznie nie rusza,
  wpisów spoza bieżącej edycji nie dotyka, a wpis bez pasującego zakresu zostaje bez kategorii,
- wpis audytowy przypisania niesie **kody i liczby**, a nie listę osób.

Adresy pochodzą z ``apps/web/urls.py`` – patrz ``test_coordinator_pipeline.py``.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.models import Category, StageKind
from apps.competitions.tests.factories import EditionFactory, StageEntryFactory, StageFactory
from apps.core.models import AuditLog
from apps.results.services import CATEGORIES_FLAG
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/categories/"


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), CATEGORIES_FLAG: True}
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


def payload(**overrides) -> dict:
    data = {
        "code": "podstawowa",
        "name": "Szkoła podstawowa",
        "position": 1,
        "grade_min": "",
        "grade_max": "",
        "is_active": "on",
    }
    data.update(overrides)
    return data


# --- przełącznik ekranu ---------------------------------------------------------------------------


def test_screen_is_off_for_a_competition_with_default_flags(client_for, competition):
    client = coordinator_for(client_for, competition)

    assert not competition.has_feature(CATEGORIES_FLAG)
    assert client.get(LIST_URL).status_code == 404
    assert client.post(f"{LIST_URL}assign/").status_code == 404


def test_participant_gets_403_regardless_of_the_flag(client_for, competition):
    user = ParticipantFactory().user
    client = client_for(competition)
    client.force_login(user)

    assert client.get(LIST_URL).status_code == 403

    enable(competition)

    assert client.get(LIST_URL).status_code == 403


# --- słownik --------------------------------------------------------------------------------------


def test_category_is_created_with_an_audit_entry(coordinator_client, competition):
    response = coordinator_client.post(f"{LIST_URL}new/", payload(grade_min=1, grade_max=8))

    category = Category.objects.get(competition=competition, code="podstawowa")
    assert response.status_code == 302
    assert (category.grade_min, category.grade_max) == (1, 8)
    assert AuditLog.objects.get(action="category.created").diff["code"] == "podstawowa"


def test_duplicate_code_is_refused_in_the_same_competition(coordinator_client, competition):
    Category.objects.create(competition=competition, code="podstawowa", name="Stara")

    response = coordinator_client.post(f"{LIST_URL}new/", payload())

    assert response.status_code == 400
    assert Category.objects.filter(competition=competition, code="podstawowa").count() == 1


def test_the_same_code_belongs_to_every_organizer_separately(
    coordinator_client, competition, other_competition
):
    Category.objects.create(competition=other_competition, code="podstawowa", name="Cudza")

    response = coordinator_client.post(f"{LIST_URL}new/", payload())

    assert response.status_code == 302
    assert Category.objects.filter(code="podstawowa").count() == 2


def test_reversed_grade_range_is_refused_under_the_field(coordinator_client, competition):
    response = coordinator_client.post(f"{LIST_URL}new/", payload(grade_min=5, grade_max=2))

    assert response.status_code == 400
    assert response.context["form"].errors["grade_max"]
    assert not Category.objects.filter(competition=competition).exists()


def test_category_can_be_edited(coordinator_client, competition):
    category = Category.objects.create(competition=competition, code="a", name="Stara nazwa")

    response = coordinator_client.post(
        f"{LIST_URL}{category.pk}/", payload(code="a", name="Nowa nazwa", position=2)
    )

    category.refresh_from_db()
    assert response.status_code == 302
    assert category.name == "Nowa nazwa"
    diff = AuditLog.objects.get(action="category.updated").diff
    assert diff["before"]["name"] == "Stara nazwa"


def test_category_without_entries_can_be_deleted(coordinator_client, competition):
    category = Category.objects.create(competition=competition, code="a", name="Do usunięcia")

    response = coordinator_client.post(f"{LIST_URL}{category.pk}/delete/")

    assert response.status_code == 302
    assert not Category.objects.filter(pk=category.pk).exists()
    assert AuditLog.objects.filter(action="category.deleted").exists()


def test_category_with_entries_is_protected_and_the_message_names_the_way_out(
    coordinator_client, competition, elim_stage
):
    category = Category.objects.create(competition=competition, code="a", name="W użyciu")
    StageEntryFactory(stage=elim_stage, category=category)

    response = coordinator_client.post(f"{LIST_URL}{category.pk}/delete/", follow=True)

    assert Category.objects.filter(pk=category.pk).exists()
    assert "Odznacz „aktywna”" in response.content.decode()


def test_a_category_of_another_competition_is_404(coordinator_client, other_competition):
    foreign = Category.objects.create(competition=other_competition, code="x", name="Cudza")

    assert coordinator_client.get(f"{LIST_URL}{foreign.pk}/").status_code == 404
    assert coordinator_client.post(f"{LIST_URL}{foreign.pk}/delete/").status_code == 404
    assert Category.objects.filter(pk=foreign.pk).exists()


# --- przypisanie automatyczne ---------------------------------------------------------------------


@pytest.fixture
def bands(competition):
    """Dwie kategorie z rozłącznymi zakresami klas – reguła, którą system policzy sam."""
    return (
        Category.objects.create(
            competition=competition, code="mlodsi", name="Młodsi", position=1, grade_min=1, grade_max=2
        ),
        Category.objects.create(
            competition=competition, code="starsi", name="Starsi", position=2, grade_min=3, grade_max=4
        ),
    )


def entry_with_grade(stage, grade, **kwargs):
    return StageEntryFactory(stage=stage, participant=ParticipantFactory(grade=grade), **kwargs)


def test_assignment_fills_empty_entries_from_the_grade(coordinator_client, elim_stage, bands):
    younger, older = bands
    first = entry_with_grade(elim_stage, 2)
    second = entry_with_grade(elim_stage, 4)

    response = coordinator_client.post(f"{LIST_URL}assign/")

    first.refresh_from_db()
    second.refresh_from_db()
    assert response.status_code == 302
    assert first.category == younger
    assert second.category == older


def test_assignment_does_not_overwrite_a_decision(coordinator_client, elim_stage, bands):
    younger, older = bands
    manual = entry_with_grade(elim_stage, 2, category=older)

    coordinator_client.post(f"{LIST_URL}assign/")

    manual.refresh_from_db()
    assert manual.category == older


def test_entry_without_a_matching_band_stays_without_a_category(coordinator_client, elim_stage, bands):
    lonely = entry_with_grade(elim_stage, 8)

    coordinator_client.post(f"{LIST_URL}assign/")

    lonely.refresh_from_db()
    assert lonely.category is None


def test_assignment_stops_at_the_current_edition(coordinator_client, competition, elim_stage, bands):
    old_edition = EditionFactory(competition=competition)
    old_stage = StageFactory(edition=old_edition, kind=StageKind.ELIM)
    old_entry = entry_with_grade(old_stage, 2)

    coordinator_client.post(f"{LIST_URL}assign/")

    old_entry.refresh_from_db()
    assert old_entry.category is None


def test_assignment_audit_carries_codes_and_counts_only(coordinator_client, elim_stage, bands):
    entry_with_grade(elim_stage, 2)
    entry_with_grade(elim_stage, 8)

    coordinator_client.post(f"{LIST_URL}assign/")

    diff = AuditLog.objects.get(action="category.auto_assigned").diff
    assert diff["assigned"] == 1
    assert diff["skipped"] == 1
    assert diff["by_code"] == {"mlodsi": 1}


def test_button_is_hidden_when_no_category_has_a_grade_rule(coordinator_client, competition):
    Category.objects.create(competition=competition, code="a", name="Bez zakresu")

    response = coordinator_client.get(LIST_URL)

    assert response.context["has_grade_rules"] is False
    assert "automat nie ma czego policzyć" in response.content.decode()
