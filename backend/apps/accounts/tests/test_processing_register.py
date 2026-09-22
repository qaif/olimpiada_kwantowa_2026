"""Rejestr czynności przetwarzania (art. 30 RODO): kompletność, zgodność z systemem i eksport.

Rejestr jest danymi w kodzie właśnie po to, żeby dało się go sprawdzić testem. Te testy pilnują
trzech rzeczy:

- **kompletność w rozumieniu przepisu.** Art. 30 ust. 1 wymienia elementy, których żaden wiersz
  nie może nie mieć: cel, podstawę prawną, kategorie osób, kategorie danych, odbiorców, termin
  usunięcia i opis środków technicznych. Wiersz bez któregokolwiek z nich jest luką w dokumencie,
  której nie widać przy czytaniu strony,
- **zgodność z systemem.** Rejestr wymienia jako odbiorcę Google Analytics (za zgodą) i mówi, że
  rozmowy idą przez własną instancję Jitsi – oba te zdania opisują stan kodu i mają się zmienić
  razem z nim. Okres przechowywania danych uczestnika pochodzi z tej samej stałej, co domyślna
  wartość ``Edition.data_retention_months``, więc nie ma dwóch odpowiedzi na to samo pytanie,
- **eksport.** CSV ma tyle wierszy, ile czynności, i te same nagłówki, co widok HTML – plik idzie
  do organu nadzorczego i nie może być skróconą wersją strony.
"""

from __future__ import annotations

import pytest

from apps.accounts.processing_register import (
    ACTIVITIES,
    CSV_HEADERS,
    FORUM_ACTIVITY,
    REGISTER_VERSION,
    activities_for,
    as_rows,
)
from apps.competitions.models import DEFAULT_RETENTION_MONTHS


def test_every_activity_has_all_elements_required_by_article_30():
    for activity in ACTIVITIES:
        assert activity.purpose, f"{activity.key}: brak celu przetwarzania"
        assert activity.legal_basis, f"{activity.key}: brak podstawy prawnej"
        assert activity.subjects, f"{activity.key}: brak kategorii osób"
        assert activity.categories, f"{activity.key}: brak kategorii danych"
        assert activity.recipients, f"{activity.key}: brak odbiorców"
        assert activity.retention, f"{activity.key}: brak terminu usunięcia"
        assert activity.measures, f"{activity.key}: brak środków technicznych"


def test_activity_keys_are_unique():
    """Klucz jest kotwicą na stronie (``#czynnosc-<key>``) – duplikat zepsułby odnośnik w piśmie."""
    keys = [activity.key for activity in ACTIVITIES]

    assert len(keys) == len(set(keys))


def test_the_register_covers_the_whole_life_cycle_of_a_participant_case():
    """Konto, zawody, ocena, wyniki, sprawy sporne – brak któregokolwiek jest luką w dokumencie."""
    keys = {activity.key for activity in ACTIVITIES}

    assert {"konta", "zgody", "prace", "wyniki", "reklamacje"} <= keys


def test_participant_retention_matches_the_default_of_the_edition_field():
    """Jedna odpowiedź na „jak długo trzymacie te dane”: rejestr i automat czytają tę samą liczbę."""
    entry = next(item for item in ACTIVITIES if item.key == "konta")

    assert str(DEFAULT_RETENTION_MONTHS) in entry.retention


def test_the_supervisor_row_exists_and_flags_the_retention_gap():
    """1.5 (22.09.2026): rola istniała wcześniej bez ani jednego wiersza – teraz ma swój.

    Termin przechowywania mówi wprost, że automat retencji jej jeszcze nie obejmuje – to jest
    ten sam dług, udokumentowany w ``apps.accounts.retention`` i w podręczniku organizatora § 9.1,
    a nie przeoczenie tego wiersza.
    """
    entry = next(item for item in ACTIVITIES if item.key == "opiekunowie")

    assert "opiekun" in entry.subjects
    assert any("zgod" in category for category in entry.categories)
    assert "nie obejmuje" in entry.retention


def test_analytics_is_listed_as_a_recipient_only_behind_consent():
    entry = next(item for item in ACTIVITIES if item.key == "serwis")
    recipients = " ".join(entry.recipients)

    assert "Google" in recipients
    assert "po zgodzie" in recipients


def test_interviews_name_the_self_hosted_video_server():
    """Rozmowy idą przez własną instancję Jitsi – rejestr nie może wymieniać obcego dostawcy."""
    entry = next(item for item in ACTIVITIES if item.key == "rozmowy")
    recipients = " ".join(entry.recipients)

    assert "Jitsi" in recipients
    assert "brak odbiorcy zewnętrznego" in recipients


def test_hosting_provider_is_named_in_every_activity():
    """Każde przetwarzanie odbywa się na tym samym serwerze – pominięcie go w wierszu byłoby błędem."""
    for activity in ACTIVITIES:
        assert any("Contabo" in item for item in activity.recipients), activity.key


def test_csv_rows_match_the_activities():
    rows = as_rows()

    assert len(rows) == len(ACTIVITIES)
    assert all(len(row) == len(CSV_HEADERS) for row in rows)
    assert rows[0][0] == ACTIVITIES[0].name


def test_the_version_is_set():
    """Wersja odpowiada na pytanie „czy czytam aktualny rejestr” i stoi w nazwie pliku CSV."""
    assert REGISTER_VERSION


# --- forum uczestników: czynność warunkowa -------------------------------------------------------
#
# Rejestr ma opisywać przetwarzanie, które **naprawdę zachodzi**. Konkurs bez forum nie zbiera ani
# jednego wpisu, więc wiersz o forum byłby w jego rejestrze opisem cudzego przetwarzania – a organ
# nadzorczy czyta ten dokument jako oświadczenie administratora, nie jako spis możliwości systemu.


@pytest.mark.django_db
def test_a_competition_without_the_forum_has_no_forum_row(competition):
    keys = {activity.key for activity in activities_for(competition)}

    assert competition.has_feature("participant_forum") is False
    assert "forum" not in keys


@pytest.mark.django_db
def test_a_competition_with_the_forum_gets_the_forum_row(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "participant_forum": True}

    keys = {activity.key for activity in activities_for(competition)}

    assert "forum" in keys


def test_the_forum_row_has_all_elements_required_by_article_30():
    """Ta sama asercja, co dla wierszy stałych – czynność warunkowa nie jest czynnością gorszą.

    Bez tego testu wiersz forum nie miałby **żadnego** pokrycia: parametryzacja wyżej chodzi po
    ``ACTIVITIES``, a ten wiersz z założenia w nich nie stoi.
    """
    for element in (
        FORUM_ACTIVITY.purpose,
        FORUM_ACTIVITY.legal_basis,
        FORUM_ACTIVITY.subjects,
        FORUM_ACTIVITY.categories,
        FORUM_ACTIVITY.recipients,
        FORUM_ACTIVITY.retention,
        FORUM_ACTIVITY.measures,
    ):
        assert element


def test_the_forum_row_names_the_hosting_provider():
    assert any("Contabo" in item for item in FORUM_ACTIVITY.recipients)


def test_the_forum_row_says_the_signature_is_not_the_public_code():
    """Najważniejsze zdanie tego wiersza: kod ``OLM-…`` jest kluczem anonimowego oceniania i na
    forum nie pojawia się w żadnej postaci. Rejestr ma to mówić wprost, bo to jest **środek
    techniczny** w rozumieniu art. 32, a nie szczegół interfejsu."""
    measures = " ".join(FORUM_ACTIVITY.measures)

    assert "kod publiczny" in measures
    assert "imieniem z inicjałem" in measures


# --- ekran koordynatora -------------------------------------------------------------------------
#
# Widok jest w ``apps.web``, ale jego testy stoją tutaj, razem z treścią, którą renderuje: to ta
# treść jest przedmiotem sprawdzenia, a widok wyłącznie ją wypisuje. Klienta i konta budujemy
# lokalnie, bo fixture'y interfejsu WWW (``apps/web/tests/conftest.py``) należą do tamtej aplikacji.

REGISTER_URL = "/coordinator/processing-register/"


@pytest.fixture
def client_():
    from django.test import Client

    return Client()


@pytest.fixture
def coordinator_user(db):
    from apps.accounts.tests.factories import CoordinatorFactory

    return CoordinatorFactory()


@pytest.fixture
def participant_user(db):
    from apps.accounts.tests.factories import ParticipantFactory

    return ParticipantFactory().user


@pytest.mark.django_db
def test_the_page_is_for_the_coordinator_only(client_, participant_user):
    client_.force_login(participant_user)

    assert client_.get(REGISTER_URL).status_code == 403


@pytest.mark.django_db
def test_the_page_lists_every_activity(client_, coordinator_user):
    client_.force_login(coordinator_user)

    body = client_.get(REGISTER_URL).content.decode()

    for activity in ACTIVITIES:
        assert activity.name in body


@pytest.mark.django_db
def test_the_page_shows_the_administrator_from_site_settings(client_, coordinator_user):
    """Dane administratora pochodzą z ``/cms/``, nie z treści rejestru – jedno źródło dla stopki i pisma."""
    client_.force_login(coordinator_user)

    body = client_.get(REGISTER_URL).content.decode()

    assert "Fundacja Quantum AI" in body


@pytest.mark.django_db
def test_the_csv_export_has_the_header_and_one_row_per_activity(client_, coordinator_user):
    client_.force_login(coordinator_user)

    response = client_.get(f"{REGISTER_URL}?format=csv")
    body = b"".join(response.streaming_content).decode("utf-8")

    assert response["Content-Type"].startswith("text/csv")
    assert CSV_HEADERS[0] in body
    # Nagłówek plus po wierszu na czynność; separator średnik, bo tak czyta polski Excel.
    assert body.count("\r\n") == len(ACTIVITIES) + 1
