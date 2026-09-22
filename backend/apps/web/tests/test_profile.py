"""Edycja własnych danych i zmiana adresu e-mail.

Dlaczego to jeden plik: adres e-mail jest **loginem**, więc jego zmiana nie jest kolejnym polem
profilu, tylko przeniesieniem konta – i dlatego jedzie osobnym przepływem z potwierdzeniem na nowej
skrzynce. Testy stoją obok siebie, żeby było widać różnicę: dane osobowe zapisują się od razu,
adres – dopiero po kliknięciu w link.

Czego pilnujemy w audycie: wpis mówi, **co** się zmieniło, ale nie **na co**, jeśli chodzi o dane
osobowe. Tak stanowi ``apps.core.models``: wpisy audytowe czytają też osoby bez prawa do danych
osobowych uczestnika.
"""

from datetime import date

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import Voivodeship
from apps.accounts.profile import request_email_change
from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog
from apps.schools.tests.factories import SchoolFactory

pytestmark = pytest.mark.django_db

PROFILE_URL = "/me/profile/"
ACCOUNT_PROFILE_URL = "/account/profile/"
EMAIL_URL = "/account/email/"


def profile_payload(**overrides) -> dict:
    data = {
        "first_name": "Anna",
        "last_name": "Nowakowska",
        "phone": "600 300 400",
        "district": Voivodeship.MALOPOLSKIE,
        "grade": "4",
        "birth_date": "2007-04-18",
        "school_custom": "on",
        "school": "LO nr 9",
    }
    data.update(overrides)
    return data


# --- profil uczestnika --------------------------------------------------------------------------


def test_profile_form_opens_with_the_current_data(web_client, participant):
    web_client.force_login(participant.user)

    response = web_client.get(PROFILE_URL)

    body = response.content.decode()
    assert participant.user.first_name in body
    assert participant.phone in body
    assert participant.school in body
    # Szkoła wpisana ręcznie (``school_ref`` puste) otwiera formularz od razu w trybie wolnego
    # tekstu – inaczej zmiana klasy wymagałaby szukania swojej szkoły od nowa.
    form = response.context["form"]
    assert form["school_custom"].value() is True
    assert form["school_query"].value() == participant.school


def test_saving_the_form_updates_the_profile_and_the_names(web_client, participant):
    web_client.force_login(participant.user)

    response = web_client.post(PROFILE_URL, profile_payload())

    assert response.status_code == 302
    participant.refresh_from_db()
    participant.user.refresh_from_db()
    assert participant.user.first_name == "Anna"
    assert participant.user.last_name == "Nowakowska"
    # Numer został sprowadzony do jednej postaci, tak samo jak przy rejestracji.
    assert participant.phone == "+48600300400"
    assert participant.district == Voivodeship.MALOPOLSKIE
    assert participant.grade == 4
    assert participant.birth_date == date(2007, 4, 18)
    # Rocznik jedzie **za** datą – liczy go ``Participant.save``, a nie formularz.
    assert participant.birth_year == 2007
    assert participant.school == "LO nr 9"


def test_the_audit_entry_names_the_changed_fields_without_the_values(web_client, participant):
    web_client.force_login(participant.user)

    web_client.post(PROFILE_URL, profile_payload())

    entry = AuditLog.objects.get(action="participant.profile_updated")
    # Dane osobowe: wyłącznie „zmieniło się”. Województwo, klasa i rocznik: wartości, bo same
    # nie identyfikują osoby, a bez nich wpis nie odpowiada na pytanie „co ustawił”.
    assert entry.diff["first_name"] is True
    assert entry.diff["phone"] is True
    assert entry.diff["district"] == {"from": "mazowieckie", "to": "malopolskie"}
    assert entry.diff["grade"] == {"from": 3, "to": 4}
    assert "Nowakowska" not in str(entry.diff)
    assert "600" not in str(entry.diff)


def test_saving_without_changes_leaves_an_empty_diff(web_client, participant):
    """Wpis zostaje (formularz zapisano), ale ``diff`` jest pusty – nic się nie zmieniło."""
    web_client.force_login(participant.user)
    current = profile_payload(
        first_name=participant.user.first_name,
        last_name=participant.user.last_name,
        phone=participant.phone,
        district=participant.district,
        grade=str(participant.grade),
        birth_date=participant.birth_date,
        school=participant.school,
    )

    web_client.post(PROFILE_URL, current)

    assert AuditLog.objects.get(action="participant.profile_updated").diff == {}


def test_the_school_can_be_switched_to_a_directory_row(web_client, participant):
    """Ta sama droga, co w rejestracji: wybór z wykazu przepisuje nazwę i dowiązuje wiersz."""
    school = SchoolFactory(name="V LICEUM OGÓLNOKSZTAŁCĄCE", city="Kraków")
    web_client.force_login(participant.user)

    web_client.post(PROFILE_URL, profile_payload(school_custom="", school="", school_id=school.pk))

    participant.refresh_from_db()
    assert participant.school_ref == school
    assert participant.school == "V LICEUM OGÓLNOKSZTAŁCĄCE"


def test_a_broken_phone_comes_back_as_a_form_error(web_client, participant):
    web_client.force_login(participant.user)

    response = web_client.post(PROFILE_URL, profile_payload(phone="600"))

    assert response.status_code == 200
    assert "Numer telefonu" in response.content.decode()
    participant.refresh_from_db()
    assert participant.phone == "+48600000000"


def test_the_profile_page_is_closed_to_other_roles(web_client, reviewer):
    """``/me/profile/`` edytuje profil **uczestnika** – konto komitetu takiego profilu nie ma."""
    web_client.force_login(reviewer.user)

    assert web_client.get(PROFILE_URL).status_code == 403


def test_the_dashboard_links_to_the_editor_and_to_the_deletion_page(web_client, participant):
    web_client.force_login(participant.user)

    body = web_client.get(reverse("web:me")).content.decode()

    assert f'href="{PROFILE_URL}"' in body
    assert 'href="/account/delete/"' in body


# --- profil konta bez profilu uczestnika --------------------------------------------------------


def test_committee_member_edits_only_the_names(web_client, reviewer):
    web_client.force_login(reviewer.user)

    response = web_client.post(ACCOUNT_PROFILE_URL, {"first_name": "Maria", "last_name": "Recenzentowa"})

    assert response.status_code == 302
    reviewer.user.refresh_from_db()
    assert reviewer.user.get_full_name() == "Maria Recenzentowa"
    assert AuditLog.objects.filter(action="account.profile_updated").exists()


def test_the_committee_district_is_read_only_on_that_page(web_client, reviewer):
    """Na województwie opiera się reguła konfliktu interesów – recenzent go sobie nie przestawia."""
    web_client.force_login(reviewer.user)

    body = web_client.get(ACCOUNT_PROFILE_URL).content.decode()

    assert 'name="district"' not in body
    assert "zmienia wyłącznie koordynator" in body


def test_a_participant_is_sent_to_the_full_editor(web_client, participant):
    web_client.force_login(participant.user)

    response = web_client.get(ACCOUNT_PROFILE_URL)

    assert response.status_code == 302
    assert response.headers["Location"] == PROFILE_URL


# --- zmiana adresu e-mail -----------------------------------------------------------------------


def link_from(message) -> str:
    """Adres potwierdzenia z listu.

    Schemat i host są opcjonalne: wysyłka z żądania buduje adres bezwzględny
    (``request.build_absolute_uri``), a wysyłka spoza żądania – ścieżkę. Test klika jedno i drugie
    tym samym klientem, więc regexp musi znieść oba kształty.
    """
    import re

    match = re.search(r"(?:https?://[^/\s]+)?(/account/email/confirm/\S+)", message.body)
    assert match, message.body
    return match.group(1)


def test_the_address_changes_only_after_the_link_is_clicked(
    web_client, participant, django_capture_on_commit_callbacks
):
    old_email = participant.user.email
    web_client.force_login(participant.user)

    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(EMAIL_URL, {"new_email": "nowy@example.test"})

    assert response.status_code == 302
    participant.user.refresh_from_db()
    # Do potwierdzenia obowiązuje stary adres: literówka nie może zamknąć drogi powrotu.
    assert participant.user.email == old_email

    confirmation = next(message for message in mail.outbox if message.to == ["nowy@example.test"])
    with django_capture_on_commit_callbacks(execute=True):
        confirmed = web_client.get(link_from(confirmation))

    assert confirmed.status_code == 200
    participant.user.refresh_from_db()
    assert participant.user.email == "nowy@example.test"
    assert AuditLog.objects.filter(action="account.email_changed").exists()

    # Stary adres dostaje powiadomienie – to jedyny sygnał dla właściciela skrzynki, który
    # o zmianę nie prosił.
    notice = next(message for message in mail.outbox if message.to == [old_email])
    assert "nowy@example.test" in notice.body


def test_an_address_already_taken_is_refused_case_insensitively(web_client, participant):
    UserFactory(email="zajety@example.test")
    web_client.force_login(participant.user)

    response = web_client.post(EMAIL_URL, {"new_email": "Zajety@Example.TEST"})

    assert response.status_code == 200
    assert "już istnieje" in response.content.decode()
    assert mail.outbox == []


def test_the_current_address_is_not_a_change(web_client, participant):
    web_client.force_login(participant.user)

    response = web_client.post(EMAIL_URL, {"new_email": participant.user.email.upper()})

    assert response.status_code == 200
    assert "To już jest adres tego konta." in response.content.decode()


def test_a_confirmation_link_goes_stale_after_a_second_change(
    web_client, participant, django_capture_on_commit_callbacks
):
    """Token niesie stary adres, więc link z poprzedniej próby nie wraca do adresu, którego nie ma."""
    with django_capture_on_commit_callbacks(execute=True):
        request_email_change(participant.user, new_email="pierwszy@example.test")
    stale = link_from(mail.outbox[-1])
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        request_email_change(participant.user, new_email="drugi@example.test")
    # Osobny blok: wywołania po commicie idą dopiero na wyjściu, więc listu nie ma jeszcze
    # w skrzynce, dopóki blok się nie zamknie.
    with django_capture_on_commit_callbacks(execute=True):
        web_client.get(link_from(mail.outbox[0]))

    participant.user.refresh_from_db()
    assert participant.user.email == "drugi@example.test"

    response = web_client.get(stale)

    assert response.status_code == 400
    participant.user.refresh_from_db()
    assert participant.user.email == "drugi@example.test"


def test_a_made_up_confirmation_link_changes_nothing(web_client, participant):
    old_email = participant.user.email

    response = web_client.get("/account/email/confirm/podrobiony-token/")

    assert response.status_code == 400
    participant.user.refresh_from_db()
    assert participant.user.email == old_email


def test_changing_the_address_requires_being_logged_in(web_client):
    response = web_client.post(EMAIL_URL, {"new_email": "obcy@example.test"})

    assert response.status_code == 302
    assert "/login/" in response.headers["Location"]


# --- API ----------------------------------------------------------------------------------------


def test_api_patch_me_updates_the_same_fields(participant):
    from rest_framework.test import APIClient

    api = APIClient()
    api.force_authenticate(user=participant.user)

    response = api.patch("/api/auth/me/", {"phone": "600 300 400", "grade": 5}, format="json")

    assert response.status_code == 200, response.data
    assert response.json()["participant"]["phone"] == "+48600300400"
    participant.refresh_from_db()
    assert participant.grade == 5
    assert AuditLog.objects.filter(action="participant.profile_updated").exists()


def test_api_patch_me_refuses_participant_fields_for_a_committee_account(reviewer):
    from rest_framework.test import APIClient

    api = APIClient()
    api.force_authenticate(user=reviewer.user)

    refused = api.patch("/api/auth/me/", {"grade": 2}, format="json")
    assert refused.status_code == 400
    assert refused.json()["code"] == "NOT_A_PARTICIPANT"

    accepted = api.patch("/api/auth/me/", {"first_name": "Maria"}, format="json")
    assert accepted.status_code == 200
    assert accepted.json()["first_name"] == "Maria"


def test_api_patch_me_needs_at_least_one_field(participant):
    from rest_framework.test import APIClient

    api = APIClient()
    api.force_authenticate(user=participant.user)

    assert api.patch("/api/auth/me/", {}, format="json").status_code == 400


def test_api_patch_me_requires_authentication():
    from rest_framework.test import APIClient

    assert APIClient().patch("/api/auth/me/", {"first_name": "X"}, format="json").status_code == 401


def test_the_profile_endpoint_exposes_the_phone(participant):
    from rest_framework.test import APIClient

    api = APIClient()
    api.force_authenticate(user=participant.user)

    body = api.get("/api/auth/me/").json()

    assert body["participant"]["phone"] == participant.phone
