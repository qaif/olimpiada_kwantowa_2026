"""Zgody w interfejsie WWW: oba formularze rejestracji i przełącznik w panelu uczestnika.

Reguły domenowe (co jest wymagane, od kogo, pod jaką wersją dokumentu) mają własne testy
w ``apps/accounts/tests/test_consents.py``. Tutaj sprawdzamy to, czego tamte nie widzą:

- czy uczestnik **widzi**, na co się zgadza – etykieta z odnośnikiem do dokumentu, a nie sam
  napis „Zgoda RODO”,
- czy blokada zgody opiekuna działa na obu drogach rejestracji i staje **pod polem**,
- czy w panelu widać historię zgód i czy jedyną odwracalną z nich da się faktycznie odwrócić.
"""

from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.consents import MINOR_MAX_AGE, ConsentKind, ConsentSource
from apps.accounts.models import ConsentRecord, Participant, User
from apps.accounts.services import set_publish_name_consent
from apps.core.models import AuditLog

from .conftest import captcha_fields, password_fields

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"
SIGNUP_URL = "/rejestracja/dokoncz/"
TOGGLE_URL = "/me/consents/publish-name/"

#: Zgody są zakładką pulpitu, a nie sekcją jednej długiej strony (``MeView``), więc panel czyta
#: się pod adresem z parametrem. Adres domyślny (``/me/``) otwiera zakładkę „Zadania”.
CONSENTS_TAB_URL = "/me/?tab=zgody"


def _shift(day, years: int):
    """``day`` przesunięty o ``years`` lat; 29 lutego w roku nieprzestępnym → 1 marca."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return date(day.year + years, 3, 1)


def minor_date() -> str:
    """Data urodzenia osoby, która osiemnastych urodzin **jeszcze nie miała** – ma je jutro.

    Dokładnie na granicy reguły i liczona od „dziś”, żeby test nie starzał się z kalendarzem.
    """
    return _shift(timezone.localdate() + timedelta(days=1), -MINOR_MAX_AGE).isoformat()


def adult_date() -> str:
    """Data urodzenia osoby, która osiemnaste urodziny ma **dzisiaj** – czyli jest już pełnoletnia."""
    return _shift(timezone.localdate(), -MINOR_MAX_AGE).isoformat()


def register_payload(**overrides) -> dict:
    data = {
        "email": "zgody-web@example.test",
        **password_fields(),
        "first_name": "Anna",
        "last_name": "Nowak",
        "school_custom": "on",
        "school": "LO nr 7",
        "district": "mazowieckie",
        "grade": 2,
        "birth_date": adult_date(),
        "phone": "600 100 200",
        "terms_consent": "on",
        "gdpr_consent": "on",
        # Blok antyspamowy (CAPTCHA w trybie testowym, pusta pułapka, podpisany znacznik czasu).
        # Bez niego każdy POST na ``/register/`` odbijałby się o formularz, a test zgód badałby
        # wyłącznie to, że CAPTCHA działa.
        **captcha_fields(),
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


def test_register_form_links_the_consent_label_to_the_pdf_when_there_is_one(web_client, edition):
    """Uwaga organizatora z 16.09: „linki do regulaminów powinny prowadzić do PDF-ów”.

    Reguła wyboru adresu ma własne testy w ``apps/accounts/tests/test_consents.py``; tutaj
    sprawdzamy, że formularz faktycznie renderuje ten adres – i że nadal otwiera go w nowej
    karcie, bo przeczytanie regulaminu nie może kosztować wypełnionego formularza.
    """
    from apps.accounts.tests.test_consents import attach_file, publish_document_page

    page = publish_document_page("regulamin")
    pdf = attach_file(page, filename="regulamin.pdf", label="PDF do druku")

    body = web_client.get(REGISTER_URL).content.decode()

    assert f'href="{pdf.url}" target="_blank" rel="noopener"' in body
    # Strona dokumentu przestaje być celem etykiety – linkiem jest plik.
    assert 'href="/dokumenty/regulamin/"' not in body


def test_register_form_blocks_a_minor_without_the_guardian_consent(web_client, edition):
    response = web_client.post(REGISTER_URL, register_payload(birth_date=minor_date()))

    assert response.status_code == 200
    form = response.context["form"]
    # Błąd stoi **pod polem** zgody opiekuna, a nie nad formularzem: wynika z rocznika
    # wpisanego obok, więc bez wskazania palcem nie wiadomo, co poprawić.
    assert "guardian_consent" in form.errors
    assert form.errors["guardian_consent"] == [
        "Zgoda rodzica lub opiekuna prawnego jest wymagana dla uczestnika niepełnoletniego."
    ]
    assert not User.objects.exists()


def test_register_form_does_not_require_the_guardian_consent_from_an_adult(web_client, edition):
    """Druga połowa tej samej reguły: pełnoletni zakłada konto bez oświadczenia o opiekunie."""
    response = web_client.post(REGISTER_URL, register_payload(birth_date=adult_date()))

    assert response.status_code == 302
    participant = Participant.objects.get()
    assert participant.guardian_consent is False
    assert not participant.consents.filter(kind=ConsentKind.GUARDIAN).exists()


# --- powiązanie daty urodzenia ze zgodą opiekuna w przeglądarce --------------------------------


def test_the_birth_date_says_what_the_guardian_consent_depends_on(web_client, edition):
    """Uwaga organizatora z 16.09: „rok urodzenia nie jest powiązany z obowiązkowością oświadczenia”.

    Powiązanie istniało od początku po stronie serwera, ale formularz o nim milczał: uczestnik
    poznawał obowiązek dopiero z odmowy po wysłaniu. Zdanie pod datą urodzenia mówi to wcześniej.
    """
    body = web_client.get(REGISTER_URL).content.decode()

    assert "Osoby niepełnoletnie potrzebują zgody opiekuna" in body
    assert body.index("Osoby niepełnoletnie potrzebują zgody opiekuna") < body.index('class="consents"')


def test_the_consents_block_carries_the_minor_rule_for_the_script(web_client, edition):
    """Skrypt nie ma własnej definicji „niepełnoletni” – regułę i dzisiejszą datę dostaje z serwera.

    Dzisiejszą **datę**, a nie rok: od wydania 0.30.0 pełnoletność zapada w dniu urodzin, więc
    zegar przeglądarki (bywa w innej strefie niż Europe/Warsaw) potrafiłby się z serwerem
    rozejść dokładnie w tym jednym dniu, w którym ta odpowiedź się zmienia.
    """
    from apps.accounts.consents import MINOR_MAX_AGE

    body = web_client.get(REGISTER_URL).content.decode()

    assert "data-age-consents" in body
    assert 'data-birth-date-field="id_birth_date"' in body
    assert f'data-minor-max-age="{MINOR_MAX_AGE}"' in body
    assert f'data-current-date="{timezone.localdate().isoformat()}"' in body
    assert 'data-age="birth-date"' in body
    # Kontrolka kalendarza i dolna granica sita na literówki – obie z ``birth_date_field``.
    assert 'type="date"' in body
    assert 'min="1900-01-01"' in body
    # Znacznik stoi **wyłącznie** przy zgodzie warunkowej – pozostałe trzy są bezwarunkowe.
    assert body.count('data-age="minor-consent"') == 1
    row = body.split('data-age="minor-consent"', 1)[1]
    assert 'name="guardian_consent"' in row.split("</div>", 1)[0]


def test_the_registration_page_loads_the_age_script_with_a_nonce(web_client, edition):
    body = web_client.get(REGISTER_URL).content.decode()

    assert "js/register-age.js" in body
    script = next(line for line in body.splitlines() if "js/register-age.js" in line)
    assert 'nonce="' in script
    # ``defer`` i adres z własnego serwisu – w projekcie nie ma ani jednego skryptu inline.
    assert "defer" in script
    assert "//" not in script.split("src=")[1]


def test_the_consent_row_is_visible_without_javascript(web_client, edition):
    """Bez skryptu wiersz zgody opiekuna stoi odsłonięty – serwer i tak jej zażąda od małoletniego."""
    body = web_client.get(REGISTER_URL).content.decode()
    row = body.split('data-age="minor-consent"', 1)[1].split(">", 1)[0]

    assert "hidden" not in row


def test_register_form_accepts_a_minor_with_the_guardian_consent(web_client, edition):
    response = web_client.post(REGISTER_URL, register_payload(birth_date=minor_date(), guardian_consent="on"))

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
            "birth_date": minor_date(),
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
            **password_fields(),
            "first_name": "Anna",
            "last_name": "Nowak",
            "school_custom": "on",
            "school": "LO nr 7",
            "district": "mazowieckie",
            "grade": 2,
            "birth_date": adult_date(),
            "phone": "600 100 200",
            "terms_consent": "on",
            "gdpr_consent": "on",
            # Pola bloku antyspamowego (CAPTCHA, pułapka, znacznik czasu) też nie mają prawa
            # dojechać do serwisu – ich brak w zbiorze niżej jest częścią tej asercji.
            **captcha_fields(),
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
        "birth_date",
        "phone",
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

    body = logged_in.get(CONSENTS_TAB_URL).content.decode()

    assert "Twoje zgody" in body
    assert "1.0 z 2 września 2026" in body
    assert "akceptacja regulaminu" in body


def test_dashboard_offers_the_publish_name_toggle(logged_in, participant, edition):
    participant.publish_full_name = False
    participant.save(update_fields=["publish_full_name"])

    body = logged_in.get(CONSENTS_TAB_URL).content.decode()

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
