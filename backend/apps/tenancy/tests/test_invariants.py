"""Testy niezmienności: co w Konkursie #1 ma zostać **dokładnie** takie, jakie jest.

Siedem pozycji z ``docs/UNIWERSALNY-ETAP-1.md`` § 7.3. Każda z nich porównuje bieżące zachowanie
ze **stałą zapisaną w tym pliku**, a nie z drugim odczytem tego samego kodu – porównanie kodu
z samym sobą przechodziłoby także wtedy, gdy zmienia się jedno i drugie naraz.

**Jak zmienić którąkolwiek z tych wartości świadomie:** zmienia się stałą w tym pliku *w tym samym
commicie*, w którym zmienia się zachowanie, a w opisie commitu pisze się, kto zmianę zamówił.
Zgody i wersje dokumentów wymagają dodatkowo decyzji organizatora (są oświadczeniem złożonym pod
konkretnym dokumentem), a tematy listów – sprawdzenia w regułach filtrów pocztowych, bo część
odbiorców ma je posortowane po temacie. Nagłówka CSP nie zmienia się „przy okazji”: każdy dopisany
host to poszerzenie powierzchni ataku dla całego serwisu.

Czego te testy **nie** sprawdzają: treści redakcyjnych. Te należą do redaktora i zmieniają się
w ``/cms/`` bez wdrożenia; ich pilnowanie testem byłoby odebraniem redakcji jej własnej roboty.
"""

from __future__ import annotations

import re

import pytest

from apps.accounts.activation import (
    ACTIVATION_SUBJECT,
    EMAIL_CHANGE_SUBJECT,
    EMAIL_CHANGED_NOTICE_SUBJECT,
)
from apps.accounts.consents import CONSENTS
from apps.accounts.guardian import GUARDIAN_CONFIRMED_SUBJECT, GUARDIAN_SUBJECT
from apps.accounts.models import PUBLIC_CODE_PREFIX, generate_public_code
from apps.accounts.services import INVITATION_SUBJECT
from apps.cms.context_processors import FALLBACK_MENU
from apps.grading.reports import REMINDER_SUBJECT
from apps.results.models import CERTIFICATE_NUMBER_PREFIX
from apps.submissions.notifications import (
    APPEAL_DECIDED_SUBJECT,
    RESULTS_PUBLISHED_SUBJECT,
    SUBMISSION_INFECTED_SUBJECT,
    SUBMISSION_RECEIVED_SUBJECT,
)
from apps.tenancy.tests.golden import build_golden

pytestmark = pytest.mark.django_db


# --- 1. nagłówek CSP ---------------------------------------------------------------------------

#: Polityka bezpieczeństwa treści dla strony publicznej Konkursu #1, bajt w bajt, z jednorazowym
#: ``nonce`` zastąpionym znacznikiem. Dwa ustawienia są w teście wyzerowane, bo zależą od
#: **środowiska**, a nie od konkursu: publiczny adres MinIO (w produkcji dokłada swój origin do
#: ``img-src``/``media-src``/``connect-src``) i klucze dostawców OAuth (dokładają origin do
#: ``form-action``). Bez tego ten sam kod dawałby inny nagłówek na maszynie dewelopera i w CI,
#: a stała przestałaby cokolwiek znaczyć.
PUBLIC_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "script-src 'self' '{nonce}' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net "
    "'strict-dynamic'; "
    "connect-src 'self' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
    "frame-src https://www.youtube.com https://www.youtube-nocookie.com https://player.vimeo.com; "
    "worker-src 'self' blob:"
)

#: Nonce jest jednorazowy z definicji, więc do porównania wstawiamy w jego miejsce znacznik.
NONCE_PATTERN = re.compile(r"'nonce-[A-Za-z0-9_-]+'")


def test_csp_header_unchanged_for_single_competition(client_for, competition, settings):
    settings.S3_PUBLIC_ENDPOINT_URL = ""
    settings.SOCIALACCOUNT_PROVIDERS = {}

    header = client_for(competition).get("/").headers["Content-Security-Policy"]

    assert NONCE_PATTERN.sub("'{nonce}'", header) == PUBLIC_CSP


def test_csp_does_not_mention_google_without_a_measurement_id(client_for, competition, settings):
    """Serwis bez identyfikatora GA4 ma politykę sprzed dodania analityki – i to jest kryterium.

    Pozwolenie na skrypt, którego strona nigdy nie wczyta, jest samym poszerzeniem powierzchni
    ataku: nie włącza żadnej funkcji i nie da się go zauważyć po zachowaniu serwisu.
    """
    settings.S3_PUBLIC_ENDPOINT_URL = ""

    header = client_for(competition).get("/").headers["Content-Security-Policy"]

    assert "google" not in header


# --- 2. zgody ------------------------------------------------------------------------------------

#: Komplet zgód Konkursu #1: pole formularza, rodzaj, wersja dokumentu i reguła wymagalności.
#: Wersja trafia do wpisu dowodowego (``ConsentRecord.document_version``), więc jej cicha zmiana
#: znaczyłaby, że nie da się już odpowiedzieć na pytanie „na co ta osoba się zgodziła”.
EXPECTED_CONSENTS = (
    ("terms_consent", "TERMS", "1.0 z 2 września 2026", True, False),
    ("gdpr_consent", "PRIVACY", "1.0 z 22 lipca 2026", True, False),
    ("guardian_consent", "GUARDIAN", "0.1 (projekt) z 10 września 2026", False, True),
    ("publish_name_consent", "PUBLISH_NAME", "1.0", False, False),
)


def test_consent_labels_unchanged():
    actual = tuple(
        (
            consent.field_name,
            str(consent.kind),
            consent.version,
            consent.required,
            consent.required_for_minor,
        )
        for consent in CONSENTS
    )

    assert actual == EXPECTED_CONSENTS


# --- 3. tematy listów ------------------------------------------------------------------------------

#: Wszystkie tematy listów wychodzących. Odbiorcy mają na nich reguły w skrzynkach (a organizator
#: – filtry w swojej), więc temat jest częścią kontraktu z człowiekiem, a nie napisem w kodzie.
EXPECTED_SUBJECTS = {
    "activation": "Aktywuj konto – Olimpiada Kwantowa",
    "email_change": "Potwierdź nowy adres e-mail – Olimpiada Kwantowa",
    "email_changed_notice": "Adres e-mail konta został zmieniony – Olimpiada Kwantowa",
    "guardian": "Prośba o zgodę opiekuna – Olimpiada Kwantowa",
    "guardian_confirmed": "Zgoda opiekuna została potwierdzona – Olimpiada Kwantowa",
    "invitation": "Zaproszenie do komitetu Olimpiady Kwantowej",
    "review_reminder": "Przypomnienie o zaległych recenzjach – Olimpiada Kwantowa",
    "submission_received": "Rozwiązanie przyjęte – Olimpiada Kwantowa",
    "submission_infected": "Plik odrzucony przez skan antywirusowy – Olimpiada Kwantowa",
    "results_published": "Wyniki etapu ogłoszone – Olimpiada Kwantowa",
    "appeal_decided": "Decyzja w sprawie reklamacji – Olimpiada Kwantowa",
}

#: Prefiks doklejany przez Django do tematów wysyłanych przez ``mail_admins``/``send_mail``
#: z ``subject_prefix``. Konkurs #1 dostał go w migracji ``tenancy.0002`` jako własną wartość.
EXPECTED_SUBJECT_PREFIX = "[Olimpiada Kwantowa] "


def test_email_subjects_unchanged(settings):
    actual = {
        "activation": str(ACTIVATION_SUBJECT),
        "email_change": str(EMAIL_CHANGE_SUBJECT),
        "email_changed_notice": str(EMAIL_CHANGED_NOTICE_SUBJECT),
        "guardian": str(GUARDIAN_SUBJECT),
        "guardian_confirmed": str(GUARDIAN_CONFIRMED_SUBJECT),
        "invitation": str(INVITATION_SUBJECT),
        "review_reminder": str(REMINDER_SUBJECT),
        "submission_received": str(SUBMISSION_RECEIVED_SUBJECT),
        "submission_infected": str(SUBMISSION_INFECTED_SUBJECT),
        "results_published": str(RESULTS_PUBLISHED_SUBJECT),
        "appeal_decided": str(APPEAL_DECIDED_SUBJECT),
    }

    assert actual == EXPECTED_SUBJECTS
    assert settings.EMAIL_SUBJECT_PREFIX == EXPECTED_SUBJECT_PREFIX


def test_competition_one_keeps_the_installation_mail_settings(competition, settings):
    """Konkurs #1 dostał nadawcę i prefiks z ustawień instalacji – i nie wolno ich rozjechać.

    Od tej zmiany listy wysyłane poza żądaniem czytają je z konkursu, a nie z ustawień. Gdyby
    migracja wpisała co innego, uczestnik dostałby list od innego nadawcy niż dotąd – co część
    filtrów pocztowych potraktuje jak nowego korespondenta, czyli jak spam.
    """
    assert competition.from_email == settings.DEFAULT_FROM_EMAIL
    assert competition.email_subject_prefix == EXPECTED_SUBJECT_PREFIX


# --- 4. i 5. prefiksy identyfikatorów -------------------------------------------------------------


def test_public_code_prefix_is_olm():
    """Kod uczestnika stoi w tabelach wyników i w pismach – jego zmiana unieważnia wydruki."""
    assert PUBLIC_CODE_PREFIX == "OLM-"
    assert generate_public_code().startswith("OLM-")


def test_certificate_number_prefix_is_ok():
    """Numer dyplomu (``OK/<rok>/<nr>``) bywa przepisany do dziennika szkolnego."""
    assert CERTIFICATE_NUMBER_PREFIX == "OK"


# --- 6. menu ---------------------------------------------------------------------------------------

#: Menu Konkursu #1: kolejność i tytuły. Kolejność bierze się z drzewa stron (migracja
#: ``cms.0002``), więc ten test pilnuje zarazem, że zakresowanie CMS-u (T4) nie przestawiło
#: kolejności ani nie podmieniło jej na listę zapasową.
EXPECTED_MENU = ("Aktualności", "Zadania", "Archiwum", "Wyniki")


def test_menu_matches_seeded_tree(client_for, competition):
    response = client_for(competition).get("/")
    titles = tuple(item["title"] for item in response.context["cms_menu"])

    assert titles[: len(EXPECTED_MENU)] == EXPECTED_MENU
    # Lista zapasowa (dla konkursu bez drzewa stron) wymienia te same pozycje i ma to zostać:
    # czytelnik, który trafi na serwis w trakcie awarii bazy, ma zobaczyć **to** menu.
    assert tuple(item["title"] for item in FALLBACK_MENU) == EXPECTED_MENU


# --- 7. liczba zapytań ------------------------------------------------------------------------------

#: Górne progi liczby zapytań na kluczowych ekranach, zmierzone na złotej fiksturze (czyli na
#: świecie w kształcie produkcji, a nie na jednym wierszu z fabryki).
#:
#: **Próg, a nie równość** – i to jest świadome odstępstwo od § 7.3. Powód: zakresowanie dokłada
#: do części ekranów jedno zapytanie o konkurs i to jest zmiana zamówiona; równość zmuszałaby do
#: przepisywania stałej przy każdym takim wydaniu i po trzecim razie nikt nie odróżniałby zmiany
#: zamówionej od regresji. Próg łapie to, o co naprawdę chodzi: zapytanie w pętli, czyli koszt
#: rosnący z liczbą danych.
#:
#: **Jak zmienić świadomie:** najpierw sprawdź, czy przyrost nie jest zapytaniem na wiersz
#: (dołóż uczestnika do złotej fikstury i zobacz, czy liczba rośnie). Jeśli nie rośnie – podnieś
#: próg w tym samym commicie i napisz w opisie, co go podniosło.
QUERY_BUDGET = {
    # Zmierzone na złotej fiksturze (wydanie B, po T2 i T3): 29 / 43 / 45. Zapas trzech zapytań
    # jest miejscem na odczyt konkursu, który zakresowanie dokłada w T4/T5 – po tych zadaniach
    # próg wraca do wartości zmierzonej, a nie zostaje „na wszelki wypadek”.
    "/": 32,
    "/me/": 46,
    "/coordinator/": 48,
}


@pytest.fixture
def golden(competition):
    return build_golden(competition)


@pytest.fixture(autouse=True)
def _reset_panel_counters():
    """Liczniki menu koordynatora liczone od nowa – inaczej pomiar zależałby od kolejności testów.

    Badge przy pozycjach menu mają wspólną, minutową pamięć podręczną (``apps.web.coordinator_nav``),
    a ta w testach żyje **w pamięci procesu**, czyli przechodzi między testami. Bez tego sprzątania
    liczba zapytań na pulpicie byłaby raz „z pamięcią”, raz „bez” – a próg, który raz łapie, a raz
    nie, jest gorszy niż brak progu.
    """
    from apps.web.coordinator_nav import invalidate_counters

    invalidate_counters()
    yield
    invalidate_counters()


def test_query_counts_unchanged_on_the_home_page(
    client_for, competition, golden, django_assert_max_num_queries
):
    with django_assert_max_num_queries(QUERY_BUDGET["/"]):
        assert client_for(competition).get("/").status_code == 200


def test_query_counts_unchanged_on_the_participant_panel(
    client_for, competition, golden, django_assert_max_num_queries
):
    client = client_for(competition)
    client.force_login(golden.participants[0].user)

    with django_assert_max_num_queries(QUERY_BUDGET["/me/"]):
        assert client.get("/me/").status_code == 200


def test_query_counts_unchanged_on_the_coordinator_dashboard(
    client_for, competition, golden, django_assert_max_num_queries
):
    client = client_for(competition)
    client.force_login(golden.coordinator)

    with django_assert_max_num_queries(QUERY_BUDGET["/coordinator/"]):
        assert client.get("/coordinator/").status_code == 200


def test_panel_query_count_does_not_grow_with_participants(
    client_for, competition, golden, django_assert_max_num_queries
):
    """Najważniejszy z testów kosztu: liczba zapytań pulpitu nie zależy od liczby uczestników.

    Sam próg złapałby dopiero regresję, która go przekroczy. Ten test pyta o to, co naprawdę
    boli na produkcji – czy koszt ekranu rośnie razem z danymi.
    """
    from apps.tenancy.tests.golden import PARTICIPANT_COUNT, add_participants

    client = client_for(competition)
    client.force_login(golden.coordinator)
    client.get("/coordinator/")  # rozgrzanie: pierwszy przebieg buduje pamięć liczników menu

    baseline = _count_queries(client, "/coordinator/")
    add_participants(competition, golden.elim, golden.problems)

    with django_assert_max_num_queries(baseline):
        assert client.get("/coordinator/").status_code == 200
    assert len(golden.participants) == PARTICIPANT_COUNT


def _count_queries(client, path: str) -> int:
    """Ile zapytań kosztuje jedno wejście na adres – do porównania „przed” z „po”."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        client.get(path)
    return len(captured.captured_queries)
