"""CAPTCHA i pułapki antyspamowe publicznych formularzy rejestracji (apps/web/captcha.py).

Czego pilnują te testy:

- że zabezpieczenie **jest** na obu publicznych formularzach i że bez niego nie powstaje konto –
  to jedyna rzecz, która chroni rejestrację przed seryjnym zakładaniem kont skryptem,
- że jest **nasze**: obrazek stoi pod naszym adresem i przechodzi przez politykę CSP, w której nie
  ma ani jednej obcej domeny. Gdyby ktoś kiedyś wymienił to na reCAPTCHĘ, ten test zapłonie,
- że pułapka i próg czasu odrzucają cicho i **jednym** komunikatem (bez instrukcji obejścia),
- że pola pomocnicze nie wyciekają do ``cleaned_data`` – widoki wołają serwisy przez
  ``**form.cleaned_data``, więc każdy nadmiarowy klucz to ``TypeError`` w ``register_participant``.

Tryb testowy CAPTCHY (``CAPTCHA_TEST_MODE``) jest włączony w ``config/settings/test.py``, więc
prawdziwą drogę – odczytanie wyzwania z bazy i wpisanie wyniku – sprawdzamy przez wyłączenie tej
flagi w ``captcha.conf.settings``. To nie jest ta sama zmienna, co w ustawieniach Django: pakiet
kopiuje ustawienia do własnego modułu przy imporcie, więc ``settings.CAPTCHA_TEST_MODE = False``
w teście nie zmieniłby niczego.
"""

import pytest
from captcha.models import CaptchaStore

from apps.accounts.models import CommitteeMember, User
from apps.web.captcha import ANTISPAM_FIELD_NAMES, REJECTED_MESSAGE
from apps.web.forms import CommitteeRegisterForm, ParticipantRegisterForm, SocialParticipantSignupForm

from .conftest import captcha_fields, participant_extra_fields, password_fields

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"
COMMITTEE_URL = "/register/committee/"


def participant_payload(**overrides) -> dict:
    data = {
        "email": "antyspam@example.test",
        "first_name": "Anna",
        "last_name": "Nowak",
        "school_custom": "on",
        "school": "LO nr 7",
        "district": "mazowieckie",
        "grade": 2,
        "birth_year": 2008,
        "terms_consent": "on",
        "gdpr_consent": "on",
        "guardian_consent": "on",
        # Hasło z powtórzeniem, telefon i blok antyspamowy jednym helperem z conftestu: przedmiotem
        # tych testów jest CAPTCHA, a nie reszta pól, którymi formularz jeszcze urośnie.
        **participant_extra_fields(),
    }
    data.update(overrides)
    return {name: value for name, value in data.items() if value is not None}


def committee_payload(**overrides) -> dict:
    data = {
        "email": "komisja@example.test",
        **password_fields(),
        "first_name": "Anna",
        "last_name": "Odwolawska",
        "invitation_code": "kod-ktorego-nie-ma",
        **captcha_fields(),
    }
    data.update(overrides)
    return {name: value for name, value in data.items() if value is not None}


def new_challenge() -> tuple[str, str]:
    """Świeże wyzwanie z bazy: (klucz, poprawna odpowiedź) – tak jak zobaczyłby je człowiek."""
    key = CaptchaStore.generate_key()
    return key, CaptchaStore.objects.get(hashkey=key).response


@pytest.fixture
def real_captcha(monkeypatch):
    """Wyłącza tryb testowy CAPTCHY w module, z którego pakiet faktycznie go czyta."""
    monkeypatch.setattr("captcha.conf.settings.CAPTCHA_TEST_MODE", False)


# --- wyzwanie -----------------------------------------------------------------------------------


def test_challenge_is_arithmetic_and_lives_in_our_database():
    """Wyzwaniem jest działanie, a nie losowe litery – „l” i „1” nie mają jak się pomylić."""
    key, response = new_challenge()

    store = CaptchaStore.objects.get(hashkey=key)
    assert store.challenge.endswith("=")
    assert any(operator in store.challenge for operator in ("×", "+", "-"))
    assert response.isdigit()


def test_captcha_image_comes_from_our_own_address_and_passes_the_policy(web_client, edition):
    key, _ = new_challenge()

    image = web_client.get(f"/captcha/image/{key}/")

    assert image.status_code == 200
    assert image.headers["Content-Type"] == "image/png"
    # Obrazek jest wstawiany na stronę rejestracji, więc musi się mieścić w ``img-src`` polityki
    # tej strony. ``'self'`` wystarcza dokładnie dlatego, że adres jest nasz.
    policy = web_client.get(REGISTER_URL).headers["Content-Security-Policy"]
    assert "img-src 'self'" in policy
    # Żaden dostawca CAPTCHY nie musiał trafić do polityki – bo żadnego nie ma (patrz
    # ``/dokumenty/cookies/``). Ta lista zapłonie przy pierwszej próbie podmiany na usługę obcą.
    for third_party in ("recaptcha", "hcaptcha", "turnstile", "challenges."):
        assert third_party not in policy


def test_registration_page_renders_the_captcha_after_the_consents(web_client, edition):
    body = web_client.get(REGISTER_URL).content.decode()

    assert "/captcha/image/" in body
    assert 'name="captcha_1"' in body
    assert "Zabezpieczenie antyspamowe" in body
    # Adres kontaktowy zamiast wersji dźwiękowej – jedyne wyjście dla kogoś, kto obrazka nie widzi.
    assert "contact@qaif.org" in body
    # CAPTCHA jest ostatnią czynnością przed wysłaniem, więc stoi pod blokiem zgód.
    assert body.index('class="consents"') < body.index('name="captcha_1"')


def test_honeypot_is_hidden_by_stylesheet_and_not_by_input_type(web_client, edition):
    """Pole ukryte ``type="hidden"`` byłoby dla bota sygnałem „nie wypełniaj”."""
    body = web_client.get(REGISTER_URL).content.decode()

    honeypot = next(line for line in body.splitlines() if 'name="website"' in line)
    assert 'type="text"' in honeypot
    assert 'class="hp-field"' in honeypot
    assert 'type="hidden"' not in honeypot


# --- formularz uczestnika -----------------------------------------------------------------------


def test_missing_captcha_blocks_the_registration(web_client, edition):
    payload = participant_payload(captcha_0=None, captcha_1=None)

    response = web_client.post(REGISTER_URL, payload)

    assert response.status_code == 200
    assert not User.objects.filter(email="antyspam@example.test").exists()


def test_wrong_answer_is_rejected_and_the_right_one_passes(real_captcha, web_client, edition):
    key, response_text = new_challenge()

    wrong = web_client.post(REGISTER_URL, participant_payload(captcha_0=key, captcha_1="999"))
    assert wrong.status_code == 200
    assert not User.objects.filter(email="antyspam@example.test").exists()

    # Zużyte wyzwanie znika z bazy, więc druga próba potrzebuje nowego – tak samo jak człowiek,
    # który po błędzie dostaje nowy obrazek.
    key, response_text = new_challenge()
    accepted = web_client.post(REGISTER_URL, participant_payload(captcha_0=key, captcha_1=response_text))

    assert accepted.status_code == 302
    assert User.objects.filter(email="antyspam@example.test").exists()
    assert not CaptchaStore.objects.filter(hashkey=key).exists()


def test_test_mode_answer_passes_only_in_tests(web_client, edition):
    """Skrót dla testów i scenariusza E2E: „PASSED” przechodzi, gdy flaga jest włączona."""
    response = web_client.post(REGISTER_URL, participant_payload())

    assert response.status_code == 302
    assert User.objects.filter(email="antyspam@example.test").exists()


def test_filled_honeypot_is_rejected_without_saying_why(web_client, edition):
    response = web_client.post(REGISTER_URL, participant_payload(website="https://tanie-buty.example"))

    assert response.status_code == 200
    assert not User.objects.filter(email="antyspam@example.test").exists()
    body = response.content.decode()
    assert REJECTED_MESSAGE in body
    # Komunikat nie nazywa reguły, która odrzuciła zgłoszenie – inaczej byłby instrukcją obejścia.
    # (Nazwa pola w HTML-u zostaje: pułapka musi wyglądać jak zwykłe pole formularza.)
    assert "pułapk" not in body.lower()
    assert "honeypot" not in body.lower()


def test_submission_faster_than_the_threshold_is_rejected(web_client, edition):
    response = web_client.post(REGISTER_URL, participant_payload(**captcha_fields(elapsed=0)))

    assert response.status_code == 200
    assert REJECTED_MESSAGE in response.content.decode()
    assert not User.objects.filter(email="antyspam@example.test").exists()


@pytest.mark.parametrize("timestamp", ["", "1700000000", "1700000000:podrobiony-podpis"])
def test_timestamp_without_our_signature_is_rejected(web_client, edition, timestamp):
    """Bez podpisu bot przepisałby znacznik na dowolną wartość z przeszłości."""
    response = web_client.post(REGISTER_URL, participant_payload(form_ts=timestamp))

    assert response.status_code == 200
    assert REJECTED_MESSAGE in response.content.decode()
    assert not User.objects.filter(email="antyspam@example.test").exists()


def test_helper_fields_do_not_reach_the_service():
    """``cleaned_data`` jedzie do serwisu jako ``**kwargs`` – każdy nadmiarowy klucz to TypeError."""
    form = ParticipantRegisterForm(participant_payload())

    assert form.is_valid(), form.errors
    assert set(form.cleaned_data) & set(ANTISPAM_FIELD_NAMES) == set()


def test_captcha_stands_last_in_the_form():
    """Kolejność wynika z deklaracji, a nie z kolejności powstawania pól (zgody też są dynamiczne)."""
    names = list(ParticipantRegisterForm().fields)

    assert names[-1] == "captcha"
    assert names.index("terms_consent") < names.index("captcha")


def test_social_signup_has_no_captcha():
    """Tam bramką jest logowanie u dostawcy – bot musiałby najpierw przejść OAuth."""
    assert "captcha" not in SocialParticipantSignupForm().fields


# --- formularz komitetu -------------------------------------------------------------------------


def test_committee_form_requires_the_captcha_too():
    form = CommitteeRegisterForm(committee_payload(captcha_0=None, captcha_1=None))

    assert form.is_valid() is False
    assert "captcha" in form.errors


def test_committee_captcha_page_renders_the_widget(web_client):
    body = web_client.get(COMMITTEE_URL).content.decode()

    assert "/captcha/image/" in body
    assert 'name="captcha_1"' in body
    assert "contact@qaif.org" in body


def test_committee_honeypot_is_rejected_before_the_invitation_code(web_client):
    """Pułapka odcina próbę, zanim kod zaproszenia zostanie w ogóle sprawdzony."""
    response = web_client.post(COMMITTEE_URL, committee_payload(website="cokolwiek"))

    assert response.status_code == 200
    body = response.content.decode()
    assert REJECTED_MESSAGE in body
    assert "Kod zaproszenia jest nieprawidłowy" not in body
    assert not CommitteeMember.objects.exists()


def test_committee_registration_with_the_captcha_reaches_the_service(web_client):
    """Przy poprawnej CAPTCZY POST dochodzi do serwisu – odmowa dotyczy już samego kodu."""
    response = web_client.post(COMMITTEE_URL, committee_payload())

    assert response.status_code == 200
    body = response.content.decode()
    assert "Kod zaproszenia jest nieprawidłowy, wygasł lub został już wykorzystany." in body
    assert REJECTED_MESSAGE not in body
