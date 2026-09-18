"""Niezmienność marki Konkursu #1 **po wyniku**, a nie po stałej.

Ten plik jest drugą połową zadania T16 (``docs/UNIWERSALNY-ETAP-2.md`` § 4.4). Pierwsza połowa
(``test_invariants.py``) zamraża napisy tam, gdzie są stałą modułu i da się je porównać bez
uruchamiania czegokolwiek. Tutaj są te, których **nie da się** tak porównać, bo powstają dopiero
w chwili wysyłki albo renderowania:

- sześć tematów listów składanych w serwisie z podstawieniem (§ 1.1.1) – czytane z
  ``django.core.mail.outbox``, czyli dokładnie tak, jak zobaczy je odbiorca,
- podpisy trzech listów, których treść wymaga bazy,
- metadane i linia podpisu składanego PDF-a,
- nagłówki pliku ``.ics`` pobranego z panelu uczestnika,
- zestaw zgód tak, jak **wydają go trzy powierzchnie naraz**: formularz ``/register/``,
  ``GET /api/auth/consents/`` i panel uczestnika,
- komunikat CAPTCHA na formularzu rejestracji,
- adres konta po anonimizacji.

**Dlaczego przez wynik, a nie przez import modułu marki.** Etap 2 przenosi te napisy za flagę
``competition_branding_in_mail`` i za nowy moduł ``apps/tenancy/branding.py``. Test, który
importowałby ten moduł, sprawdzałby nową drogę – a pytanie brzmi odwrotnie: czy Konkurs #1
z ``feature_flags`` **bez** tej flagi dostaje to samo, co dostawał. Dlatego wszystkie asercje niżej
patrzą wyłącznie na to, co wychodzi z systemu do człowieka.

**Jak zmienić którąkolwiek z tych wartości świadomie:** tak samo jak w ``test_invariants.py`` –
stała zmienia się w tym samym commicie, co zachowanie, a w opisie commitu stoi, kto zmianę
zamówił. Zgody wymagają dodatkowo decyzji organizatora, bo są oświadczeniem złożonym pod
konkretnym dokumentem.
"""

from __future__ import annotations

import re
from io import BytesIO

import pytest
from django.core import mail

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ParticipantFactory
from apps.tenancy.tests.factories import grant_membership
from apps.tenancy.tests.golden import (
    answer_support_ticket,
    assign_pending_review,
    book_interview,
    build_golden,
    open_support_ticket,
)
from apps.tenancy.tests.test_invariants import (
    COUNT_MARK,
    EXPECTED_CALENDAR,
    EXPECTED_CAPTCHA_HELP,
    EXPECTED_CAPTCHA_LABEL,
    EXPECTED_PDF_AUTHOR,
    EXPECTED_SERVICE_SUBJECTS,
    EXPECTED_SIGNATURE_LINE,
    SIGNATURE_LINES,
    STAGE_MARK,
    TICKET_MARK,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def golden(competition):
    """Świat w kształcie produkcji – ten sam, na którym mierzą się progi zapytań."""
    return build_golden(competition)


def _signature_of(message: str) -> tuple[str, ...]:
    """Trzy ostatnie wiersze listu – podpis tak, jak zobaczy go odbiorca."""
    return tuple(message.splitlines()[-3:])


def _without_ticket_number(subject: str) -> str:
    """Numer sprawy na znacznik: przedmiotem jest zdanie tematu, a nie który to z kolei wpis."""
    return re.sub(r"#\d+", TICKET_MARK, subject)


def _without_count(subject: str) -> str:
    """Liczba zaległych recenzji na znacznik – z tego samego powodu, co numer sprawy."""
    return re.sub(r"\d+", COUNT_MARK, subject)


# --- listy składane w serwisie ---------------------------------------------------------------------


def test_support_ticket_subjects_unchanged(competition, golden, django_capture_on_commit_callbacks):
    """Dwa tematy zgłoszeń (``apps/support/services.py:238,267``) – pierwszy raz objęte testem.

    ``django_capture_on_commit_callbacks`` jest tu warunkiem sprawdzania czegokolwiek: obie
    wysyłki są zakolejkowane **po commicie**, a w teście transakcja nigdy się nie domyka. Bez tego
    ``outbox`` zostałby pusty, a test przeszedłby, nie przeczytawszy ani jednego listu.
    """
    with django_capture_on_commit_callbacks(execute=True):
        ticket = open_support_ticket(golden)

    assert _without_ticket_number(mail.outbox[-1].subject) == EXPECTED_SERVICE_SUBJECTS["support_opened"]
    assert str(ticket.pk) in mail.outbox[-1].subject

    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        answer_support_ticket(golden, ticket)

    assert _without_ticket_number(mail.outbox[-1].subject) == EXPECTED_SERVICE_SUBJECTS["support_answered"]


def test_interview_subjects_and_signature_unchanged(competition, golden, django_capture_on_commit_callbacks):
    """Potwierdzenie terminu i przypomnienie o rozmowie – dwa tematy z nazwą etapu w środku.

    Nazwa etapu wchodzi do tematu z ``Stage.display_name``, czyli z **danych** – i to jest
    w porządku, bo etapy nazywa organizator. Zamrożone jest zdanie wokół niej.
    """
    with django_capture_on_commit_callbacks(execute=True):
        book_interview(golden)

    stage_name = golden.district.display_name
    confirmation = mail.outbox[-1]
    assert (
        confirmation.subject.replace(stage_name, STAGE_MARK) == EXPECTED_SERVICE_SUBJECTS["interview_booked"]
    )

    from apps.competitions.video import send_interview_reminders

    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert send_interview_reminders(competition=competition) == 1

    reminder = mail.outbox[-1]
    assert reminder.subject.replace(stage_name, STAGE_MARK) == EXPECTED_SERVICE_SUBJECTS["interview_reminder"]
    # Przypomnienie o rozmowie jest jednym z pięciu listów z podpisem (§ 1.1.1) – i jedynym
    # z nich, którego treść wymaga bazy, więc podpis sprawdza się tutaj, a nie w stałych.
    assert _signature_of(reminder.body) == SIGNATURE_LINES


@pytest.mark.parametrize(
    ("overdue", "key"),
    [(True, "reviews_overdue"), (False, "reviews_due_soon")],
)
def test_review_reminder_subjects_unchanged(competition, golden, overdue, key):
    """Dwa brzmienia przypomnienia o recenzjach (``apps/grading/deadlines.py:149–152``).

    Przebieg idzie przez zadanie okresowe, a nie przez wywołanie ``reminder_message``: temat
    zależy od **doboru** recenzji (ile jest po terminie), więc sprawdzenie samej funkcji
    składającej napis pomijałoby połowę decyzji, która o tym temacie rozstrzyga.
    """
    from apps.grading.tasks import remind_overdue_reviews

    assign_pending_review(golden, overdue=overdue)
    mail.outbox.clear()

    remind_overdue_reviews()

    assert len(mail.outbox) == 1
    assert _without_count(mail.outbox[-1].subject) == EXPECTED_SERVICE_SUBJECTS[key]


def test_signatures_of_the_letters_built_from_the_database(competition, golden):
    """Dwa pozostałe podpisy: przypomnienie koordynatora dla recenzenta i powiadomienia o pracach.

    Oba składają treść z etapu, więc nie dają się sprawdzić bez bazy – a są podpisane tym samym
    napisem, co listy z ``test_invariants.py``. Gdyby T9 przepisało pięć miejsc na jedno wywołanie
    i zgubiło przy tym szóste, zobaczy to ten test.
    """
    from apps.grading.reports import reminder_message
    from apps.submissions.notifications import results_published_message

    assert _signature_of(reminder_message(golden.elim, 3, 1)) == SIGNATURE_LINES
    assert (
        _signature_of(results_published_message(golden.elim, "https://kwantowa.invalid/wyniki/", ""))
        == SIGNATURE_LINES
    )


# --- dokumenty --------------------------------------------------------------------------------------


def test_certificate_pdf_carries_the_frozen_strings(competition):
    """Autor w metadanych i linia podpisu na papierze – jedno i drugie z gotowego pliku.

    PDF-a nikt nie przechowuje (``apps/results/certificates.py``): powstaje przy każdym pobraniu,
    więc zmiana napisu zmienia **także dokumenty wydane dawniej**, które ktoś trzyma w ręku.
    Dlatego sprawdzamy plik, a nie stałą, z której plik powstał.
    """
    from pypdf import PdfReader

    from apps.results.certificates import compose_pdf, sample_content
    from apps.results.models import CertificateKind

    raw = compose_pdf(sample_content(CertificateKind.LAUREAT))
    reader = PdfReader(BytesIO(raw))

    assert reader.metadata.author == EXPECTED_PDF_AUTHOR
    # Dokument bez szablonu graficznego dostaje jeden blok podpisu z nazwą organu – tak wygląda
    # każdy dokument Konkursu #1 wystawiony przed wprowadzeniem szablonów.
    assert EXPECTED_SIGNATURE_LINE in reader.pages[0].extract_text()


# --- kalendarz ---------------------------------------------------------------------------------------


def test_participant_calendar_headers_unchanged(client_for, competition, golden):
    """Plik ``.ics`` pobrany z panelu: nazwa pliku, ``PRODID``, ``X-WR-CALNAME`` i człon ``UID``.

    ``UID`` jest kluczem wydarzenia w kliencie kalendarza. Jego zmiana nie poprawia istniejącego
    wpisu – dokłada drugi obok, a uczestnik nie ma jak tego cofnąć: plik bywa **zasubskrybowany**,
    więc stary wpis zostaje u niego na zawsze.
    """
    client = client_for(competition)
    client.force_login(golden.participants[0].user)

    response = client.get("/me/calendar.ics")
    body = response.content.decode("utf-8")
    uids = [line for line in body.split("\r\n") if line.startswith("UID:")]

    assert response.status_code == 200
    assert response["Content-Disposition"] == f'attachment; filename="{EXPECTED_CALENDAR["filename"]}"'
    assert f"PRODID:{EXPECTED_CALENDAR['prodid']}" in body
    assert f"X-WR-CALNAME:{EXPECTED_CALENDAR['calname']}" in body
    assert uids, "Kalendarz bez ani jednego wydarzenia nie sprawdziłby członu UID."
    assert all(uid.endswith(f"@{EXPECTED_CALENDAR['uid_domain']}") for uid in uids)


# --- zgody ---------------------------------------------------------------------------------------------

#: Zestaw zgód Konkursu #1 **w całości**: rodzaj, nazwa pola, brzmienie po podstawieniu, dokument,
#: wersja i reguła wymagalności. ``test_invariants.test_consent_labels_unchanged`` zamraża z tego
#: pięć kolumn; tutaj stoi reszta, czyli to, co człowiek naprawdę czyta przy checkboksie.
#:
#: Nazwa organizatora jest podstawiona **wprost** (``Fundacja Quantum AI``), a nie odczytana
#: z ustawień: odczyt porównywałby kod z samym sobą i przeszedłby także wtedy, gdy zgoda przestaje
#: wymieniać administratora danych. Adresy dokumentów są kanoniczne (``/dokumenty/<slug>/``), bo
#: w bazie testowej nie ma jeszcze stron dokumentów – i to jest ten sam odwrót, który widzi
#: uczestnik świeżej instalacji.
EXPECTED_CONSENT_SET = (
    {
        "kind": "TERMS",
        "field": "terms_consent",
        "text": "Zapoznałem/-am się z Regulaminem Olimpiady Kwantowej i akceptuję jego postanowienia.",
        "document_slug": "regulamin",
        "document_url": "/dokumenty/regulamin/",
        "version": "1.0 z 2 września 2026",
        "required": True,
        "required_for_minor": False,
        "help_text": "",
    },
    {
        "kind": "PRIVACY",
        "field": "gdpr_consent",
        "text": (
            "Wyrażam zgodę na przetwarzanie moich danych osobowych przez organizatora – "
            "Fundacja Quantum AI – w celu organizacji i przeprowadzenia Olimpiady Kwantowej oraz "
            "oświadczam, że zapoznałem/-am się z Polityką RODO (klauzulą informacyjną)."
        ),
        "document_slug": "rodo",
        "document_url": "/dokumenty/rodo/",
        "version": "1.0 z 22 lipca 2026",
        "required": True,
        "required_for_minor": False,
        "help_text": "",
    },
    {
        "kind": "GUARDIAN",
        "field": "guardian_consent",
        "text": (
            "Oświadczam, że mój rodzic / opiekun prawny zapoznał się z Zgodą rodzica lub opiekuna "
            "prawnego i wyraża zgodę na mój udział w Olimpiadzie oraz na przetwarzanie moich "
            "danych osobowych."
        ),
        "document_slug": "zgoda-opiekuna",
        "document_url": "/dokumenty/zgoda-opiekuna/",
        "version": "0.1 (projekt) z 10 września 2026",
        "required": False,
        "required_for_minor": True,
        "help_text": "wymagane dla osób niepełnoletnich",
    },
    {
        "kind": "PUBLISH_NAME",
        "field": "publish_name_consent",
        "text": (
            "Wyrażam zgodę na publikację mojego imienia i nazwiska (wraz ze szkołą) na listach "
            "wyników i laureatów."
        ),
        "document_slug": "",
        "document_url": "",
        "version": "1.0",
        "required": False,
        "required_for_minor": False,
        "help_text": "dobrowolne – bez tej zgody w tabelach wyników zostaje sam kod uczestnika",
    },
)

#: Etykieta zgody jako HTML – to, co stoi przy checkboksie na ``/register/`` i w panelu uczestnika.
#: Odnośnik otwiera się w nowej karcie: przeczytanie regulaminu nie może kosztować utraty
#: wypełnionego formularza.
EXPECTED_CONSENT_LABELS = {
    "TERMS": (
        'Zapoznałem/-am się z <a href="/dokumenty/regulamin/" target="_blank" rel="noopener">'
        "Regulaminem Olimpiady Kwantowej</a> i akceptuję jego postanowienia."
    ),
    "PRIVACY": (
        "Wyrażam zgodę na przetwarzanie moich danych osobowych przez organizatora – "
        "Fundacja Quantum AI – w celu organizacji i przeprowadzenia Olimpiady Kwantowej oraz "
        'oświadczam, że zapoznałem/-am się z <a href="/dokumenty/rodo/" target="_blank" '
        'rel="noopener">Polityką RODO (klauzulą informacyjną)</a>.'
    ),
    "GUARDIAN": (
        'Oświadczam, że mój rodzic / opiekun prawny zapoznał się z <a href="/dokumenty/'
        'zgoda-opiekuna/" target="_blank" rel="noopener">Zgodą rodzica lub opiekuna prawnego</a> '
        "i wyraża zgodę na mój udział w Olimpiadzie oraz na przetwarzanie moich danych osobowych."
    ),
    "PUBLISH_NAME": (
        "Wyrażam zgodę na publikację mojego imienia i nazwiska (wraz ze szkołą) na listach "
        "wyników i laureatów."
    ),
}

#: Kolejność pól zgód w formularzu i w API. Kolejność jest treścią: regulamin i RODO stoją przed
#: zgodami dobrowolnymi, żeby nikt nie zaakceptował dobrowolnej, myśląc, że to ta wymagana.
EXPECTED_CONSENT_FIELD_ORDER = (
    "terms_consent",
    "gdpr_consent",
    "guardian_consent",
    "publish_name_consent",
)


def test_consent_set_of_the_api_unchanged(client_for, competition):
    """``GET /api/auth/consents/`` – kontrakt dla klienta zewnętrznego, więc zamrożony w całości."""
    response = client_for(competition).get("/api/auth/consents/")
    payload = response.json()

    assert response.status_code == 200
    assert [{key: row[key] for key in EXPECTED_CONSENT_SET[0]} for row in payload] == list(
        EXPECTED_CONSENT_SET
    )
    assert [row["label"] for row in payload] == [EXPECTED_CONSENT_LABELS[row["kind"]] for row in payload]
    assert tuple(row["field"] for row in payload) == EXPECTED_CONSENT_FIELD_ORDER


def test_registration_form_consents_unchanged(client_for, competition):
    """Formularz ``/register/``: te same etykiety, ta sama kolejność, ta sama podpowiedź.

    Porównujemy pola i etykiety, a nie cały dokument: dokument zmienia się przy każdej poprawce
    CSS-a i test byłby alarmem, którego nikt nie czyta (§ 5.3).
    """
    response = client_for(competition).get("/register/")
    form = response.context["form"]

    assert response.status_code == 200
    assert form.consent_field_names == EXPECTED_CONSENT_FIELD_ORDER
    for consent in EXPECTED_CONSENT_SET:
        field = form.fields[consent["field"]]
        assert str(field.label) == EXPECTED_CONSENT_LABELS[consent["kind"]]
        assert str(field.help_text) == consent["help_text"]
        # Wymagalność rozstrzyga serwis, nie pole – patrz docstring ``ConsentFieldsMixin``.
        assert field.required is False


def test_registration_form_keeps_the_captcha_message(client_for, competition):
    """Komunikat CAPTCHA czyta człowiek, który **nie może się zarejestrować** – z adresem w środku.

    Sprawdzamy pole formularza, a nie napis w dokumencie: blok antyspamowy renderuje się osobnym
    fragmentem i jego obecność w HTML-u zależy od szablonu, a przedmiotem jest **adres**, pod który
    odsyłamy kogoś, kto obrazka nie widzi. Ten adres musi zostać adresem tego organizatora.
    """
    form = client_for(competition).get("/register/").context["form"]

    assert str(form.fields["captcha"].help_text) == EXPECTED_CAPTCHA_HELP
    assert str(form.fields["captcha"].label) == EXPECTED_CAPTCHA_LABEL


def test_participant_panel_consents_unchanged(client_for, competition):
    """Panel uczestnika wydaje ten sam zestaw, co formularz i API – trzecia powierzchnia, ta sama treść.

    Gdyby któraś z trzech czytała zgody osobno, uczestnik zobaczyłby w panelu inne oświadczenie niż
    to, które złożył przy rejestracji – a dowodem jest ``ConsentRecord`` zapisany pod wersją, którą
    pokazuje panel.
    """
    participant = ParticipantFactory(competition=competition)
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)
    client = client_for(competition)
    client.force_login(participant.user)

    # Zakładka „Zgody” wprost: panel liczy dane **tylko** wybranej zakładki, więc pulpit domyślny
    # zgód w ogóle nie czyta – i to jest zamierzone, bo każdy odczyt kosztuje tam zapytanie.
    rows = client.get("/me/?tab=zgody").context["consent_rows"]

    assert [row["kind"] for row in rows] == [consent["kind"] for consent in EXPECTED_CONSENT_SET]
    assert [row["version"] for row in rows] == [consent["version"] for consent in EXPECTED_CONSENT_SET]
    assert [str(row["label"]) for row in rows] == [
        EXPECTED_CONSENT_LABELS[consent["kind"]] for consent in EXPECTED_CONSENT_SET
    ]
    # „Dobrowolna” znaczy: ani wymagana zawsze, ani wymagana od niepełnoletniego. Panel pokazuje
    # to słowem przy każdej pozycji, więc rozjazd byłby widoczny dla uczestnika od razu.
    assert [row["optional"] for row in rows] == [
        not (consent["required"] or consent["required_for_minor"]) for consent in EXPECTED_CONSENT_SET
    ]


# --- konto po anonimizacji -----------------------------------------------------------------------------


def test_anonymised_account_keeps_the_frozen_domain(competition):
    """Adres konta po skorzystaniu z prawa do usunięcia danych – domena **istniejącego** konta.

    Adres anonimowy jest daną, a nie konfiguracją: stoi w kolumnie logowania pod więzem
    unikalności (``accounts_user_email_ci_uniq``). Migracji, która przepisałaby go na domenę
    konkursu, nie ma i nie będzie – ten test jest jej zakazem wyrażonym wykonalnie.
    """
    from apps.accounts.profile import ANONYMISED_EMAIL_DOMAIN, anonymise_account

    participant = ParticipantFactory(competition=competition)
    user = participant.user

    anonymise_account(user)

    assert user.email == f"deleted-{user.pk}@{ANONYMISED_EMAIL_DOMAIN}"
    assert user.email.endswith("@invalid.olimpiadakwantowa.pl")
