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
    ("terms_consent", "TERMS", "z 20 września 2026", True, False),
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

#: Znaczniki podstawień w tematach składanych w serwisie. Porównujemy **wzorzec**, a nie wynik:
#: numer sprawy i nazwa etapu są danymi konkretnego listu, a przedmiotem tego testu jest brzmienie
#: zdania, które zostaje w skrzynce odbiorcy i w jego regułach filtrowania.
TICKET_MARK = "#<n>"
STAGE_MARK = "<etap>"
COUNT_MARK = "<n>"
#: Znaczniki tematu listu z przekazanym rozwiązaniem: numer zadania i kod uczestnika.
PROBLEM_MARK = "<nr>"
CODE_MARK = "<kod>"

#: Prefiks doklejany przez Django do tematów wysyłanych przez ``mail_admins``/``send_mail``
#: z ``subject_prefix``. Konkurs #1 dostał go w migracji ``tenancy.0002`` jako własną wartość.
#: Stoi **nad** listą tematów, bo jeden z nich (przekazanie rozwiązania) sam go niesie.
EXPECTED_SUBJECT_PREFIX = "[Olimpiada Kwantowa] "

#: Sześć tematów, których do etapu 2 **nie pilnował żaden test** (``docs/UNIWERSALNY-ETAP-2.md``
#: § 1.1.1 i § 5.4). Różnią się od jedenastu wyżej jedną rzeczą: nie są stałą modułu, tylko
#: powstają w serwisie z podstawieniem, więc jedynym sposobem odczytania ich takimi, jakie
#: dochodzą do człowieka, jest wysłanie listu i przeczytanie go z ``django.core.mail.outbox``.
#: Robi to ``apps/tenancy/tests/test_branding.py``; tutaj stoi sama zamrożona wartość, bo to ten
#: plik jest listą „co w Konkursie #1 ma zostać dokładnie takie, jakie jest”.
#:
#: Pozycji jest siedem, a wierszy tabeli § 1.1.1 dochodzi pięć: przypomnienie o recenzjach po
#: terminie ma **dwa** brzmienia (``apps/grading/deadlines.py:149–152``) i oba są tematem listu,
#: który ktoś dostanie – zamrożenie jednego z nich zostawiałoby drugi bez żadnej asercji. Siódma
#: pozycja (przekazanie rozwiązania) jest młodsza od tej tabeli: powstała na prośbę organizatora
#: z 20.09.2026 i jako jedyna nie idzie do uczestnika, tylko do komitetu.
EXPECTED_SERVICE_SUBJECTS = {
    "support_opened": f"Nowe zgłoszenie {TICKET_MARK} – Olimpiada Kwantowa",
    "support_answered": f"Odpowiedź na zgłoszenie {TICKET_MARK} – Olimpiada Kwantowa",
    "interview_booked": f"Termin rozmowy kwalifikacyjnej: {STAGE_MARK}",
    "interview_reminder": f"Jutro rozmowa kwalifikacyjna: {STAGE_MARK}",
    "reviews_overdue": f"Olimpiada Kwantowa: {COUNT_MARK} recenzji po terminie",
    "reviews_due_soon": "Olimpiada Kwantowa: zbliża się termin recenzji",
    # Przekazanie przyjętego rozwiązania na skrzynkę organizatora (prośba z 20.09.2026). Jedyny
    # temat w całym serwisie z **prefiksem** konkursu (``[Olimpiada Kwantowa] ``): odbiorcą jest
    # komitet, który tych listów dostaje setki i filtruje je po nawiasie kwadratowym. Uzasadnienie
    # stoi przy ``apps.submissions.forwarding.subject_for``; treść sprawdza
    # ``apps/submissions/tests/test_forwarding.py``.
    "submission_forwarded": (
        f"{EXPECTED_SUBJECT_PREFIX}Nowe rozwiązanie: {STAGE_MARK} – zadanie {PROBLEM_MARK} – {CODE_MARK}"
    ),
}

#: Komplet tematów wychodzących z instalacji – siedemnaście rodzajów listu, z których jeden
#: (przypomnienie o recenzjach) niesie dwa brzmienia. Stała jest jedna, żeby dopisanie
#: dziewiętnastego listu bez wiersza w teście było widoczne w jednym miejscu.
ALL_EXPECTED_SUBJECTS = {**EXPECTED_SUBJECTS, **EXPECTED_SERVICE_SUBJECTS}


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


def test_every_outgoing_subject_is_frozen():
    """Osiemnaście napisów na siedemnaście rodzajów listu – i ani jednego więcej bez wiersza tutaj.

    Test pilnuje **listy**, a nie treści: treści pilnują ``test_email_subjects_unchanged`` (stałe
    modułów), ``test_branding.py`` (tematy składane w serwisie, czytane z ``mail.outbox``)
    i ``apps/submissions/tests/test_forwarding.py`` (przekazanie rozwiązania). Bez tego liczenia
    dopisanie kolejnego rodzaju listu byłoby zmianą, po której nadal wszystko przechodzi – bo
    nowego tematu po prostu nikt by nie porównywał.
    """
    assert len(EXPECTED_SUBJECTS) == 11
    assert len(EXPECTED_SERVICE_SUBJECTS) == 7
    assert len(ALL_EXPECTED_SUBJECTS) == 18
    # Żaden temat nie jest pusty i żaden nie powtarza się pod dwoma kluczami: powtórzenie znaczyłoby,
    # że dwa różne zdarzenia dają w skrzynce ten sam wiersz i nie da się ich rozróżnić filtrem.
    assert all(ALL_EXPECTED_SUBJECTS.values())
    assert len(set(ALL_EXPECTED_SUBJECTS.values())) == len(ALL_EXPECTED_SUBJECTS)


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


# --- 8. podpisy listów ------------------------------------------------------------------------------

#: Stopka listu Konkursu #1 – trzy wiersze, w tej kolejności. Podpis jest tym, po czym odbiorca
#: poznaje nadawcę, gdy temat zginie w podglądzie skrzynki, a zdanie o skrzynce bez odbioru jest
#: **informacją prawną**: pisząc na ten adres, nikt nie dostanie odpowiedzi.
SIGNATURE_LINES = (
    "--",
    "Olimpiada Kwantowa",
    "Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać.",
)

#: Po jednym wpisie na rodzaj listu, mimo że wszystkie niosą ten sam napis. Wspólna stała
#: z jednym testem „gdzieś stoi podpis” przepuściłaby list, który podpis **zgubił** – a to jest
#: dokładnie ta regresja, którą T9 może zrobić, przepisując pięć miejsc na jedno wywołanie.
EXPECTED_SIGNATURES = {
    "activation": SIGNATURE_LINES,
    "email_change": SIGNATURE_LINES,
    "email_changed_notice": SIGNATURE_LINES,
    "guardian": SIGNATURE_LINES,
    "guardian_confirmed": SIGNATURE_LINES,
    "invitation": SIGNATURE_LINES,
}


def _signature_of(message: str) -> tuple[str, ...]:
    """Trzy ostatnie wiersze listu – podpis tak, jak zobaczy go odbiorca."""
    return tuple(message.splitlines()[-3:])


def test_email_signatures_unchanged():
    """Sześć listów składanych bez bazy; pozostałe trzy sprawdza ``test_branding.py`` z ``outbox``.

    Podpisy porównujemy na **złożonej treści**, a nie na stałej w module: napis jest dziś literałem
    w pięciu plikach (§ 1.1.1) i to, że pięć literałów brzmi tak samo, jest właśnie tym, czego
    ten test pilnuje.
    """
    from django.utils import timezone

    from apps.accounts import activation, guardian
    from apps.accounts.services import invitation_message

    link = "https://kwantowa.invalid/aktywuj/token/"
    actual = {
        "activation": _signature_of(activation.activation_message(link)),
        "email_change": _signature_of(activation.email_change_message(link, "nowy@example.invalid")),
        "email_changed_notice": _signature_of(activation.email_changed_notice("nowy@example.invalid")),
        "guardian": _signature_of(guardian.request_message(link, "Uczestnik", "Liceum testowe")),
        "guardian_confirmed": _signature_of(guardian.confirmed_message("opiekun@example.invalid")),
        "invitation": _signature_of(invitation_message("KODKODKOD", link=link, expires_at=timezone.now())),
    }

    assert actual == EXPECTED_SIGNATURES


# --- 9. napisy dokumentów --------------------------------------------------------------------------

#: Tytuł na papierze, per rodzaj dokumentu (``apps/results/certificates.py``). Dyplom bywa
#: przepisywany do dziennika i do dorobku naukowego, a jego tytuł cytuje się w piśmie – zmiana
#: znaczy, że dwa dokumenty tej samej olimpiady nazywają się inaczej.
EXPECTED_DOCUMENT_TITLES = {
    "LAUREAT": "Dyplom laureata",
    "FINALISTA": "Dyplom finalisty",
    "UCZESTNIK": "Zaświadczenie o udziale",
    "OPIEKUN": "Zaświadczenie dla opiekuna",
    "WARSZTATY": "Zaświadczenie o udziale w warsztatach",
}

#: Zdanie pod nazwiskiem – właściwa treść dokumentu. Tu zmiana jednego słowa zmienia to, co
#: dokument poświadcza, więc stała jest tu z dokładnie tego samego powodu, co wersje zgód.
EXPECTED_DOCUMENT_STATEMENTS = {
    "LAUREAT": "uzyskał(a) tytuł laureata Olimpiady Kwantowej",
    "FINALISTA": "uzyskał(a) tytuł finalisty Olimpiady Kwantowej",
    "UCZESTNIK": "brał(a) udział w Olimpiadzie Kwantowej",
    "OPIEKUN": "sprawował(a) opiekę nad uczestnikami Olimpiady Kwantowej",
    "WARSZTATY": "uczestniczył(a) w warsztatach online Olimpiady Kwantowej",
}

#: Nagłówek listy warsztatów i nazwa organu nad kreską podpisu. Linia podpisu wchodzi na **każdy**
#: dokument wystawiony bez szablonu graficznego (``_draw_signatures``), czyli na wszystkie
#: dokumenty Konkursu #1 sprzed wprowadzenia szablonów.
EXPECTED_WORKSHOP_LIST_HEADING = "Tematy zajęć:"
EXPECTED_SIGNATURE_LINE = "Przewodniczący Komitetu Sterującego Olimpiady Kwantowej"

#: Autor w metadanych PDF-a. Widać go w podglądzie pliku **przed** otwarciem dokumentu i zostaje
#: w nim na zawsze – PDF-a nikt nie przechowuje, więc powstaje przy każdym pobraniu na nowo.
EXPECTED_PDF_AUTHOR = "Olimpiada Kwantowa"


def test_document_strings_unchanged():
    from apps.results.certificates import (
        DOCUMENT_STATEMENTS,
        DOCUMENT_TITLES,
        SIGNATURE_LINE,
        WORKSHOP_LIST_HEADING,
    )

    assert {str(kind): title for kind, title in DOCUMENT_TITLES.items()} == EXPECTED_DOCUMENT_TITLES
    assert {
        str(kind): statement for kind, statement in DOCUMENT_STATEMENTS.items()
    } == EXPECTED_DOCUMENT_STATEMENTS
    assert WORKSHOP_LIST_HEADING == EXPECTED_WORKSHOP_LIST_HEADING
    assert SIGNATURE_LINE == EXPECTED_SIGNATURE_LINE


# --- 10. kalendarz ----------------------------------------------------------------------------------

#: Cztery napisy pliku ``.ics`` uczestnika (``apps/cms/calendar.py``). ``uid_domain`` jest z nich
#: najważniejszy: ``UID`` jest **kluczem wydarzenia** w kliencie kalendarza, więc jego zmiana nie
#: poprawia wpisu, tylko dokłada drugi obok istniejącego – i uczestnik ma odtąd każdy termin
#: podwójnie, bez żadnego sposobu, żeby to cofnąć zdalnie.
EXPECTED_CALENDAR = {
    "uid_domain": "olimpiadakwantowa.pl",
    "prodid": "-//Olimpiada Kwantowa//Kalendarz uczestnika//PL",
    "calname": "Olimpiada Kwantowa",
    "filename": "olimpiada-kwantowa.ics",
}


def test_calendar_strings_unchanged():
    """Trzy stałe modułu i nazwa kalendarza wpisana wprost w nagłówek pliku.

    ``X-WR-CALNAME`` nie jest stałą (stoi w ``calendar_ics``), więc czytamy go z **wyniku**: pusty
    kalendarz też ma nagłówek i to on jest tu przedmiotem, a nie lista wydarzeń.
    """
    from apps.cms.calendar import ICS_FILENAME, PRODID, UID_DOMAIN, calendar_ics

    header = calendar_ics([])

    assert UID_DOMAIN == EXPECTED_CALENDAR["uid_domain"]
    assert PRODID == EXPECTED_CALENDAR["prodid"]
    assert ICS_FILENAME == EXPECTED_CALENDAR["filename"]
    assert f"X-WR-CALNAME:{EXPECTED_CALENDAR['calname']}\r\n" in header
    assert f"PRODID:{EXPECTED_CALENDAR['prodid']}\r\n" in header


# --- 11. pozostałe napisy marki ----------------------------------------------------------------------

#: Komunikaty CAPTCHA (``apps/web/captcha.py``). Czyta je człowiek, który **nie może się
#: zarejestrować** – adres w nich jest jedyną drogą, jaka mu zostaje, więc ma być adresem
#: organizatora tego konkursu, a nie napisem, który zniknął przy okazji.
EXPECTED_CAPTCHA_REJECTED = (
    "Nie udało się potwierdzić, że formularz wypełnił człowiek. Wyślij go jeszcze raz, "
    "a jeśli błąd się powtarza – napisz do contact@qaif.org."
)
EXPECTED_CAPTCHA_HELP = (
    "Wpisz wynik działania z obrazka. Nie widzisz obrazka (czytnik ekranu, brak grafiki)? "
    "Napisz do contact@qaif.org – konto założymy ręcznie."
)
EXPECTED_CAPTCHA_LABEL = "Zabezpieczenie antyspamowe"

#: Domena adresów po anonimizacji konta (``apps/accounts/profile.py``). Adres anonimowy jest
#: **daną**, a nie konfiguracją: stoi w kolumnie logowania (``USERNAME_FIELD``) pod więzem
#: unikalności, więc kont już zanonimizowanych nie rusza żadna migracja i nigdy nie ruszy.
EXPECTED_ANONYMISED_EMAIL_DOMAIN = "invalid.olimpiadakwantowa.pl"

#: Strona 500. Renderuje się **bez bazy i bez procesorów kontekstu** – i to jest jej sens, więc
#: napisy są tu jedynym, co da się o niej zamrozić.
EXPECTED_ERROR_PAGE_TITLE = "Błąd serwera – Olimpiada Kwantowa"
EXPECTED_ERROR_PAGE_HEADING = "Coś poszło nie tak po naszej stronie"
EXPECTED_ERROR_PAGE_CONTACT = "mailto:contact@qaif.org"


def test_captcha_messages_unchanged():
    from apps.web.captcha import CAPTCHA_HELP_TEXT, CAPTCHA_LABEL, REJECTED_MESSAGE

    assert REJECTED_MESSAGE == EXPECTED_CAPTCHA_REJECTED
    assert CAPTCHA_HELP_TEXT == EXPECTED_CAPTCHA_HELP
    assert CAPTCHA_LABEL == EXPECTED_CAPTCHA_LABEL


def test_anonymised_email_domain_unchanged():
    from apps.accounts.profile import ANONYMISED_EMAIL_DOMAIN

    assert ANONYMISED_EMAIL_DOMAIN == EXPECTED_ANONYMISED_EMAIL_DOMAIN


def test_error_page_strings_unchanged():
    from django.template.loader import render_to_string

    html = render_to_string("500.html")

    assert f"<title>{EXPECTED_ERROR_PAGE_TITLE}</title>" in html
    assert EXPECTED_ERROR_PAGE_HEADING in html
    assert EXPECTED_ERROR_PAGE_CONTACT in html
