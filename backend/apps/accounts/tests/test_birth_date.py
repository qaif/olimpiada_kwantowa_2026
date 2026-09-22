"""Pełna data urodzenia: reguła wieku, spójność kolumn i każda droga, którą data wchodzi do bazy.

Zgłoszenie organizatora z 22.09.2026: „rejestracja ma pytać o pełną datę urodzenia i z niej
rozstrzygać pełnoletność”. Do wydania 0.30.0 znaliśmy sam rocznik i reguła była **przybliżeniem
na korzyść ochrony małoletniego**: kto kończył 18 lat w tym roku, widział obowiązek zgody opiekuna
przez cały rok, także nazajutrz po swoich urodzinach.

Czego pilnują te testy – i dlaczego akurat tego:

- **reguła wieku ma jedno miejsce i dwa warianty.** Z datą odpowiedź jest dokładna („czy
  dzisiaj jest już dzień osiemnastych urodzin”), bez daty – stara, rocznikowa. Profile sprzed tej
  zmiany dnia urodzin nie mają i nikt im go nie dopisze, więc ten drugi wariant nie jest długiem,
  tylko trwałą częścią reguły,
- **dwie kolumny nie mogą się rozjechać.** ``birth_year`` zostaje (``NOT NULL``, minimalizacja
  w eksportach dla podmiotów zewnętrznych) i od tej zmiany jest **liczony z daty**. Więzu
  bazodanowego nie ma, bo nie da się go zapisać przenośnie – jest za to jedno miejsce zapisu
  (``Participant.save``) i ten plik,
- **nieznany wiek znaczy „małoletni”.** W każdej drodze: brak daty i brak rocznika, profil
  rejestracji bez pytania o wiek, plik importu bez kolumny. Zawyżenie kosztuje jeden zbędny
  checkbox, zaniżenie – zgodę pobraną od dziecka bez wiedzy opiekuna,
- **pełna data nie wypływa tam, gdzie wystarczy rocznik.** Eksport koordynatora ma obie kolumny,
  bo to jego własne dane; audyt zapisuje wyłącznie „zmieniło się”; anonimizacja czyści datę całą.

Przypadek 29 lutego ma własne testy, bo rok „+18” bywa nieprzestępny i takiego dnia po prostu
nie ma. Bierzemy 1 marca, czyli dzień później – w razie wątpliwości dłużej uznajemy kogoś za
małoletniego, a nie krócej.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import consents
from apps.accounts.bulk_registration import import_students, preview_upload
from apps.accounts.consents import ConsentKind, adulthood_date, is_minor, required_kinds
from apps.accounts.data_export import export_payload
from apps.accounts.models import UNKNOWN_BIRTH_YEAR, Participant, RegistrationProfile, User
from apps.accounts.profile import anonymise_account, update_participant_profile
from apps.core.models import AuditLog
from apps.results.services import _is_adult

from .factories import ParticipantFactory, UserFactory

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/auth/register/participant/"
PASSWORD = "Poprawne-Haslo-2026"


def shift(day: date, years: int) -> date:
    """``day`` przesunięty o ``years`` lat; 29 lutego w roku nieprzestępnym → 1 marca."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return date(day.year + years, 3, 1)


def born_years_ago(years: int) -> date:
    return shift(timezone.localdate(), -years)


# --- (a) reguła wieku ---------------------------------------------------------------------------


def test_the_eighteenth_birthday_makes_an_adult_on_the_day_itself():
    """Granica jest w dniu urodzin, a nie „gdzieś w tym roku” – to jest cała ta zmiana."""
    today = date(2026, 9, 22)

    assert is_minor(date(2008, 9, 22), today=today) is False
    assert is_minor(date(2008, 9, 23), today=today) is True


def test_a_day_before_the_birthday_is_still_a_minor():
    today = timezone.localdate()

    assert is_minor(shift(today + timedelta(days=1), -18)) is True
    assert is_minor(shift(today, -18)) is False


@pytest.mark.parametrize(
    ("birth", "expected"),
    [
        (date(2008, 2, 29), date(2026, 3, 1)),
        (date(2004, 2, 29), date(2022, 3, 1)),
    ],
)
def test_the_twenty_ninth_of_february_falls_on_the_first_of_march(birth, expected):
    """Urodzony 29 lutego staje się pełnoletni 1 marca – **zawsze**, i to nie jest przypadek.

    Rok przestępny dzieli się przez 4, a 18 nie, więc rok „+18” po roczniku przestępnym nigdy
    przestępny nie jest: dnia osiemnastych urodzin dosłownie nie ma w kalendarzu. Prawo (art. 112
    k.c.) kazałoby wziąć ostatni dzień lutego; my bierzemy 1 marca, czyli dzień później, bo przy
    wątpliwości wolimy uznawać kogoś za małoletniego dłużej niż krócej.
    """
    assert adulthood_date(birth) == expected
    assert is_minor(birth, today=expected) is False
    assert is_minor(birth, today=expected - timedelta(days=1)) is True


def test_without_a_date_the_old_year_rule_still_decides():
    """Profil sprzed wydania 0.30.0: dnia urodzin nie ma, więc zostaje reguła zachowawcza.

    Rocznik „minus 18” znaczy osobę, która może mieć jeszcze 17 lat – i tak ją traktujemy przez
    cały rok. Uściślić się tego nie da: w tych wierszach dnia urodzin po prostu nie zapisano.
    """
    today = date(2026, 6, 1)

    assert is_minor(None, 2008, today=today) is True
    assert is_minor(None, 2007, today=today) is False
    # Ta sama odpowiedź przez drogę wsteczną, czyli rocznik na pierwszej pozycji.
    assert is_minor(2008, today=today) is True


def test_nothing_at_all_means_a_minor():
    """Nieznany wiek nie może być furtką – nie zgadujemy na korzyść pominięcia zgody."""
    assert is_minor(None, None) is True
    assert ConsentKind.GUARDIAN in required_kinds(None, None)


def test_the_date_wins_over_a_contradicting_year():
    """Gdy oba pola są, rozstrzyga dokładniejsze. Wiersza „data vs rocznik” w bazie i tak nie ma."""
    assert is_minor(date(2000, 1, 1), 2020, today=date(2026, 9, 22)) is False


# --- (b) niezmiennik dwóch kolumn ---------------------------------------------------------------


def test_the_year_column_follows_the_date(competition):
    """``birth_year`` jest liczony z daty przy **każdym** zapisie – to jest ten niezmiennik."""
    participant = ParticipantFactory(competition=competition, birth_year=2008)

    participant.birth_date = date(2005, 3, 14)
    participant.save(update_fields=["birth_date"])

    participant.refresh_from_db()
    # Zapis punktowy samej daty dopisał rocznik do ``update_fields`` – bez tego kolumny
    # rozjechałyby się dokładnie w drodze, dla której to pole powstało (uzupełnianie profilu).
    assert participant.birth_year == 2005


def test_a_participant_without_a_date_keeps_the_year_it_has(competition):
    participant = ParticipantFactory(competition=competition, birth_date=None, birth_year=2004)

    participant.refresh_from_db()
    assert participant.birth_date is None
    assert participant.birth_year == 2004
    assert participant.known_birth_year == 2004


def test_an_unknown_year_reads_as_none(competition):
    """Zero w kolumnie rocznika znaczy „nie wiem” i tak ma wyjść na zewnątrz – nie jako liczba."""
    participant = ParticipantFactory(competition=competition, birth_date=None, birth_year=UNKNOWN_BIRTH_YEAR)

    assert participant.known_birth_year is None
    assert is_minor(participant.birth_date, participant.birth_year) is True


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(date(1899, 12, 31), id="sprzed-1900"),
        pytest.param(timezone.localdate() + timedelta(days=1), id="z-przyszlosci"),
    ],
)
def test_the_model_refuses_a_date_outside_the_range(competition, bad):
    participant = ParticipantFactory(competition=competition)
    participant.birth_date = bad

    with pytest.raises(ValidationError) as error:
        participant.clean()

    assert "birth_date" in error.value.message_dict


# --- (c) wyniki: pełnoletność przy publikacji nazwiska -------------------------------------------


def test_results_read_the_exact_age_when_the_date_is_there():
    """``_is_adult`` z datą odpowiada dokładnie – dzień urodzin przestaje być zaokrąglany rocznikiem.

    Pod starą regułą osoba z rocznika „minus 18” nie była pełnoletnia aż do ``ADULT_AGE`` (19),
    więc jej nazwisko nie weszło do tabeli finału nawet dzień po osiemnastych urodzinach.
    """
    today = date(2026, 9, 22)

    assert _is_adult(date(2008, 9, 22), 2008, today) is True
    assert _is_adult(date(2008, 9, 23), 2008, today) is False


def test_results_fall_back_to_the_conservative_year_rule_without_a_date():
    today = date(2026, 9, 22)

    # Rocznik 2007 to pod starą regułą „na pewno pełnoletni” (19 lat), 2008 – jeszcze nie.
    assert _is_adult(None, 2007, today) is True
    assert _is_adult(None, 2008, today) is False
    assert _is_adult(None, None, today) is False


# --- (d) rejestracja przez API -------------------------------------------------------------------


def api_payload(**overrides) -> dict:
    data = {
        "email": "data@example.test",
        "password": PASSWORD,
        "first_name": "Anna",
        "last_name": "Nowak",
        "school": "LO nr 3",
        "district": "mazowieckie",
        "grade": 2,
        "phone": "600 100 200",
        "terms_consent": True,
        "gdpr_consent": True,
    }
    data.update(overrides)
    return data


@pytest.fixture
def api():
    return APIClient()


def test_the_api_accepts_a_full_birth_date_and_derives_the_year(api, open_registration):
    resp = api.post(REGISTER_URL, api_payload(birth_date="1990-04-12"), format="json")

    assert resp.status_code == 201, resp.json()
    participant = Participant.objects.get()
    assert participant.birth_date == date(1990, 4, 12)
    assert participant.birth_year == 1990
    # Profil w ``GET /api/auth/me/`` niesie obie wartości: klient sprzed tej zmiany czyta
    # wyłącznie rocznik i ma dalej dostawać to samo, co dostawał.
    from apps.accounts.serializers import ParticipantProfileSerializer

    data = ParticipantProfileSerializer(participant).data
    assert data["birth_date"] == "1990-04-12"
    assert data["birth_year"] == 1990


def test_the_api_still_accepts_a_bare_year(api, open_registration):
    """Droga wsteczna: klient sprzed wydania 0.30.0 nie zna daty i ma działać bez zmiany znaku."""
    resp = api.post(REGISTER_URL, api_payload(birth_year=1990), format="json")

    assert resp.status_code == 201, resp.json()
    participant = Participant.objects.get()
    assert participant.birth_date is None
    assert participant.birth_year == 1990


def test_the_api_refuses_a_registration_without_any_age(api, open_registration):
    resp = api.post(REGISTER_URL, api_payload(), format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "BIRTH_DATE_REQUIRED"
    assert not User.objects.exists()


def test_the_api_refuses_a_birth_date_from_the_future(api, open_registration):
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()

    resp = api.post(REGISTER_URL, api_payload(birth_date=tomorrow), format="json")

    assert resp.status_code == 400
    assert not User.objects.exists()


def test_the_api_requires_the_guardian_consent_exactly_from_a_minor(api, open_registration):
    """Ta sama granica, co w regule wieku: dzień przed osiemnastymi urodzinami – tak, w dniu – nie."""
    tomorrow_eighteen = shift(timezone.localdate() + timedelta(days=1), -18).isoformat()

    resp = api.post(REGISTER_URL, api_payload(birth_date=tomorrow_eighteen), format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "CONSENT_REQUIRED"


# --- (e) profil rejestracji bez pytania o wiek ---------------------------------------------------


def test_a_competition_that_does_not_ask_for_the_age_treats_everyone_as_a_minor(
    api, competition, open_registration
):
    """Wyłączony przełącznik znaczy „data nieobowiązkowa”, a **nie** „wieku nie sprawdzamy”.

    To jest jedyny bezpieczny odwrót: od wieku zależy podstawa prawna zapisu, więc nieznany wiek
    nie może znaczyć „pełnoletni”. Konkurs, który o datę nie pyta, zbiera zgodę opiekuna od każdego.
    """
    # Profil rejestracji jest czytany **wyłącznie** przy włączonej fladze konkursu
    # (``services.registration_profile``), więc bez niej wiersz w bazie niczego nie zmienia.
    competition.feature_flags = {**(competition.feature_flags or {}), "institution_types": True}
    competition.save(update_fields=["feature_flags"])
    RegistrationProfile.objects.update_or_create(
        competition=competition, defaults={"require_birth_year": False}
    )

    refused = api.post(REGISTER_URL, api_payload(), format="json")
    assert refused.status_code == 400
    assert refused.json()["code"] == "CONSENT_REQUIRED"

    accepted = api.post(REGISTER_URL, api_payload(guardian_consent=True), format="json")

    assert accepted.status_code == 201, accepted.json()
    participant = Participant.objects.get()
    assert participant.birth_date is None
    assert participant.birth_year == UNKNOWN_BIRTH_YEAR


# --- (f) uzupełnienie daty w profilu -------------------------------------------------------------


def test_completing_the_date_leaves_its_own_audit_entry(competition):
    """Uzupełnienie brakującej daty jest osobnym zdarzeniem, a nie pozycją w „zmieniono dane”.

    Pytanie „od kiedy ten uczestnik ma dokładny wiek i kto go wpisał” pada przy sporze o zgodę
    opiekuna i nie może odpowiadać na nie sam napis „zapisano formularz”.
    """
    participant = ParticipantFactory(competition=competition, birth_date=None, birth_year=2007)

    update_participant_profile(participant, actor=participant.user, birth_date="2009-05-04")

    participant.refresh_from_db()
    assert participant.birth_date == date(2009, 5, 4)
    # Rocznik przeliczony z daty, choć uczestnik podał kiedyś inny: dokładniejsza wartość wygrywa.
    assert participant.birth_year == 2009
    entry = AuditLog.objects.get(action="participant.birth_date_completed")
    # ``target_id`` jest kolumną tekstową – audyt wskazuje obiekty różnych tabel.
    assert entry.target_id == str(participant.pk)
    # W audycie nie ma samej daty – jest daną osobową, a wpisy czyta też ktoś bez prawa do niej.
    assert "2009-05-04" not in str(entry.diff)
    assert AuditLog.objects.get(action="participant.profile_updated").diff["birth_date"] is True


def test_changing_an_existing_date_is_not_a_completion(competition):
    participant = ParticipantFactory(competition=competition, birth_year=2007)

    update_participant_profile(participant, actor=participant.user, birth_date="2007-01-02")

    assert not AuditLog.objects.filter(action="participant.birth_date_completed").exists()


# --- (g) import listy klasowej --------------------------------------------------------------------


class Upload(io.BytesIO):
    """Plik „jak z formularza”: ``read_table`` rozpoznaje CSV po rozszerzeniu w nazwie."""

    name = "lista.csv"


def preview_rows(competition, text: str):
    return preview_upload(Upload(text.encode("utf-8")), with_supervisor=False, competition=competition).rows


@pytest.mark.parametrize(
    ("header", "cell", "expected"),
    [
        pytest.param("data urodzenia", "2009-05-04", date(2009, 5, 4), id="iso"),
        pytest.param("data urodzenia", "04.05.2009", date(2009, 5, 4), id="polski"),
        # Arkusz potrafi dokleić godzinę – dzień jest tym, o co pytamy.
        pytest.param("data urodzenia", "2009-05-04 00:00:00", date(2009, 5, 4), id="z-godzina"),
    ],
)
def test_the_import_reads_the_date_column_in_both_notations(competition, header, cell, expected):
    rows = preview_rows(
        competition,
        f"imię;nazwisko;e-mail;{header};klasa\nKasia;Nowak;kasia@example.test;{cell};3\n",
    )

    assert rows[0].errors == []
    assert rows[0].birth_date == expected
    assert rows[0].birth_year == 2009


def test_the_import_still_accepts_a_file_with_the_year_column(competition):
    """Arkusze sprzed wydania 0.30.0 krążą po szkołach gotowe – odesłanie ich byłoby odmową bez powodu."""
    rows = preview_rows(
        competition,
        "imię;nazwisko;e-mail;rok urodzenia;klasa\nKasia;Nowak;kasia@example.test;2009;3\n",
    )

    assert rows[0].errors == []
    assert rows[0].birth_date is None
    assert rows[0].birth_year == 2009


def test_the_import_refuses_a_file_without_any_age_column(competition):
    from apps.core.api import DomainError

    with pytest.raises(DomainError) as error:
        preview_rows(competition, "imię;nazwisko;e-mail;klasa\nKasia;Nowak;kasia@example.test;3\n")

    assert "data urodzenia" in error.value.detail


def test_the_import_marks_a_badly_written_date(competition):
    rows = preview_rows(
        competition,
        "imię;nazwisko;e-mail;data urodzenia;klasa\nKasia;Nowak;kasia@example.test;wczoraj;3\n",
    )

    assert rows[0].birth_date is None
    assert any("data urodzenia" in problem for problem in rows[0].errors)


def test_an_imported_date_reaches_the_profile(competition):
    rows = preview_rows(
        competition,
        "imię;nazwisko;e-mail;data urodzenia;klasa\nKasia;Nowak;kasia@example.test;2009-05-04;3\n",
    )

    import_students(rows, school_name="XIV LO")

    participant = Participant.objects.get(user__email="kasia@example.test")
    assert participant.birth_date == date(2009, 5, 4)
    assert participant.birth_year == 2009


# --- (h) eksporty i minimalizacja ----------------------------------------------------------------


def test_the_participant_data_export_carries_both_the_date_and_the_year(competition):
    participant = ParticipantFactory(competition=competition, birth_year=2008)

    section = export_payload(participant.user)["profil_uczestnika"]

    assert section["data_urodzenia"] == participant.birth_date.isoformat()
    assert section["rok_urodzenia"] == 2008


def test_the_data_export_of_a_legacy_profile_says_the_date_is_missing(competition):
    participant = ParticipantFactory(competition=competition, birth_date=None, birth_year=2008)

    section = export_payload(participant.user)["profil_uczestnika"]

    assert section["data_urodzenia"] is None
    assert section["rok_urodzenia"] == 2008


def test_the_coordinator_export_has_a_date_column(competition):
    from apps.competitions.tests.factories import (
        CurrentEditionFactory,
        StageEntryFactory,
        StageFactory,
    )
    from apps.core.exports import participant_dataset

    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(competition=competition, edition=edition)
    participant = ParticipantFactory(competition=competition, birth_year=2008)
    StageEntryFactory(competition=competition, stage=stage, participant=participant)

    dataset = participant_dataset(edition)

    assert "data urodzenia" in dataset.header
    assert "rok urodzenia" in dataset.header
    row = next(iter(dataset.rows))
    assert row[dataset.header.index("data urodzenia")] == participant.birth_date
    assert row[dataset.header.index("rok urodzenia")] == 2008


def test_the_integration_exports_do_not_carry_the_age_at_all():
    """Zasada minimalizacji: co wychodzi poza organizatora, nie niesie ani daty, ani rocznika.

    Test jest **strukturalny** i taki ma zostać: pyta o moduł, a nie o jeden plik, bo dopisanie
    kolumny z wiekiem do protokołu dla podmiotu zewnętrznego ma tu upaść, zanim ktokolwiek
    zobaczy gotowy PDF.
    """
    from pathlib import Path

    import apps.integrations.exports as exports

    source = Path(exports.__file__).read_text(encoding="utf-8")

    assert "birth_date" not in source
    assert "birth_year" not in source


# --- (i) anonimizacja ------------------------------------------------------------------------------


def test_anonymisation_clears_the_whole_date(competition):
    """Dzień i miesiąc urodzin same w sobie zawężają krąg osób – po anonimizacji nie mają czego opisywać."""
    participant = ParticipantFactory(competition=competition, birth_year=2008)
    user = participant.user

    anonymise_account(user)

    participant.refresh_from_db()
    assert participant.birth_date is None
    # Rocznik zostaje, bo kolumna jest ``NOT NULL`` – na wartości jawnie nieprawdziwej.
    assert participant.birth_year == 1900


# --- (j) zgoda opiekuna liczona z daty w panelu ---------------------------------------------------


def test_the_guardian_consent_requirement_reads_the_date(competition):
    from apps.accounts.guardian import requires_guardian_consent

    minor = ParticipantFactory(
        competition=competition,
        user=UserFactory(email="maloletni@example.test", groups=["participant"]),
        birth_date=born_years_ago(18) + timedelta(days=1),
    )
    adult = ParticipantFactory(
        competition=competition,
        user=UserFactory(email="pelnoletni@example.test", groups=["participant"]),
        birth_date=born_years_ago(18),
    )

    assert requires_guardian_consent(minor) is True
    assert requires_guardian_consent(adult) is False


def test_the_consent_set_itself_does_not_change(competition):
    """Reguła wieku zmienia **kogo** dotyczy zgoda opiekuna, a nie to, jakie zgody w ogóle zbieramy."""
    kinds = [consent.kind for consent in consents.consent_set(competition)]

    assert kinds == [
        ConsentKind.TERMS,
        ConsentKind.PRIVACY,
        ConsentKind.GUARDIAN,
        ConsentKind.PUBLISH_NAME,
    ]
