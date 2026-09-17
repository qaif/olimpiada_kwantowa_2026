"""T3: domena zawodów należy do konkursu – edycja, etap, zadanie, wpis, termin, wydarzenie.

Trzy pytania, na które odpowiada ten plik:

1. **czyje to dane** – ``for_competition`` na każdym querysecie aplikacji (§ 3.4, § 3.5),
2. **co widzi pytający** – ``current_edition`` / ``current_registration_status`` oraz 404 na
   obiekcie cudzego konkursu w API (§ 3.6),
3. **co robi przebieg wsadowy** – zadanie okresowe obchodzi wszystkie konkursy, każdy w jego
   własnym kontekście (§ 6, T3).

Świat dwóch konkursów budują fikstury z ``backend/conftest.py`` (``competition``,
``other_competition``, ``as_competition``) i fabryki z argumentem ``competition``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.models import (
    REGISTRATION_DISABLED,
    Edition,
    EditionEvent,
    InterviewBooking,
    InterviewSlot,
    Problem,
    Stage,
    StageEntry,
    StageKind,
    current_registration_status,
)
from apps.competitions.scoping import each_competition, scope_to_competition
from apps.competitions.services import current_edition
from apps.competitions.tasks import remind_interviews
from apps.tenancy.context import current_competition

from .factories import (
    CurrentEditionFactory,
    EditionFactory,
    InterviewBookingFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from .scope_helpers import api_client_factory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_for(settings):
    """Klient DRF pod domeną wskazanego konkursu – reguła stoi w ``scope_helpers``."""
    return api_client_factory(settings)


# --- własność: klucz obcy i managery ------------------------------------------------------------


def test_edition_belongs_to_the_competition_of_the_context(competition):
    """Edycja zapisana bez wskazania właściciela dostaje konkurs „na teraz”.

    Domyślna wartość jest w modelu, bo edycje zakłada też kod spoza tego zadania (``/admin/``,
    seedy, import) – edycja bez właściciela byłaby niewidoczna dla ``current_edition()``, czyli
    zniknęłaby ze strony głównej.
    """
    edition = Edition.objects.create(year_label="Edycja bez wskazania")

    assert edition.competition_id == competition.pk


def test_edition_keeps_the_owner_given_explicitly(competition, other_competition, as_competition):
    """Wskazanie wprost wygrywa z kontekstem – inaczej import do konkursu B trafiałby do A."""
    with as_competition(competition):
        edition = Edition.objects.create(year_label="Edycja obcego", competition=other_competition)

    assert edition.competition_id == other_competition.pk


@pytest.mark.parametrize(
    ("model", "path"),
    [
        (Edition, "competition"),
        (Stage, "edition__competition"),
        (Problem, "stage__edition__competition"),
        (StageEntry, "stage__edition__competition"),
        (InterviewSlot, "stage__edition__competition"),
        (InterviewBooking, "entry__stage__edition__competition"),
        (EditionEvent, "edition__competition"),
    ],
)
def test_every_model_declares_its_way_to_the_competition(model, path):
    """Ścieżka jest **deklaracją modelu**, nie decyzją wołającego (§ 3.5).

    Test porównuje ją z tabelą dróg z dokumentu. Brak deklaracji albo cicha zmiana ścieżki to
    najgorszy z możliwych błędów w tej warstwie: zapytanie dalej działa, tylko filtruje po czymś
    innym, niż wszyscy myślą.
    """
    assert model.objects.all().competition_path == path


def test_stage_entry_and_booking_of_another_competition_are_invisible(competition, other_competition):
    """Komplet obiektów drugiego konkursu nie wychodzi z ``for_competition`` pierwszego."""
    booking_b = InterviewBookingFactory(competition=other_competition)
    entry_b = booking_b.entry
    slot_b = booking_b.slot

    assert list(StageEntry.objects.for_competition(competition)) == []
    assert list(InterviewSlot.objects.for_competition(competition)) == []
    assert list(InterviewBooking.objects.for_competition(competition)) == []
    assert list(StageEntry.objects.for_competition(other_competition)) == [entry_b]
    assert list(InterviewSlot.objects.for_competition(other_competition)) == [slot_b]


def test_without_a_competition_nothing_is_visible(other_competition):
    """``for_competition(None)`` to pustka: domyślnie zamknięte (§ 3.5).

    Odwrotna decyzja („brak konkursu = wszystko”) zamieniłaby każdy błąd rozstrzygania hosta
    w wyciek, i to wyciek cichy – odpowiedź wyglądałaby poprawnie.
    """
    StageFactory(competition=other_competition)

    assert list(Stage.objects.for_competition(None)) == []


def test_two_competitions_without_a_hint_see_nothing(other_competition, unbound_competition):  # noqa: ARG001
    """Instalacja wielokonkursowa bez wskazania konkursu zwraca pustkę, a nie „pierwszy z brzegu”.

    ``unbound_competition`` wyłącza autouse'owe związanie kontekstu – tylko tak da się sprawdzić
    zachowanie przy **pustym** kontekście, bo w kontekście ustawionym nie ma czego sprawdzać.
    """
    StageFactory(competition=other_competition)

    assert list(scope_to_competition(Stage.objects.all())) == []


# --- co widzi pytający: bieżąca edycja i rejestracja --------------------------------------------


def test_current_edition_follows_the_competition(competition, other_competition):
    """Bieżąca edycja konkursu B nie jest bieżącą edycją konkursu A.

    Edycja bieżąca jest tu jedna, bo więzu ``competitions_edition_single_current`` nie wolno
    ruszać przed wydaniem D (§ 4.1) – i właśnie dlatego ten kierunek jest mocniejszy: pokazuje,
    że odpowiedź bierze się z **konkursu**, a nie z „tej jedynej bieżącej w bazie”.
    """
    edition_b = CurrentEditionFactory(competition=other_competition)
    EditionFactory(competition=competition)

    assert current_edition(other_competition) == edition_b
    assert current_edition(competition) is None


def test_current_edition_without_an_argument_reads_the_context(
    competition, other_competition, as_competition
):
    """Bez argumentu obowiązuje konkurs kontekstu – tak samo w żądaniu, jak w zadaniu Celery."""
    edition_a = CurrentEditionFactory(competition=competition)

    with as_competition(competition):
        assert current_edition() == edition_a
    with as_competition(other_competition):
        assert current_edition() is None


def test_registration_status_of_a_competition_without_an_edition_is_disabled(competition, other_competition):
    """Otwarta rejestracja sąsiada nie jest otwartą rejestracją tutaj.

    Brak bieżącej edycji to ``disabled``, a nie „otwarta”: konto założone w takiej chwili nie
    miałoby do czego należeć.
    """
    CurrentEditionFactory(competition=competition, registration_enabled=True)

    assert current_registration_status(competition=competition).is_open is True
    status_b = current_registration_status(competition=other_competition)
    assert status_b.is_open is False
    assert status_b.reason == REGISTRATION_DISABLED


def test_entries_of_a_coordinator_are_scoped_to_his_competition(competition, other_competition):
    """Koordynator widzi wszystko **swojego** konkursu – to jest sedno zmiany w ``for_user``."""
    coordinator = CoordinatorFactory()
    entry_a = StageEntryFactory(competition=competition)
    entry_b = StageEntryFactory(competition=other_competition)

    visible = StageEntry.objects.for_user(coordinator, competition)

    assert list(visible) == [entry_a]
    assert entry_b not in visible


def test_participant_sees_only_his_entries_in_his_competition(competition, other_competition):
    """Uczestnik: zakres konkursu **i** własny profil. Kolejność (§ 3.5) nie zmienia wyniku tutaj,
    ale zmienia go dla koordynatora – dlatego oba przypadki mają własny test."""
    participant = ParticipantFactory(competition=competition)
    mine = StageEntryFactory(competition=competition, participant=participant)
    StageEntryFactory(competition=competition)
    StageEntryFactory(competition=other_competition)

    assert list(StageEntry.objects.for_user(participant.user, competition)) == [mine]
    assert list(StageEntry.objects.for_user(participant.user, other_competition)) == []


# --- reguła krzyżowa w API ----------------------------------------------------------------------


def test_current_edition_endpoint_answers_per_domain(api_for, competition, other_competition):
    """Ten sam adres pod dwiema domenami: raz edycja, raz 404. Nigdy cudzy harmonogram."""
    CurrentEditionFactory(competition=competition, year_label="Edycja konkursu A")

    ours = api_for(competition).get("/api/competitions/editions/current/")
    theirs = api_for(other_competition).get("/api/competitions/editions/current/")

    assert ours.status_code == 200
    assert ours.json()["year_label"] == "Edycja konkursu A"
    assert theirs.status_code == 404
    assert theirs.json()["code"] == "NO_CURRENT_EDITION"


def test_registering_to_a_stage_of_another_competition_is_not_found(api_for, competition, other_competition):
    """404, nie 403: istnienie etapu konkursu B nie jest informacją dla uczestnika konkursu A."""
    participant = ParticipantFactory(competition=competition)
    stage_b = StageFactory(competition=other_competition, kind=StageKind.ELIM, edition__is_current=True)

    response = api_for(competition, participant.user).post(f"/api/competitions/stages/{stage_b.pk}/register/")

    assert response.status_code == 404
    assert not StageEntry.objects.filter(stage=stage_b).exists()


def test_problem_statement_of_another_competition_is_not_found(api_for, competition, other_competition):
    """Treść zadania jest plikiem za adresem, którego nikt nie musi znać – zakres jest tu bramką.

    Koordynator konkursu A ma przy tym wyjątek na **własne** zadania przed otwarciem etapu i ten
    wyjątek zostaje bez zmian; zakres konkursu jest od niego niezależny.
    """
    coordinator = CoordinatorFactory()
    problem_b = ProblemFactory(competition=other_competition)

    response = api_for(competition, coordinator).get(f"/api/competitions/problems/{problem_b.pk}/statement/")

    assert response.status_code == 404


# --- przebieg wsadowy ---------------------------------------------------------------------------


def test_each_competition_binds_the_context(competition, other_competition):
    """Przebieg wsadowy wiąże konkurs na czas swojej iteracji, a po niej go zdejmuje.

    To nie jest kosmetyka pętli: adresy w listach powstają przez ``absolute_url``, które czyta
    konkurs z kontekstu. Bez wiązania uczestnik konkursu B dostałby link pod domenę konkursu A.
    """
    seen = [(visited, current_competition()) for visited in each_competition()]

    assert [pair[0] for pair in seen] == [pair[1] for pair in seen]
    assert {pair[0].pk for pair in seen} == {competition.pk, other_competition.pk}
    assert current_competition() == competition


def test_interview_reminders_go_out_per_competition(competition, other_competition, mailoutbox):
    """Zadanie obchodzi oba konkursy i wysyła po jednym liście, a nie jeden zbiorczy.

    Zapisy obu konkursów są w tym samym oknie czasowym, więc jedyne, co je rozdziela, to zakres.
    """
    soon = timezone.now() + timedelta(hours=2)
    for owner in (competition, other_competition):
        booking = InterviewBookingFactory(competition=owner)
        booking.slot.starts_at = soon
        booking.slot.ends_at = soon + timedelta(minutes=20)
        booking.slot.save(update_fields=["starts_at", "ends_at"])

    sent = remind_interviews()

    assert sent == 2
    assert InterviewBooking.objects.filter(reminder_sent_at__isnull=True).count() == 0
