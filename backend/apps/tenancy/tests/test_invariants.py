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

from apps.accounts.consents import CONSENTS
from apps.accounts.models import PUBLIC_CODE_PREFIX, generate_public_code
from apps.cms.context_processors import FALLBACK_MENU
from apps.core.tests.query_budgets import budget
from apps.results.models import CERTIFICATE_NUMBER_PREFIX
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
    # Zaświadczenie o statusie ucznia (``apps.student_status.notifications``, v0.34.0). Do 25.09.2026
    # tych dwóch tematów tu nie było: test liczył wiersze tej tabeli, a nie stałe w kodzie, więc
    # nowy list przeszedł obok niego. Znalazł je ``test_every_subject_in_the_code_is_frozen``.
    "student_status_accepted": "Zaświadczenie o statusie ucznia zaakceptowane – Olimpiada Kwantowa",
    "student_status_rejected": "Zaświadczenie o statusie ucznia odrzucone – Olimpiada Kwantowa",
    # Zaproszenie ucznia założonego przez nauczyciela (``apps.accounts.bulk_registration``). Temat
    # stoi w pliku szablonu, a nie w stałej – z tego samego powodu nikt go dotąd nie porównywał.
    "student_invitation": "Zaproszenie do Olimpiady Kwantowej",
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
    # Przekazanie przyjętego rozwiązania na skrzynkę organizatora (prośba z 20.09.2026). Pierwszy
    # temat w serwisie z **prefiksem** konkursu (``[Olimpiada Kwantowa] ``; od 25.09.2026 niosą go
    # też listy z forum – ``EXPECTED_FORUM_SUBJECTS`` niżej): odbiorcą jest
    # komitet, który tych listów dostaje setki i filtruje je po nawiasie kwadratowym. Uzasadnienie
    # stoi przy ``apps.submissions.forwarding.subject_for``; treść sprawdza
    # ``apps/submissions/tests/test_forwarding.py``.
    "submission_forwarded": (
        f"{EXPECTED_SUBJECT_PREFIX}Nowe rozwiązanie: {STAGE_MARK} – zadanie {PROBLEM_MARK} – {CODE_MARK}"
    ),
}

#: Znacznik tematu wątku w tematach listów z forum – dana uczestnika, nie brzmienie zdania.
TITLE_MARK = "<temat>"

#: Siedem tematów listów z forum (prośba organizatora z 25.09.2026, ``apps.forum.notifications``).
#: Niosą **prefiks** konkursu z tego samego powodu, co przekazanie rozwiązania: to są listy nowe,
#: więc prefiks nie zmienia nikomu istniejącej reguły w skrzynce, a „[Olimpiada Kwantowa] Forum:”
#: jest dokładnie tym, po czym odbiorca odfiltruje całą rodzinę naraz. Brzmienia sprawdza
#: ``test_forum_subjects_unchanged`` niżej; wysyłkę – ``apps/forum/tests/test_notifications.py``.
EXPECTED_FORUM_SUBJECTS = {
    "forum_moderation": f"{EXPECTED_SUBJECT_PREFIX}Forum: wpisy czekają na moderację ({COUNT_MARK})",
    "forum_reply": f"{EXPECTED_SUBJECT_PREFIX}Forum: nowe odpowiedzi w wątku „{TITLE_MARK}”",
    "forum_reply_coordinator": (
        f"{EXPECTED_SUBJECT_PREFIX}Forum: organizator odpowiedział w wątku „{TITLE_MARK}”"
    ),
    "forum_reply_committee": f"{EXPECTED_SUBJECT_PREFIX}Forum: komitet odpowiedział w wątku „{TITLE_MARK}”",
    "forum_decisions": f"{EXPECTED_SUBJECT_PREFIX}Forum: decyzja organizatora w sprawie Twojego wpisu",
    "forum_news": f"{EXPECTED_SUBJECT_PREFIX}Forum: nowości w obserwowanych wątkach",
    "forum_daily": f"{EXPECTED_SUBJECT_PREFIX}Forum: podsumowanie dnia",
}

#: Komplet tematów wychodzących z instalacji. **Liczby tu nie ma i ma jej nie być**: do 25.09.2026
#: test porównywał długości tych słowników z literałami (11/7/7/25), więc każdy nowy list wymagał
#: poprawienia liczby w miejscu, które z tym listem nie miało nic wspólnego – a przy tym niczego nie
#: pilnował, bo liczył wiersze tabeli, a nie tematy w kodzie (trzy tematy przeszły obok niego).
#: Kompletności pilnuje dziś ``test_every_subject_in_the_code_is_frozen`` niżej.
ALL_EXPECTED_SUBJECTS = {**EXPECTED_SUBJECTS, **EXPECTED_SERVICE_SUBJECTS, **EXPECTED_FORUM_SUBJECTS}

#: Każda stała tematu listu w kodzie (nazwa z ``SUBJECT``) → klucz zamrożonego brzmienia wyżej.
#: Wariant z marką konkursu (``*_TEMPLATE`` z ``%(competition)s``) wskazuje ten sam klucz co stała
#: bez marki: to jest ten sam list, a jego brzmienie dla Konkursu #1 ma być identyczne. Wartość
#: kończąca się na ``.txt`` to ścieżka szablonu tematu – porównujemy wtedy wyrenderowany tekst.
#:
#: **Nowy list** = nowy wiersz w ``EXPECTED_*`` wyżej i nowy wiersz tutaj. Nic więcej – żadnej
#: liczby do poprawienia. Zapomniany wiersz wskaże z nazwy ``test_every_subject_in_the_code_is_frozen``.
SUBJECT_CONSTANTS = {
    "apps.accounts.activation.ACTIVATION_SUBJECT": "activation",
    "apps.accounts.activation.ACTIVATION_SUBJECT_TEMPLATE": "activation",
    "apps.accounts.activation.EMAIL_CHANGE_SUBJECT": "email_change",
    "apps.accounts.activation.EMAIL_CHANGE_SUBJECT_TEMPLATE": "email_change",
    "apps.accounts.activation.EMAIL_CHANGED_NOTICE_SUBJECT": "email_changed_notice",
    "apps.accounts.activation.EMAIL_CHANGED_NOTICE_SUBJECT_TEMPLATE": "email_changed_notice",
    "apps.accounts.bulk_registration.INVITE_SUBJECT_TEMPLATE": "student_invitation",
    "apps.accounts.guardian.GUARDIAN_SUBJECT": "guardian",
    "apps.accounts.guardian.GUARDIAN_SUBJECT_TEMPLATE": "guardian",
    "apps.accounts.guardian.GUARDIAN_CONFIRMED_SUBJECT": "guardian_confirmed",
    "apps.accounts.guardian.GUARDIAN_CONFIRMED_SUBJECT_TEMPLATE": "guardian_confirmed",
    "apps.accounts.services.INVITATION_SUBJECT": "invitation",
    "apps.accounts.services.INVITATION_SUBJECT_TEMPLATE": "invitation",
    "apps.forum.notifications.SUBJECT_MODERATION": "forum_moderation",
    "apps.forum.notifications.SUBJECT_REPLY": "forum_reply",
    "apps.forum.notifications.SUBJECT_REPLY_COORDINATOR": "forum_reply_coordinator",
    "apps.forum.notifications.SUBJECT_REPLY_COMMITTEE": "forum_reply_committee",
    "apps.forum.notifications.SUBJECT_DECISIONS": "forum_decisions",
    "apps.forum.notifications.SUBJECT_NEWS": "forum_news",
    "apps.forum.notifications.SUBJECT_DAILY": "forum_daily",
    "apps.grading.deadlines.OVERDUE_SUBJECT": "reviews_overdue",
    "apps.grading.deadlines.OVERDUE_SUBJECT_TEMPLATE": "reviews_overdue",
    "apps.grading.deadlines.DUE_SOON_SUBJECT": "reviews_due_soon",
    "apps.grading.deadlines.DUE_SOON_SUBJECT_TEMPLATE": "reviews_due_soon",
    "apps.grading.reports.REMINDER_SUBJECT": "review_reminder",
    "apps.grading.reports.REMINDER_SUBJECT_TEMPLATE": "review_reminder",
    "apps.student_status.notifications.ACCEPTED_SUBJECT": "student_status_accepted",
    "apps.student_status.notifications.ACCEPTED_SUBJECT_TEMPLATE": "student_status_accepted",
    "apps.student_status.notifications.REJECTED_SUBJECT": "student_status_rejected",
    "apps.student_status.notifications.REJECTED_SUBJECT_TEMPLATE": "student_status_rejected",
    "apps.submissions.forwarding.FORWARD_SUBJECT_TEMPLATE": "submission_forwarded",
    "apps.submissions.notifications.SUBMISSION_RECEIVED_SUBJECT": "submission_received",
    "apps.submissions.notifications.SUBMISSION_RECEIVED_SUBJECT_TEMPLATE": "submission_received",
    "apps.submissions.notifications.SUBMISSION_INFECTED_SUBJECT": "submission_infected",
    "apps.submissions.notifications.SUBMISSION_INFECTED_SUBJECT_TEMPLATE": "submission_infected",
    "apps.submissions.notifications.RESULTS_PUBLISHED_SUBJECT": "results_published",
    "apps.submissions.notifications.RESULTS_PUBLISHED_SUBJECT_TEMPLATE": "results_published",
    "apps.submissions.notifications.APPEAL_DECIDED_SUBJECT": "appeal_decided",
    "apps.submissions.notifications.APPEAL_DECIDED_SUBJECT_TEMPLATE": "appeal_decided",
    "apps.support.services.TICKET_OPENED_SUBJECT": "support_opened",
    "apps.support.services.TICKET_OPENED_SUBJECT_TEMPLATE": "support_opened",
    "apps.support.services.TICKET_ANSWERED_SUBJECT": "support_answered",
    "apps.support.services.TICKET_ANSWERED_SUBJECT_TEMPLATE": "support_answered",
}

#: Stałe z ``SUBJECT`` w nazwie, które **nie są** tematem wychodzącego listu – każda z powodem.
NOT_MAIL_SUBJECTS = {
    "apps.cms.management.commands.build_guardian_consent_pdf.PDF_SUBJECT": "metadane PDF-a (pole Subject)",
    "apps.forum.notifications.SUBJECT_TITLE_LIMIT": "limit długości tytułu wątku wstawianego do tematu",
}

#: Wartości podstawień dla Konkursu #1 i znaczniki danych konkretnego listu – te same, co w wierszach
#: ``EXPECTED_*`` (numer sprawy, etap, liczba, zadanie, kod i tytuł wątku to dane, nie brzmienie).
SUBJECT_PLACEHOLDERS = {
    "competition": "Olimpiada Kwantowa",
    "competition_genitive": "Olimpiady Kwantowej",
    "ticket": TICKET_MARK.removeprefix("#"),
    "stage": STAGE_MARK,
    "count": COUNT_MARK,
    "number": PROBLEM_MARK,
    "code": CODE_MARK,
    "title": TITLE_MARK,
}


def test_email_subjects_unchanged(settings):
    """Tematy stałych modułów, czytane ze stałych wskazanych w ``SUBJECT_CONSTANTS``.

    Słownik ``actual`` nie jest już przepisywany ręcznie: nowy temat dopisuje się w jednym miejscu
    (``EXPECTED_SUBJECTS`` + jego źródło), a nie w trzech.
    """
    actual = {}
    for dotted, key in sorted(SUBJECT_CONSTANTS.items(), key=lambda item: item[0].endswith("_TEMPLATE")):
        if key in EXPECTED_SUBJECTS:
            actual.setdefault(key, _rendered_subject(dotted))

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


def test_forum_subjects_unchanged(competition):
    """Tematy listów z forum – z prefiksem Konkursu #1, tak, jak dochodzą do skrzynki."""
    from apps.forum import notifications as forum_mail

    def subject(template, **values):
        return forum_mail._subject(template, competition, **values)

    actual = {
        "forum_moderation": subject(forum_mail.SUBJECT_MODERATION, count=COUNT_MARK),
        "forum_reply": subject(forum_mail.SUBJECT_REPLY, title=TITLE_MARK),
        "forum_reply_coordinator": subject(forum_mail.SUBJECT_REPLY_COORDINATOR, title=TITLE_MARK),
        "forum_reply_committee": subject(forum_mail.SUBJECT_REPLY_COMMITTEE, title=TITLE_MARK),
        "forum_decisions": subject(forum_mail.SUBJECT_DECISIONS),
        "forum_news": subject(forum_mail.SUBJECT_NEWS),
        "forum_daily": subject(forum_mail.SUBJECT_DAILY),
    }

    assert actual == EXPECTED_FORUM_SUBJECTS


#: Stała modułu z ``SUBJECT`` w nazwie – tak, jak stoi w pliku (``NAZWA = …`` od pierwszej kolumny).
_SUBJECT_CONSTANT = re.compile(r"^(?P<name>[A-Z0-9_]*SUBJECT[A-Z0-9_]*)\s*(?::[^=\n]+)?=", re.MULTILINE)


def _subject_constants_in_the_code() -> set[str]:
    """Kropkowane ścieżki wszystkich stałych tematów w ``apps/`` (bez testów i migracji)."""
    from pathlib import Path

    apps_dir = Path(__file__).resolve().parents[2]
    found = set()
    for path in apps_dir.rglob("*.py"):
        relative = path.relative_to(apps_dir.parent)
        if "tests" in relative.parts or "migrations" in relative.parts:
            continue
        module = ".".join(relative.with_suffix("").parts)
        for match in _SUBJECT_CONSTANT.finditer(path.read_text(encoding="utf-8")):
            found.add(f"{module}.{match['name']}")
    return found


def _rendered_subject(dotted: str) -> str:
    """Temat z danej stałej tak, jak dojdzie do skrzynki odbiorcy Konkursu #1 (bez danych listu)."""
    from django.template.loader import render_to_string
    from django.utils.module_loading import import_string

    value = import_string(dotted)
    text = render_to_string(value).strip() if str(value).endswith(".txt") else str(value)
    return text % SUBJECT_PLACEHOLDERS if "%(" in text else text


def test_every_subject_in_the_code_is_frozen():
    """Każdy temat listu w kodzie ma zamrożone brzmienie – i dla Konkursu #1 brzmi dokładnie tak.

    Test szuka stałych w **kodzie**, a nie liczy wierszy tabeli: nowy list bez wiersza w
    ``SUBJECT_CONSTANTS`` kończy się tu komunikatem z nazwą stałej. Porównanie jest per temat, więc
    dopisanie listu nie zmienia żadnej innej asercji. Prefiks ``[Olimpiada Kwantowa] `` doklejają
    listy komitetu i forum przy wysyłce (``_subject``, ``subject_for``) – stała go nie niesie.
    """
    in_code = _subject_constants_in_the_code()

    unregistered = sorted(in_code - set(SUBJECT_CONSTANTS) - set(NOT_MAIL_SUBJECTS))
    assert not unregistered, (
        f"Tematy listów bez zamrożonego brzmienia: {unregistered}. Dopisz brzmienie do EXPECTED_* "
        "i stałą do SUBJECT_CONSTANTS (albo do NOT_MAIL_SUBJECTS z powodem, jeśli to nie jest temat listu)."
    )
    stale = sorted((set(SUBJECT_CONSTANTS) | set(NOT_MAIL_SUBJECTS)) - in_code)
    assert not stale, f"Wiersze dla stałych, których w kodzie już nie ma: {stale}"

    for dotted, key in SUBJECT_CONSTANTS.items():
        expected = ALL_EXPECTED_SUBJECTS[key]
        rendered = _rendered_subject(dotted)
        if expected.startswith(EXPECTED_SUBJECT_PREFIX) and not rendered.startswith(EXPECTED_SUBJECT_PREFIX):
            rendered = EXPECTED_SUBJECT_PREFIX + rendered
        assert rendered == expected, dotted


def test_every_translatable_subject_has_an_english_version():
    """Temat przepuszczany przez ``gettext`` (stała leniwa albo szablon ``.txt``) ma wersję angielską.

    Sprawdzane per temat, więc nowy list dostaje tę asercję sam, przez wiersz w
    ``SUBJECT_CONSTANTS`` – bez liczby do poprawienia. Tematy zwykłymi napisami (komitet,
    recenzenci, zgłoszenia) są świadomie tylko po polsku i tej reguły nie dotyczą.
    """
    from django.utils.functional import Promise
    from django.utils.module_loading import import_string
    from django.utils.translation import override

    untranslated = []
    for dotted in SUBJECT_CONSTANTS:
        value = import_string(dotted)
        if not (isinstance(value, Promise) or str(value).endswith(".txt")):
            continue
        with override("pl"):
            polish = _rendered_subject(dotted)
        with override("en"):
            english = _rendered_subject(dotted)
        if english == polish:
            untranslated.append(dotted)

    assert not untranslated, f"Tematy bez tłumaczenia w locale/en/LC_MESSAGES/django.po: {untranslated}"


def test_every_frozen_subject_is_distinct_and_has_a_source():
    """Żaden temat nie jest pusty ani nie powtarza się pod dwoma kluczami – i każdy ma źródło.

    Powtórzenie znaczyłoby, że dwa różne zdarzenia dają w skrzynce ten sam wiersz i nie da się ich
    rozróżnić filtrem. Wiersz bez źródła w ``SUBJECT_CONSTANTS`` jest dozwolony wyłącznie dla
    tematów składanych w serwisie bez stałej (rozmowy kwalifikacyjne) – te czyta z ``outbox``
    ``test_branding.py``.
    """
    assert all(ALL_EXPECTED_SUBJECTS.values())
    assert len(set(ALL_EXPECTED_SUBJECTS.values())) == len(ALL_EXPECTED_SUBJECTS)
    without_source = set(ALL_EXPECTED_SUBJECTS) - set(SUBJECT_CONSTANTS.values())
    assert without_source <= {"interview_booked", "interview_reminder"}, sorted(without_source)


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


def test_menu_matches_seeded_tree(competition):
    """Drzewo Konkursu #1 (migracja ``cms.0002``) ma zostać dokładnie takie, jakie jest.

    Nagłówek dziś filtruje i przestawia tę listę (domek, ``HIDDEN_MENU_SLUGS``, ``MENU_ORDER`` –
    decyzje organizatora z 21.09.2026, testowane osobno w ``apps/cms/tests`` i mogące się zmienić
    przy kolejnej uwadze), więc inwariant czytamy z surowego drzewa stron w kolejności
    rodzeństwa – to ono ma zostać stałe, niezależnie od tego, co z niego akurat pokazuje nagłówek.
    """
    from wagtail.models import Page

    titles = tuple(
        Page.objects.live()
        .in_menu()
        .child_of(competition.site.root_page)
        .order_by("path")
        .values_list("title", flat=True)
    )

    assert titles == EXPECTED_MENU
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
#: Wartości (i historia każdego podniesienia) stoją w **jednej** tabeli budżetów całej suity –
#: ``apps/core/tests/query_budgets.py``. Tutaj jest tylko wybór ekranów tego pliku.
QUERY_BUDGET = {path: budget(path) for path in ("/", "/me/", "/coordinator/")}


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
    from apps.accounts.supervisors import reset_registration_cache
    from apps.cms import analytics
    from apps.web.coordinator_nav import invalidate_counters

    # Drugi licznik z pamięcią procesu: odpowiedź „czy witryna ma identyfikator GA4”
    # (``apps.cms.analytics``), o którą pyta nagłówek CSP przy każdej odpowiedzi HTML. Zapamiętana
    # przez wcześniejszy test oszczędzała tu jedno zapytanie, więc wynik zależał od tego, co
    # biegło przed tym modułem w tym samym procesie – a od podziału testów na shardy w CI
    # (v0.27.2) kolejność zmienia się z każdym dołożonym testem. Wyszło to 21.09.2026: ten sam
    # kod dawał 46 zapytań w całym zbiorze i 47 w pojedynkę. Mierzymy zawsze **na zimno**.
    #
    # Trzeci: przełącznik rejestracji opiekunów szkolnych (``apps.accounts.supervisors``), od
    # 22.09.2026 czytany bezwarunkowo przez menu CMS na każdej stronie (``_supervisor_menu_item``) –
    # ten sam powód, ta sama pamięć trzydziestosekundowa na proces.
    analytics._cache.clear()
    invalidate_counters()
    reset_registration_cache()
    yield
    invalidate_counters()
    analytics._cache.clear()
    reset_registration_cache()


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


def test_workshop_materials_link_does_not_change_the_participant_panel_budget(
    client_for, competition, golden, django_assert_max_num_queries
):
    """Odnośnik „Materiały z warsztatów” (pasek konta i kafel na ``/me/``) w budżecie panelu.

    Dwie strony tej samej prawdy. Konkurs #1 z domyślnymi przełącznikami (flaga
    ``workshop_materials`` wyłączona) nie pyta o materiały **ani razu** – ``QUERY_BUDGET["/me/"]``
    zostaje bez zmian, co sprawdza test wyżej. Konkurs z włączoną flagą płaci jedno ``EXISTS``
    przy pierwszym żądaniu po zimnym starcie, a potem czyta odpowiedź z pamięci podręcznej
    (``apps.workshop_materials.availability``) – rozgrzany panel mieści się w tym samym budżecie
    i nie dotyka tabeli materiałów.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.workshop_materials.models import MaterialStatus, WorkshopMaterial

    client = client_for(competition)
    client.force_login(golden.participants[0].user)
    with CaptureQueriesContext(connection) as cold_flag_off:
        assert client.get("/me/").status_code == 200
    assert not [q for q in cold_flag_off.captured_queries if "workshop_materials_" in q["sql"]]

    competition.feature_flags = {**(competition.feature_flags or {}), "workshop_materials": True}
    competition.save(update_fields=["feature_flags"])
    WorkshopMaterial.objects.create(
        competition=competition,
        workshop_key="2026-11-12-kubity",
        kind="link",
        title="Nagranie",
        url="https://example.com/nagranie",
        status=MaterialStatus.READY,
        is_published=True,
    )
    assert "/warsztaty/materialy/" in client.get("/me/").content.decode()  # rozgrzanie pamięci

    with django_assert_max_num_queries(QUERY_BUDGET["/me/"]):
        with CaptureQueriesContext(connection) as warm:
            assert client.get("/me/").status_code == 200
    assert not [q for q in warm.captured_queries if "workshop_materials_" in q["sql"]]


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
