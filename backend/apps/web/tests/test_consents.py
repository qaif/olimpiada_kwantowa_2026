"""Zgody w interfejsie WWW: oba formularze rejestracji i przełącznik w panelu uczestnika.

Reguły domenowe (co jest wymagane, od kogo, pod jaką wersją dokumentu) mają własne testy
w ``apps/accounts/tests/test_consents.py``. Tutaj sprawdzamy to, czego tamte nie widzą:

- czy uczestnik **widzi**, na co się zgadza – etykieta z odnośnikiem do dokumentu, a nie sam
  napis „Zgoda RODO”,
- czy blokada zgody opiekuna działa na obu drogach rejestracji i staje **pod polem**,
- czy w panelu widać historię zgód i czy jedyną odwracalną z nich da się faktycznie odwrócić.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.consents import MINOR_MAX_AGE, ConsentKind, ConsentSource
from apps.accounts.models import ConsentRecord, Participant, User
from apps.accounts.services import set_publish_name_consent
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"
SIGNUP_URL = "/rejestracja/dokoncz/"
TOGGLE_URL = "/me/consents/publish-name/"


def minor_year() -> int:
    return timezone.localdate().year - MINOR_MAX_AGE


def adult_year() -> int:
    return timezone.localdate().year - MINOR_MAX_AGE - 1


def register_payload(**overrides) -> dict:
    data = {
        "email": "zgody-web@example.test",
        "password": "Poprawne-Haslo-2026",
        "first_name": "Anna",
        "last_name": "Nowak",
        "school_custom": "on",
        "school": "LO nr 7",
        "district": "mazowieckie",
        "grade": 2,
        "birth_year": adult_year(),
        "terms_consent": "on",
        "gdpr_consent": "on",
    }
    data.update(overrides)
    return data


# --- formularz rejestracji hasłem -------------------------------------------------------------


def test_register_form_shows_every_consent_with_a_link_to_its_document(web_client, edition):
    body = web_client.get(REGISTER_URL).content.decode()

    assert 'class="consents"' in body
    assert "<legend>Zgody</legend>" in body
    for slug in ("regulamin", "rodo", "zgoda-opiekuna"):
        assert f'href="/dokumenty/{slug}/"' in body
    assert 'rel="noopener"' in body
    assert "Regulaminem Olimpiady Kwantowej" in body
    assert "Polityką RODO (klauzulą informacyjną)" in body
    assert "wymagane dla osób niepełnoletnich" in body
    assert 'name="publish_name_consent"' in body


def test_register_form_blocks_a_minor_without_the_guardian_consent(web_client, edition):
    response = web_client.post(REGISTER_URL, register_payload(birth_year=minor_year()))

    assert response.status_code == 200
    form = response.context["form"]
    # Błąd stoi **pod polem** zgody opiekuna, a nie nad formularzem: wynika z rocznika
    # wpisanego obok, więc bez wskazania palcem nie wiadomo, co poprawić.
    assert "guardian_consent" in form.errors
    assert not User.objects.exists()


def test_register_form_accepts_a_minor_with_the_guardian_consent(web_client, edition):
    response = web_client.post(REGISTER_URL, register_payload(birth_year=minor_year(), guardian_consent="on"))

    assert response.status_code == 302
    participant = Participant.objects.get()
    assert participant.guardian_consent is True
    assert set(participant.consents.values_list("kind", flat=True)) == {
        ConsentKind.TERMS,
        ConsentKind.PRIVACY,
        ConsentKind.GUARDIAN,
    }
    assert participant.consents.first().source == ConsentSource.WEB


def test_register_form_records_the_optional_publish_name_consent(web_client, edition):
    web_client.post(REGISTER_URL, register_payload(publish_name_consent="on"))

    participant = Participant.objects.get()
    assert participant.publish_full_name is True
    assert participant.consents.filter(kind=ConsentKind.PUBLISH_NAME).exists()


def test_register_without_terms_consent_shows_the_service_message(web_client, edition):
    response = web_client.post(REGISTER_URL, register_payload(terms_consent=""))

    assert response.status_code == 200
    assert "Akceptacja Regulaminu Olimpiady Kwantowej jest wymagana." in response.content.decode()
    assert not User.objects.exists()


# --- dokończenie rejestracji przez dostawcę zewnętrznego --------------------------------------


@pytest.fixture
def google(settings):
    """Klucze dostawcy – ten sam układ, co w ``test_social_login`` (tam pełny przelot OAuth)."""
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {"APP": {"client_id": "id-testowe", "secret": "sekret-testowy", "key": ""}}
    }
    return settings


def test_social_signup_form_shows_the_same_linked_consents(web_client, google, edition):
    """Drugi formularz rejestracji nie może mieć innej treści zgód niż pierwszy."""
    from apps.web.forms import ParticipantRegisterForm, SocialParticipantSignupForm

    web = ParticipantRegisterForm()
    social = SocialParticipantSignupForm()

    for name in web.consent_field_names:
        assert str(web.fields[name].label) == str(social.fields[name].label)
    assert "/dokumenty/regulamin/" in str(social.fields["terms_consent"].label)


def test_social_signup_form_blocks_a_minor_without_the_guardian_consent():
    from apps.web.forms import SocialParticipantSignupForm

    form = SocialParticipantSignupForm(
        data={
            "first_name": "Anna",
            "last_name": "Nowak",
            "school_custom": "on",
            "school": "LO nr 7",
            "district": "mazowieckie",
            "grade": 2,
            "birth_year": minor_year(),
            "terms_consent": "on",
            "gdpr_consent": "on",
        }
    )

    assert form.is_valid() is False
    assert "guardian_consent" in form.errors


def test_consent_fields_do_not_leak_into_the_service_kwargs():
    """``cleaned_data`` jedzie do serwisu jako ``**kwargs`` – każdy nadmiarowy klucz to TypeError."""
    from apps.accounts.consents import CONSENT_FIELD_NAMES
    from apps.web.forms import ParticipantRegisterForm

    form = ParticipantRegisterForm(
        data={
            "email": "kwargs@example.test",
            "password": "Poprawne-Haslo-2026",
            "first_name": "Anna",
            "last_name": "Nowak",
            "school_custom": "on",
            "school": "LO nr 7",
            "district": "mazowieckie",
            "grade": 2,
            "birth_year": adult_year(),
            "terms_consent": "on",
            "gdpr_consent": "on",
        }
    )

    assert form.is_valid(), form.errors
    assert set(form.cleaned_data) == {
        "email",
        "password",
        "first_name",
        "last_name",
        "district",
        "grade",
        "birth_year",
        "school",
        "school_id",
        *CONSENT_FIELD_NAMES,
    }


# --- panel uczestnika -------------------------------------------------------------------------


@pytest.fixture
def logged_in(participant) -> Client:
    client = Client()
    client.force_login(participant.user)
    return client


def test_dashboard_lists_the_consents_with_version_and_date(logged_in, participant, edition):
    ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentKind.TERMS,
        document_version="1.0 z 2 września 2026",
        source=ConsentSource.WEB,
    )

    body = logged_in.get("/me/").content.decode()

    assert "Twoje zgody" in body
    assert "1.0 z 2 września 2026" in body
    assert "akceptacja regulaminu" in body


def test_dashboard_offers_the_publish_name_toggle(logged_in, participant, edition):
    participant.publish_full_name = False
    participant.save(update_fields=["publish_full_name"])

    body = logged_in.get("/me/").content.decode()

    assert TOGGLE_URL in body
    assert "Wyraź zgodę na publikację nazwiska" in body


def test_participant_can_give_and_withdraw_the_publish_name_consent(logged_in, participant, edition):
    response = logged_in.post(TOGGLE_URL, {"given": "1"})

    assert response.status_code == 302
    participant.refresh_from_db()
    assert participant.publish_full_name is True
    assert participant.consents.get(kind=ConsentKind.PUBLISH_NAME).withdrawn_at is None

    logged_in.post(TOGGLE_URL, {"given": "0"})

    participant.refresh_from_db()
    assert participant.publish_full_name is False
    # Wycofanie znaczy wiersz, a nie jego brak – z historii dalej widać, że zgoda obowiązywała.
    assert participant.consents.get(kind=ConsentKind.PUBLISH_NAME).withdrawn_at is not None


def test_publish_name_toggle_is_audited(logged_in, participant, edition):
    logged_in.post(TOGGLE_URL, {"given": "1"})

    entry = AuditLog.objects.get(action="participant.consent_publish_name")
    assert entry.diff["given"] is True
    assert entry.diff["source"] == ConsentSource.PANEL


def test_publish_name_toggle_requires_a_participant(web_client):
    response = web_client.post(TOGGLE_URL, {"given": "1"})

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == "/login/"
    assert parse_qs(urlparse(response.headers["Location"]).query)["next"] == [TOGGLE_URL]


def test_withdrawing_publish_name_removes_the_full_name_from_published_results(participant):
    """Wycofana zgoda ma skutek tam, gdzie zgoda cokolwiek znaczy – w publikowanej tabeli."""
    from apps.results.services import _may_show_full_name

    set_publish_name_consent(participant, given=True)
    participant.refresh_from_db()
    row = {"qualified": True, "publish_full_name": participant.publish_full_name, "is_adult": True}
    assert _may_show_full_name(row) is True

    set_publish_name_consent(participant, given=False)
    participant.refresh_from_db()
    row["publish_full_name"] = participant.publish_full_name
    assert _may_show_full_name(row) is False
