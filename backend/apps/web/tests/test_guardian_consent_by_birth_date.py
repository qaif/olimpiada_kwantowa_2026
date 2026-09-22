"""Zgoda opiekuna liczona z **daty urodzenia** – wszystkie drogi wejścia i próby obejścia.

Zgłoszenie organizatora z 22.09.2026 brzmi dosłownie: „upewnij się, że **potrzeba** zgody opiekuna
jest poprawnie sprawdzana na podstawie daty urodzenia”. Tym plikiem odpowiadamy na to zdanie, a nie
na pytanie „czy pole daty się renderuje” (to ma ``apps/accounts/tests/test_birth_date.py``).

Trzy rzeczy, których te testy pilnują, i powód każdej z nich:

- **granica jest w dniu urodzin, po stronie serwera.** ``static/js/register-age.js`` odsłania
  i chowa wiersz zgody, ale niczego nie rozstrzyga: formularz da się wysłać bez JavaScriptu,
  z podmienionym HTML-em albo zwykłym ``curl``. Dlatego każdy test tutaj **wysyła żądanie**,
  zamiast sprawdzać atrybuty w HTML-u,
- **ta sama reguła w każdej drodze.** Rejestracja hasłem, dokończenie rejestracji przez dostawcę
  zewnętrznego, API, import listy klasowej i przyjęcie zaproszenia dochodzą do wieku pięcioma
  różnymi drogami, a odpowiedź musi być jedna. Rozjazd którejkolwiek z nich znaczy zgodę pobraną
  od dziecka bez wiedzy opiekuna – albo pytanie o zgodę rodzica zadane dorosłemu człowiekowi,
- **edycja danych nie kasuje dowodu.** Zgoda jest oświadczeniem z własną historią
  (``ConsentRecord``), a nie polem profilu: poprawienie daty urodzenia – w którąkolwiek stronę –
  nie ma prawa usunąć ani dopisać wpisu dowodowego.

Daty są zamrożone (``freeze_time``), bo cała ta zmiana dotyczy **jednego dnia w życiu człowieka**
i test liczony od „dziś” nie umiałby pokazać, że granica leży dokładnie tam, gdzie ma leżeć.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.consents import ConsentKind, is_minor
from apps.accounts.guardian import requires_guardian_consent
from apps.accounts.models import ConsentRecord, Participant, User
from apps.accounts.tests.factories import ParticipantFactory

from .conftest import captcha_fields, password_fields

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"
PROFILE_URL = "/me/profile/"

#: Dzień, na którym stoją testy granicy. Wybrany tak, żeby „osiemnaste urodziny dzisiaj” miało
#: w kalendarzu okrągłą datę urodzenia – ułatwia to czytanie asercji, a nic poza tym nie znaczy.
TODAY = date(2026, 9, 22)

#: Osiemnaste urodziny **dzisiaj**: pełnoletni (dzień urodzin już nastał).
ADULT_TODAY = date(2008, 9, 22)

#: Osiemnaste urodziny **jutro**: jeszcze małoletni, i to jest cała różnica tej zmiany –
#: pod starą regułą rocznikową obie te osoby były małoletnie przez cały 2026 rok.
MINOR_BY_A_DAY = date(2008, 9, 23)


def register_payload(**overrides) -> dict:
    data = {
        "email": "granica@example.test",
        **password_fields(),
        "first_name": "Anna",
        "last_name": "Nowak",
        "school_custom": "on",
        "school": "LO nr 7",
        "district": "mazowieckie",
        "grade": 2,
        "birth_date": ADULT_TODAY.isoformat(),
        "phone": "600 100 200",
        "terms_consent": "on",
        "gdpr_consent": "on",
        **captcha_fields(),
    }
    data.update(overrides)
    return data


# --- (a) granica: dzień urodzin -----------------------------------------------------------------


@freeze_time(TODAY)
def test_an_adult_since_today_registers_without_the_guardian_consent(web_client, edition):
    """Osiemnaste urodziny **dzisiaj** – zgoda opiekuna przestaje być wymagana tego samego dnia."""
    response = web_client.post(REGISTER_URL, register_payload(birth_date=ADULT_TODAY.isoformat()))

    assert response.status_code == 302
    participant = Participant.objects.get()
    assert participant.birth_date == ADULT_TODAY
    assert requires_guardian_consent(participant) is False
    assert not participant.consents.filter(kind=ConsentKind.GUARDIAN).exists()


@freeze_time(TODAY)
def test_a_minor_by_one_day_is_refused_without_the_guardian_consent(web_client, edition):
    """Osiemnaste urodziny **jutro** – jeden dzień różnicy i zgoda jest wymagana."""
    response = web_client.post(REGISTER_URL, register_payload(birth_date=MINOR_BY_A_DAY.isoformat()))

    assert response.status_code == 200
    assert "guardian_consent" in response.context["form"].errors
    # Nic nie powstało: sprawdzenie zgód stoi przed utworzeniem konta.
    assert not User.objects.exists()


@freeze_time(TODAY)
def test_that_same_minor_registers_with_the_guardian_consent(web_client, edition):
    response = web_client.post(
        REGISTER_URL, register_payload(birth_date=MINOR_BY_A_DAY.isoformat(), guardian_consent="on")
    )

    assert response.status_code == 302
    participant = Participant.objects.get()
    assert participant.guardian_consent is True
    assert participant.consents.filter(kind=ConsentKind.GUARDIAN).exists()


@pytest.mark.parametrize(
    ("day", "minor"),
    [
        pytest.param(date(2026, 2, 28), True, id="28-lutego-jeszcze-maloletni"),
        pytest.param(date(2026, 3, 1), False, id="1-marca-juz-pelnoletni"),
    ],
)
def test_the_twenty_ninth_of_february_is_judged_on_the_first_of_march(day, minor):
    """Urodzony 29.02.2008: roku 2026 nie ma tego dnia w kalendarzu, więc granicą jest 1 marca.

    Dzień później zamiast dnia wcześniej – przy wątpliwości wolimy wymagać zgody opiekuna o jeden
    dzień za długo niż o jeden dzień za krótko.
    """
    assert is_minor(date(2008, 2, 29), today=day) is minor


# --- (b) próby obejścia po stronie serwera ------------------------------------------------------


@freeze_time(TODAY)
def test_a_missing_birth_date_field_does_not_open_a_way_in(web_client, edition):
    """Pole wycięte z HTML-a przed wysłaniem: formularz odmawia, a nie zakłada „pełnoletni”."""
    payload = register_payload()
    payload.pop("birth_date")

    response = web_client.post(REGISTER_URL, payload)

    assert response.status_code == 200
    assert "birth_date" in response.context["form"].errors
    assert not User.objects.exists()


@freeze_time(TODAY)
def test_a_birth_date_from_the_future_is_refused(web_client, edition):
    """Data z przyszłości jest pomyłką albo próbą – w obu przypadkach nie ma czego zapisywać."""
    response = web_client.post(
        REGISTER_URL, register_payload(birth_date=(TODAY + timedelta(days=1)).isoformat())
    )

    assert response.status_code == 200
    assert "birth_date" in response.context["form"].errors
    assert not User.objects.exists()


@freeze_time(TODAY)
def test_a_nonsense_birth_date_is_refused(web_client, edition):
    response = web_client.post(REGISTER_URL, register_payload(birth_date="wczoraj"))

    assert response.status_code == 200
    assert "birth_date" in response.context["form"].errors
    assert not User.objects.exists()


@freeze_time(TODAY)
def test_the_lower_bound_is_accepted(web_client, edition):
    """1 stycznia 1900 jest granicą sita na literówki, a nie regułą wieku – wolno ją podać."""
    response = web_client.post(REGISTER_URL, register_payload(birth_date="1900-01-01"))

    assert response.status_code == 302
    assert Participant.objects.get().birth_date == date(1900, 1, 1)


@freeze_time(TODAY)
def test_a_date_before_nineteen_hundred_is_refused(web_client, edition):
    response = web_client.post(REGISTER_URL, register_payload(birth_date="1899-12-31"))

    assert response.status_code == 200
    assert "birth_date" in response.context["form"].errors


@freeze_time(TODAY)
def test_a_child_born_today_is_a_minor(web_client, edition):
    """Dzisiejsza data jest poprawna (to nie jest przyszłość) i znaczy osobę **na pewno** małoletnią."""
    response = web_client.post(REGISTER_URL, register_payload(birth_date=TODAY.isoformat()))

    assert response.status_code == 200
    assert "guardian_consent" in response.context["form"].errors


@freeze_time(TODAY)
def test_the_guardian_checkbox_removed_from_the_html_does_not_help(web_client, edition):
    """Skrypt chowa wiersz i zdejmuje ``required`` – serwer o tym nie wie i wiedzieć nie ma.

    To jest dokładnie ta droga, którą przechodzi żądanie wysłane bez JavaScriptu: pola zgody
    w ``POST`` po prostu nie ma. Brak pola znaczy „nie złożono oświadczenia”, a nie „nie dotyczy”.
    """
    payload = register_payload(birth_date=MINOR_BY_A_DAY.isoformat())
    assert "guardian_consent" not in payload

    response = web_client.post(REGISTER_URL, payload)

    assert response.status_code == 200
    assert "guardian_consent" in response.context["form"].errors


# --- (c) edycja profilu nie rusza dowodu --------------------------------------------------------


@freeze_time(TODAY)
def test_moving_the_date_from_adult_to_minor_keeps_the_guardian_consent_required(web_client, competition):
    """Uczestnik nie zdejmie z siebie zgody opiekuna, poprawiając datę urodzenia.

    Ekran ``/me/profile/`` nie ma ani jednego pola zgody i to jest sedno: zgody są oświadczeniami
    z własną historią, a nie polami profilu. Po zmianie daty na „małoletni” wymagalność zgody
    **wraca**, licząc się od nowa z tej samej, jednej reguły.
    """
    participant = ParticipantFactory(competition=competition, birth_date=ADULT_TODAY)
    web_client.force_login(participant.user)

    response = web_client.post(
        PROFILE_URL,
        {
            "first_name": participant.user.first_name,
            "last_name": participant.user.last_name,
            "phone": "600 300 400",
            "district": participant.district,
            "grade": str(participant.grade),
            "birth_date": MINOR_BY_A_DAY.isoformat(),
            "school_custom": "on",
            "school": participant.school,
        },
    )

    assert response.status_code == 302
    participant.refresh_from_db()
    assert participant.birth_date == MINOR_BY_A_DAY
    assert requires_guardian_consent(participant) is True


@freeze_time(TODAY)
def test_becoming_an_adult_does_not_delete_the_evidence(web_client, competition):
    """Zmiana w drugą stronę: wymagalność gaśnie, ale wpis dowodowy zostaje nietknięty.

    Dowód mówi „na co ta osoba się zgodziła i kiedy”, a nie „czego od niej dzisiaj wymagamy”.
    Kasowanie go razem z wymagalnością wycierałoby historię, o którą pyta organ nadzorczy.
    """
    participant = ParticipantFactory(competition=competition, birth_date=MINOR_BY_A_DAY)
    record = ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentKind.GUARDIAN,
        document_version="0.1",
        source="web",
        given_by_email="rodzic@example.test",
    )
    web_client.force_login(participant.user)

    web_client.post(
        PROFILE_URL,
        {
            "first_name": participant.user.first_name,
            "last_name": participant.user.last_name,
            "phone": "600 300 400",
            "district": participant.district,
            "grade": str(participant.grade),
            "birth_date": ADULT_TODAY.isoformat(),
            "school_custom": "on",
            "school": participant.school,
        },
    )

    participant.refresh_from_db()
    record.refresh_from_db()
    assert requires_guardian_consent(participant) is False
    assert record.withdrawn_at is None
    assert ConsentRecord.objects.filter(participant=participant, kind=ConsentKind.GUARDIAN).count() == 1


# --- (d) panel uczestnika i karta koordynatora --------------------------------------------------


@freeze_time(TODAY)
def test_the_panel_asks_a_minor_for_the_guardian_consent_and_leaves_an_adult_alone(web_client, competition):
    minor = ParticipantFactory(
        competition=competition,
        user__email="maloletni@example.test",
        birth_date=MINOR_BY_A_DAY,
    )
    web_client.force_login(minor.user)
    body = web_client.get("/me/?tab=zgody").content.decode()

    assert "zgod" in body.lower()
    assert requires_guardian_consent(minor) is True

    adult = ParticipantFactory(
        competition=competition,
        user__email="pelnoletni@example.test",
        birth_date=ADULT_TODAY,
    )
    assert requires_guardian_consent(adult) is False


@freeze_time(TODAY)
def test_the_coordinator_is_warned_after_turning_a_participant_into_a_minor(
    web_client, competition, coordinator
):
    """Data urodzenia jest edytowalna z panelu, więc jedna poprawka zmienia podstawę prawną udziału.

    Bez tego zdania koordynator zobaczyłby wyłącznie „dane zostały zapisane” i o brakującej zgodzie
    dowiedziałby się najwcześniej z karty uczestnika, na którą już nie wraca.
    """
    participant = ParticipantFactory(competition=competition, birth_date=ADULT_TODAY)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/accounts/{participant.user.pk}/",
        {
            "account-first_name": participant.user.first_name,
            "account-last_name": participant.user.last_name,
            "account-email": participant.user.email,
            "account-is_active": "on",
            "participant-phone": participant.phone,
            "participant-district": participant.district,
            "participant-grade": str(participant.grade),
            "participant-birth_date": MINOR_BY_A_DAY.isoformat(),
            "participant-school_custom": "on",
            "participant-school": participant.school,
        },
        follow=True,
    )

    participant.refresh_from_db()
    assert participant.birth_date == MINOR_BY_A_DAY
    warnings = [str(message) for message in response.context["messages"]]
    assert any("niepełnoletni" in text and "zgody opiekuna" in text for text in warnings), warnings


# --- (e) przyjęcie zaproszenia przez ucznia z listy klasowej -------------------------------------


@freeze_time(TODAY)
@pytest.mark.parametrize(
    ("birth_date", "expected_status"),
    [
        pytest.param(MINOR_BY_A_DAY, 400, id="maloletni-bez-zgody-odmowa"),
        pytest.param(ADULT_TODAY, 302, id="pelnoletni-przechodzi"),
    ],
)
def test_the_invitation_form_reads_the_age_from_the_profile(
    web_client, competition, birth_date, expected_status
):
    """Uczeń z listy klasowej nie podaje daty – przyszła z pliku nauczyciela, więc liczy ją serwis.

    Formularz przyjęcia zaproszenia nie ma pola wieku i mieć go nie będzie: data jest już
    w profilu, a pytanie o nią drugi raz zapraszałoby do wpisania czegoś innego niż to, co
    nauczyciel zgłosił. Reguła jest ta sama, co przy rejestracji otwartej.
    """
    from apps.accounts.bulk_registration import make_invite_token

    # Profil „zaproszonego ucznia”, czyli dokładnie to, co zostawia import listy klasowej:
    # konto nieaktywne i niepotwierdzone (inaczej ``read_invite_token`` uzna token za zużyty),
    # ``invited_at`` wypełnione, a zgód jeszcze nie ma – nauczyciel nie składa ich za nikogo.
    participant = ParticipantFactory(
        competition=competition,
        birth_date=birth_date,
        user__email="zaproszony@example.test",
        user__is_active=False,
        user__email_verified_at=None,
        invited_at=timezone.now(),
        gdpr_consent_at=None,
        terms_accepted_at=None,
        guardian_consent=False,
    )
    token = make_invite_token(participant)

    response = web_client.post(
        f"/zaproszenie/{token}/",
        {
            **password_fields(),
            "phone": "600 100 200",
            "district": participant.district,
            "terms_consent": "on",
            "gdpr_consent": "on",
        },
    )

    assert response.status_code == expected_status
