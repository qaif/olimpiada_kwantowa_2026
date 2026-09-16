"""Rozmowy kwalifikacyjne w interfejsie WWW: ekran koordynatora i zapisy uczestnika.

Reguły domenowe mają własne testy (``apps/competitions/tests/test_interviews.py``); tutaj
sprawdzamy drogę przez HTTP: co widać na ekranie, co robi POST, jakim kodem kończy się odmowa
i kto w ogóle ma prawo wejść.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import InterviewBooking, InterviewSlot, StageEntryStatus
from apps.competitions.tests.factories import InterviewSlotFactory, StageEntryFactory

from .conftest import PARTICIPANT_LAST_NAME

pytestmark = pytest.mark.django_db

WARSAW_FORMAT = "%Y-%m-%dT%H:%M"


def _tomorrow(hour: int = 9):
    return (timezone.now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)


def slots_payload(**overrides) -> dict:
    data = {
        "starts_at": timezone.localtime(_tomorrow()).strftime(WARSAW_FORMAT),
        "duration_minutes": 20,
        "count": 3,
        "capacity": 1,
        "meeting_url": "",
        "note": "komisja A",
    }
    data.update(overrides)
    return data


# --- ekran koordynatora ---------------------------------------------------------------------------


def test_coordinator_page_creates_a_series_of_slots(web_client, coordinator, interview_stage):
    web_client.force_login(coordinator)

    assert web_client.get(f"/coordinator/stages/{interview_stage.pk}/interviews/").status_code == 200

    response = web_client.post(f"/coordinator/stages/{interview_stage.pk}/interviews/", slots_payload())

    assert response.status_code == 302
    assert InterviewSlot.objects.filter(stage=interview_stage).count() == 3


def test_coordinator_page_lists_the_booked_participant(
    web_client, coordinator, interview_stage, interview_entry, participant
):
    """Jedyny ekran obok podglądu wyników, na którym wolno pokazać dane osobowe."""
    slot = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())
    InterviewBooking.objects.create(slot=slot, entry=interview_entry)
    web_client.force_login(coordinator)

    content = web_client.get(f"/coordinator/stages/{interview_stage.pk}/interviews/").content.decode()

    assert PARTICIPANT_LAST_NAME in content
    assert participant.user.email in content
    assert participant.public_code in content
    assert "dane osobowe" in content


def test_coordinator_page_of_a_written_stage_explains_instead_of_offering_the_form(
    web_client, coordinator, elim_stage
):
    web_client.force_login(coordinator)

    response = web_client.get(f"/coordinator/stages/{elim_stage.pk}/interviews/")
    content = response.content.decode()

    assert response.status_code == 200
    assert 'name="duration_minutes"' not in content
    assert f"/coordinator/stages/{elim_stage.pk}/edit/" in content


def test_slot_outside_the_stage_window_is_refused_with_400(web_client, coordinator, interview_stage):
    after = timezone.localtime(interview_stage.deadline_at) + timedelta(days=2)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{interview_stage.pk}/interviews/",
        slots_payload(starts_at=after.strftime(WARSAW_FORMAT)),
    )

    assert response.status_code == 400
    assert "oknie etapu" in response.content.decode()
    assert not InterviewSlot.objects.exists()


def test_deleting_a_booked_slot_renders_409(web_client, coordinator, interview_stage, interview_entry):
    slot = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())
    InterviewBooking.objects.create(slot=slot, entry=interview_entry)
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/interview-slots/{slot.pk}/delete/")

    assert response.status_code == 409
    assert InterviewSlot.objects.filter(pk=slot.pk).exists()
    assert "nie można go usunąć" in response.content.decode()


def test_dashboard_links_to_interviews_instead_of_problems(web_client, coordinator, interview_stage):
    InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert f'href="/coordinator/stages/{interview_stage.pk}/interviews/"' in content
    assert f'href="/coordinator/stages/{interview_stage.pk}/problems/"' not in content
    assert "Rozmowy (1)" in content
    assert "rozmowa kwalifikacyjna online" in content


# --- panel uczestnika -----------------------------------------------------------------------------


def test_participant_sees_slots_and_books_one(web_client, participant, interview_stage, interview_entry):
    slot = InterviewSlotFactory(
        stage=interview_stage, starts_at=_tomorrow(), meeting_url="https://meet.example/abc"
    )
    web_client.force_login(participant.user)

    listing = web_client.get("/me/").content.decode()
    assert "Wybierz termin rozmowy" in listing
    assert "Zapisz się" in listing
    # Link do rozmowy jest poświadczeniem – przed zapisem nie ma go na liście terminów.
    assert "https://meet.example/abc" not in listing

    response = web_client.post(f"/me/interview-slots/{slot.pk}/book/")

    assert response.status_code == 302
    assert InterviewBooking.objects.filter(slot=slot, entry=interview_entry).exists()
    assert "https://meet.example/abc" in web_client.get("/me/").content.decode()


def test_participant_can_move_and_cancel_the_booking(
    web_client, participant, interview_stage, interview_entry
):
    first = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow(9))
    second = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow(11))
    web_client.force_login(participant.user)
    web_client.post(f"/me/interview-slots/{first.pk}/book/")

    # Lista terminów jest listą wyboru z jednym przyciskiem, a nie tabelą z przyciskiem w każdym
    # wierszu – podpis mówi więc o „wybranym terminie”, a nie o „tym”.
    assert "Zmień na wybrany termin" in web_client.get("/me/").content.decode()
    web_client.post(f"/me/interview-slots/{second.pk}/book/")
    assert InterviewBooking.objects.get().slot_id == second.pk

    web_client.post(f"/me/stages/{interview_stage.pk}/interview/cancel/")

    assert not InterviewBooking.objects.exists()


def test_participant_without_an_entry_gets_an_explanation(web_client, participant, interview_stage):
    InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert "wyłącznie osoby zakwalifikowane" in content
    assert "Zapisz się" not in content
    # Etap w formie rozmowy nie ma uploadu ani zgłoszenia otwartego.
    assert "Zgłoś się do etapu eliminacyjnego" not in content


def test_slots_are_a_radio_list_grouped_by_day(web_client, participant, interview_stage, interview_entry):
    """Wybór terminu to jedna decyzja, więc jest jednym formularzem, a nie przyciskiem w wierszu."""
    morning = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow(9))
    afternoon = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow(15))
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert 'action="/me/interview/choose/"' in content
    assert f'name="slot_id" value="{morning.pk}"' in content
    assert f'name="slot_id" value="{afternoon.pk}"' in content
    # Oba terminy są tego samego dnia, więc grupa jest jedna – nagłówek dnia raz.
    assert content.count("<legend") == 1


def test_participant_books_the_slot_chosen_from_the_list(
    web_client, participant, interview_stage, interview_entry
):
    slot = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())
    web_client.force_login(participant.user)

    response = web_client.post("/me/interview/choose/", {"slot_id": str(slot.pk)})

    assert response.status_code == 302
    assert InterviewBooking.objects.filter(slot=slot, entry=interview_entry).exists()


def test_choosing_nothing_is_a_message_not_a_crash(web_client, participant, interview_stage, interview_entry):
    """Formularz wysłany bez zaznaczonego terminu (klawiatura, wyłączone skrypty) ma odpowiedzieć."""
    InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())
    web_client.force_login(participant.user)

    response = web_client.post("/me/interview/choose/", {}, follow=True)

    assert response.redirect_chain == [("/me/", 302)]
    assert "Wybierz termin rozmowy z listy" in response.content.decode()
    assert not InterviewBooking.objects.exists()


def test_stage_without_slots_shows_the_empty_message(
    web_client, participant, interview_stage, interview_entry
):
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert "Koordynator nie wyznaczył jeszcze terminów" in content


def test_booking_someone_elses_full_slot_is_refused(web_client, interview_stage, interview_entry):
    """Ostatnie miejsce zajęte – drugi uczestnik wraca z komunikatem, a nie z drugim zapisem.

    Sam licznik zapisów by tu nie wystarczył: gdyby serwis podmienił cudzy zapis na własny,
    zostałby dokładnie jeden – i test przechodziłby nad najgorszym możliwym błędem tego ekranu.
    Dlatego sprawdzamy, **czyj** jest ocalały zapis.
    """
    slot = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow(), capacity=1)
    InterviewBooking.objects.create(slot=slot, entry=interview_entry)
    latecomer = ParticipantFactory()
    StageEntryFactory(participant=latecomer, stage=interview_stage, status=StageEntryStatus.QUALIFIED)
    web_client.force_login(latecomer.user)

    response = web_client.post(f"/me/interview-slots/{slot.pk}/book/", follow=True)

    assert response.redirect_chain == [("/me/", 302)]
    assert "nie ma już wolnych miejsc" in response.content.decode()
    booking = InterviewBooking.objects.get()
    assert booking.entry_id == interview_entry.pk


def test_booking_a_slot_of_a_stage_without_an_entry_is_refused(
    web_client, participant, elim_stage, entry, interview_stage
):
    """Wpis w etapie A nie jest przepustką do rozmów etapu B.

    Uczestnik zapisany do eliminacji trafia na adres terminu z etapu okręgowego (link z czatu,
    zgadnięte id). Odmowa jest odmową domeny (``NOT_QUALIFIED``), więc widok-akcja zamienia ją na
    komunikat i powrót na pulpit – ale zapis nie powstaje.
    """
    slot = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())
    web_client.force_login(participant.user)

    response = web_client.post(f"/me/interview-slots/{slot.pk}/book/", follow=True)

    assert response.redirect_chain == [("/me/", 302)]
    # Dosłowna treść ``NOT_QUALIFIED``, a nie jej fragment: podobne zdanie stoi też w szablonie
    # przy etapie rozmowy bez wpisu, więc luźniejsza asercja przechodziłaby bez komunikatu błędu.
    assert (
        "Do rozmowy przystępują wyłącznie osoby zakwalifikowane w poprzednim etapie."
        in response.content.decode()
    )
    assert not InterviewBooking.objects.exists()


# --- uprawnienia ------------------------------------------------------------------------------------


def test_anonymous_is_redirected_to_login(web_client, interview_stage):
    slot = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())

    listing = web_client.get(f"/coordinator/stages/{interview_stage.pk}/interviews/")
    booking = web_client.post(f"/me/interview-slots/{slot.pk}/book/")

    assert listing.status_code == 302
    assert listing["Location"].startswith("/login/")
    assert booking.status_code == 302
    assert booking["Location"].startswith("/login/")


@pytest.mark.parametrize("role", ["participant", "reviewer"])
def test_other_roles_cannot_manage_slots(web_client, participant, reviewer, interview_stage, role):
    slot = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())
    user = participant.user if role == "participant" else reviewer.user
    web_client.force_login(user)

    assert web_client.get(f"/coordinator/stages/{interview_stage.pk}/interviews/").status_code == 403
    assert web_client.post(f"/coordinator/interview-slots/{slot.pk}/delete/").status_code == 403


def test_reviewer_cannot_book_a_slot(web_client, reviewer, interview_stage):
    slot = InterviewSlotFactory(stage=interview_stage, starts_at=_tomorrow())
    web_client.force_login(reviewer.user)

    assert web_client.post(f"/me/interview-slots/{slot.pk}/book/").status_code == 403
