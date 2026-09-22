"""Usunięcie własnego konta (art. 17 RODO): dwie drogi i dlaczego nie jedna.

Prawo do usunięcia danych nie jest prawem do usunięcia cudzej dokumentacji. Ogłoszona tabela
wyników i protokoły recenzji są dokumentem, na którym opierają się kwalifikacje **innych**
uczestników – kaskada ``User`` → ``Participant`` → ``StageEntry`` → ``Submission`` → ``Review``
wyrwałaby z niego kartę i przesunęła progi. Dlatego:

- konto ze śladem w zawodach przechodzi **anonimizację**: dane osobowe znikają, pseudonimowy
  wiersz (``public_code``) i dokumentacja zostają,
- konto bez ani jednego takiego odwołania znika w całości – nie ma czego chronić, a adres zwalnia
  się do ponownej rejestracji.

Trzecia rzecz, której pilnują te testy: **potwierdzenie tożsamości**. Bez niego każda niezamknięta
sesja w cudzej przeglądarce byłaby jednym kliknięciem od usunięcia danych.
"""

import pytest
from django.contrib.sessions.models import Session
from django.urls import reverse

from apps.accounts.models import ConsentRecord, Participant, User
from apps.accounts.tests.factories import DEFAULT_PASSWORD, ParticipantFactory, UserFactory
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.models import AuditLog
from apps.results.models import Anonymization, ResultsPublication

pytestmark = pytest.mark.django_db

DELETE_URL = "/account/delete/"


def confirm(password: str = DEFAULT_PASSWORD, **overrides) -> dict:
    data = {"password": password, "confirm": "on"}
    data.update(overrides)
    return data


# --- strona potwierdzenia -----------------------------------------------------------------------


def test_the_page_explains_the_anonymisation_for_a_participant_of_the_competition(
    web_client, participant, entry
):
    web_client.force_login(participant.user)

    body = web_client.get(DELETE_URL).content.decode()

    assert "anonimowy ślad udziału" in body
    assert participant.public_code in body


def test_the_page_explains_the_full_deletion_for_an_account_without_a_footprint(web_client, participant):
    web_client.force_login(participant.user)

    body = web_client.get(DELETE_URL).content.decode()

    assert "usunięte w całości" in body
    assert "zwolni się do ponownej rejestracji" in body


# --- potwierdzenie tożsamości -------------------------------------------------------------------


def test_a_wrong_password_changes_nothing(web_client, participant):
    web_client.force_login(participant.user)

    response = web_client.post(DELETE_URL, confirm(password="nie-to-haslo"))

    assert response.status_code == 200
    assert "Nieprawidłowe hasło." in response.content.decode()
    assert User.objects.filter(pk=participant.user.pk).exists()


def test_an_account_without_a_usable_password_confirms_with_its_address(web_client, participant):
    """Konto z Google/Facebooka nie ma hasła – potwierdzeniem jest przepisanie własnego adresu."""
    participant.user.set_unusable_password()
    participant.user.save(update_fields=["password"])
    web_client.force_login(participant.user)

    refused = web_client.post(DELETE_URL, {"email": "cos@innego.test", "confirm": "on"})
    assert refused.status_code == 200
    assert "Przepisz dokładnie adres" in refused.content.decode()
    assert User.objects.filter(pk=participant.user.pk).exists()

    accepted = web_client.post(DELETE_URL, {"email": participant.user.email, "confirm": "on"})
    assert accepted.status_code == 302
    assert not User.objects.filter(pk=participant.user.pk).exists()


def test_the_confirmation_checkbox_is_required(web_client, participant):
    web_client.force_login(participant.user)

    response = web_client.post(DELETE_URL, {"password": DEFAULT_PASSWORD})

    assert response.status_code == 200
    assert User.objects.filter(pk=participant.user.pk).exists()


def test_the_coordinator_cannot_delete_their_own_account_here(web_client, coordinator):
    """Konto organizatora jest jedynym wejściem do prowadzenia edycji – i chroni je ``PROTECT``."""
    web_client.force_login(coordinator)

    response = web_client.post(DELETE_URL, confirm())

    assert response.status_code == 200
    assert "skontaktuj się z administratorem" in response.content.decode()
    assert User.objects.filter(pk=coordinator.pk).exists()


def test_deleting_requires_being_logged_in(web_client):
    assert web_client.get(DELETE_URL).status_code == 302
    assert web_client.post(DELETE_URL, confirm()).status_code == 302


# --- droga bez śladu: wiersz znika --------------------------------------------------------------


def test_an_account_without_a_footprint_is_deleted_completely(web_client, participant):
    user_pk = participant.user.pk
    web_client.force_login(participant.user)

    response = web_client.post(DELETE_URL, confirm(), follow=True)

    assert "Konto zostało usunięte" in response.content.decode()
    assert not User.objects.filter(pk=user_pk).exists()
    assert not Participant.objects.filter(pk=participant.pk).exists()
    assert not ConsentRecord.objects.filter(participant_id=participant.pk).exists()
    entry = AuditLog.objects.get(action="account.deleted")
    # W ``diff`` wyłącznie identyfikator: wpis audytowy zostaje na stałe, także po skasowaniu konta.
    assert entry.diff == {"user_id": user_pk}


def test_the_address_is_free_again_after_a_full_deletion(web_client, participant):
    email = participant.user.email
    web_client.force_login(participant.user)

    web_client.post(DELETE_URL, confirm())

    assert not User.objects.filter(email=email).exists()
    assert UserFactory(email=email).pk


def test_deleting_closes_every_session_of_the_account(web_client, participant):
    """Ciasteczko z innej przeglądarki nie może dalej otwierać panelu."""
    from django.test import Client

    other_browser = Client()
    other_browser.force_login(participant.user)
    assert Session.objects.count() >= 1

    web_client.force_login(participant.user)
    web_client.post(DELETE_URL, confirm())

    assert other_browser.get(reverse("web:me")).status_code == 302
    assert Session.objects.count() == 0


# --- droga ze śladem: anonimizacja --------------------------------------------------------------


def test_a_participant_of_the_competition_is_anonymised_not_deleted(web_client, participant, entry):
    public_code = participant.public_code
    web_client.force_login(participant.user)

    response = web_client.post(DELETE_URL, confirm(), follow=True)

    assert "Konto zostało usunięte" in response.content.decode()
    user = User.objects.get(pk=participant.user.pk)
    assert user.email == f"deleted-{user.pk}@invalid.olimpiadakwantowa.pl"
    assert user.first_name == ""
    assert user.last_name == ""
    assert user.is_active is False
    assert user.has_usable_password() is False
    # Adres był kiedyś potwierdzony – wyzerowanie tego pola wstawiłoby konto na listę oczekujących
    # na aktywację i pod kosiarkę nieaktywowanych kont.
    assert user.email_verified_at is not None

    participant.refresh_from_db()
    assert participant.public_code == public_code
    assert participant.phone == ""
    assert participant.school == "—"
    assert participant.school_ref is None
    # Data urodzenia znika **cała**: dzień i miesiąc same w sobie zawężają krąg osób,
    # a rocznik idzie na wartość jawnie nieprawdziwą (kolumna jest ``NOT NULL``).
    assert participant.birth_date is None
    assert participant.birth_year == 1900
    assert participant.publish_full_name is False
    # Województwo zostaje: pole ma zamkniętą listę, a okręg sam nie identyfikuje osoby.
    assert participant.district
    assert AuditLog.objects.filter(action="account.anonymised").exists()


def test_the_competition_documentation_survives_the_anonymisation(web_client, participant, entry):
    web_client.force_login(participant.user)

    web_client.post(DELETE_URL, confirm())

    entry.refresh_from_db()
    assert entry.participant_id == participant.pk


def test_the_published_snapshot_is_left_untouched(web_client, participant, entry, elim_stage):
    """Ogłoszona tabela jest zamrożonym dokumentem – anonimizacja konta jej nie przepisuje."""
    rows = [{"public_code": participant.public_code, "total": 12, "school": "LO nr 1"}]
    publication = ResultsPublication.objects.create(
        stage=elim_stage, anonymization=Anonymization.CODE, snapshot=rows
    )
    web_client.force_login(participant.user)

    web_client.post(DELETE_URL, confirm())

    publication.refresh_from_db()
    assert publication.snapshot == rows


def test_the_consents_are_withdrawn_rather_than_erased(web_client, participant, entry):
    """Dowód, że zgoda kiedyś obowiązywała, zostaje – ale nie jest już podstawą przetwarzania."""
    from apps.accounts.consents import ConsentKind, ConsentSource

    record = ConsentRecord.objects.create(
        participant=participant, kind=ConsentKind.TERMS, source=ConsentSource.WEB
    )
    web_client.force_login(participant.user)

    web_client.post(DELETE_URL, confirm())

    record.refresh_from_db()
    assert record.withdrawn_at is not None


def test_a_reviewer_with_reviews_is_anonymised_too(web_client, reviewer):
    """``Review.reviewer`` jest na ``PROTECT`` – tego konta nie da się skasować, i tak ma być."""
    from apps.grading.tests.factories import ReviewFactory

    ReviewFactory(reviewer=reviewer)
    web_client.force_login(reviewer.user)

    response = web_client.post(DELETE_URL, confirm(), follow=True)

    assert "Konto zostało usunięte" in response.content.decode()
    user = User.objects.get(pk=reviewer.user.pk)
    assert user.email.endswith("@invalid.olimpiadakwantowa.pl")
    assert user.first_name == ""


def test_anonymisation_drops_the_provider_links_and_the_api_token(web_client, participant, entry):
    from allauth.socialaccount.models import SocialAccount
    from rest_framework.authtoken.models import Token

    SocialAccount.objects.create(user=participant.user, provider="google", uid="123")
    Token.objects.create(user=participant.user)
    web_client.force_login(participant.user)

    web_client.post(DELETE_URL, confirm())

    assert not SocialAccount.objects.filter(user=participant.user).exists()
    assert not Token.objects.filter(user=participant.user).exists()


def test_a_second_participant_is_unaffected(web_client, participant, entry):
    """Anonimizacja dotyczy jednego konta – sąsiedni wiersz nie może nawet drgnąć."""
    other = ParticipantFactory()
    web_client.force_login(participant.user)

    web_client.post(DELETE_URL, confirm())

    other.refresh_from_db()
    assert other.user.first_name == "Jan"
    assert other.school == "LO nr 1"


def test_a_participant_with_only_a_submission_is_also_anonymised(web_client, participant, entry):
    """Dowolny ślad wystarczy: praca bez recenzji też jest częścią dokumentacji etapu."""
    from apps.submissions.tests.factories import SubmissionFactory

    SubmissionFactory(entry=entry)
    web_client.force_login(participant.user)

    web_client.post(DELETE_URL, confirm())

    assert User.objects.filter(pk=participant.user.pk).exists()
    assert Participant.objects.filter(pk=participant.pk).exists()


def test_an_entry_on_its_own_is_enough_to_trigger_anonymisation(participant):
    """Sprawdzenie śladu liczy zgłoszenia, prace, recenzje i potwierdzenia opiekuna – każde z osobna."""
    from apps.accounts.profile import competition_footprint

    assert competition_footprint(participant.user) == {
        "entries": 0,
        "submissions": 0,
        "reviews": 0,
        "school_participations": 0,
    }

    StageEntryFactory(participant=participant)

    assert competition_footprint(participant.user)["entries"] == 1
