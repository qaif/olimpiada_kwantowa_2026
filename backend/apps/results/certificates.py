"""Dyplomy i zaświadczenia: rejestr wystawionych dokumentów i skład PDF-a.

Dlaczego dokument powstaje przy każdym pobraniu, a nie leży w storage jako plik. Bo treść
dyplomu jest w całości wyprowadzona z faktów, które i tak są w bazie (edycja, rodzaj, odbiorca,
numer, data), a plik binarny dokładałby do tego trzy problemy naraz: kopię danych osobowych
w drugim miejscu, poprawkę szablonu, która nie dotyczy dokumentów już wystawionych, oraz
pytanie „która wersja pliku jest tą prawdziwą”. Tożsamość dokumentu niesie **numer i kod
weryfikacyjny** (``apps.results.models.Certificate``), a nie bajty PDF-a.

Kroje DejaVu są w repozytorium świadomie (``backend/static/fonts/`` wraz z licencją). Wbudowane
w reportlab fonty Type1 obsługują WinAnsi, czyli nie mają ``ą``, ``ę``, ``ł``, ``ś``, ``ż``,
``ź`` ani ``ń`` – dyplom dla Łucji Śniadeckiej składałby się częściowo z pustych prostokątów,
a to jest dokument, który uczestnik oprawia w ramkę i pokazuje w rekrutacji.

Weryfikacja publiczna (``/dyplomy/<kod>/``) jest celowo **uboga**: potwierdza rodzaj dokumentu,
edycję, numer i to, że dokument istnieje. Imienia i nazwiska nie pokazuje, dopóki uczestnik nie
zgodził się na publikację pełnych danych – strona jest dostępna bez logowania dla każdego, kto
przepisze kod z papieru, więc byłaby inaczej wyszukiwarką danych osobowych po kodzie z dyplomu.

Wygląd dokumentu jest **danymi**, a nie kodem: położenie napisów, tło, logo i podpisy bierze się
z ``results.CertificateTemplate`` (``apps.results.certificate_layout`` opisuje układ). Skład jest
jeden i ten sam dla dokumentu z szablonem i bez niego – bez szablonu po prostu obowiązuje układ
domyślny, czyli dokładnie ta karta, którą olimpiada składała od pierwszej edycji. Dwie osobne
ścieżki składu znaczyłyby, że poprawka w jednej z nich milcząco rozjeżdża dokumenty z drugiej.

Gotowy plik przechodzi jeszcze przez pieczęć elektroniczną (``apps.results.signing``). Bez
skonfigurowanego klucza to działanie puste – i taki jest stan domyślny.
"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status as http

from apps.accounts.models import SchoolSupervisor
from apps.competitions.models import Edition, StageEntry
from apps.competitions.scoping import scope_to_competition
from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy.documents import current_version, render_document

from .certificate_layout import PAGE_HEIGHT, PAGE_WIDTH, block, is_visible
from .models import (
    CERTIFICATE_NUMBER_PREFIX,
    Certificate,
    CertificateKind,
    CertificateTemplate,
)
from .signing import SignedDocument, sign_document

logger = logging.getLogger(__name__)

#: Katalog z krojami. Ten sam, z którego korzystają style serwisu – kroje są częścią repozytorium,
#: nie zależnością systemową, więc obraz produkcyjny nie musi mieć zainstalowanych fontów.
FONT_DIR = Path(settings.BASE_DIR) / "static" / "fonts"
FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"

#: Ile razy próbujemy nadać numer, zanim się poddamy. Numer jest kolejny w obrębie roku, więc
#: dwa równoległe „Wystaw” mogą sięgnąć po ten sam – unikalność w bazie to wychwyci, a ponowienie
#: weźmie następny wolny. Trzy próby wystarczają: koordynatorów jest kilku, nie kilkuset.
MAX_NUMBER_ATTEMPTS = 3

#: Podpisy dokumentów. Osobno od etykiet ``CertificateKind``, bo na papierze stoi zdanie
#: („Zaświadczenie o udziale”), a w panelu – jedno słowo („uczestnik”).
DOCUMENT_TITLES = {
    CertificateKind.LAUREAT: "Dyplom laureata",
    CertificateKind.FINALISTA: "Dyplom finalisty",
    CertificateKind.UCZESTNIK: "Zaświadczenie o udziale",
    CertificateKind.OPIEKUN: "Zaświadczenie dla opiekuna",
    CertificateKind.WARSZTATY: "Zaświadczenie o udziale w warsztatach",
}

#: Zdanie pod nazwiskiem. Rodzaj dokumentu mówi, **co** poświadczamy; to zdanie mówi to samo
#: językiem, w którym pisze się dokumenty – i to ono jest właściwą treścią dyplomu.
DOCUMENT_STATEMENTS = {
    CertificateKind.LAUREAT: "uzyskał(a) tytuł laureata Olimpiady Kwantowej",
    CertificateKind.FINALISTA: "uzyskał(a) tytuł finalisty Olimpiady Kwantowej",
    CertificateKind.UCZESTNIK: "brał(a) udział w Olimpiadzie Kwantowej",
    CertificateKind.OPIEKUN: "sprawował(a) opiekę nad uczestnikami Olimpiady Kwantowej",
    CertificateKind.WARSZTATY: "uczestniczył(a) w warsztatach online Olimpiady Kwantowej",
}

#: Nagłówek listy warsztatów na zaświadczeniu. Lista bez zapowiedzi czytałaby się jak przypis,
#: a to ona jest treścią tego akurat dokumentu – zdanie wyżej mówi tylko, że warsztaty były.
WORKSHOP_LIST_HEADING = "Tematy zajęć:"

#: Nazwa organizatora nad linią podpisu. Stała, bo na papierze podpisuje się komitet jako organ,
#: a nie osoba, która akurat kliknęła „Wystaw”.
SIGNATURE_LINE = "Przewodniczący Komitetu Głównego Olimpiady Kwantowej"

#: Autor w metadanych PDF-a. Widać go w podglądzie pliku **przed** otwarciem dokumentu, więc jest
#: treścią dokumentu, a nie szczegółem technicznym. Dotąd stał literałem w ``compose_pdf``; nazwa
#: istnieje po to, żeby odwrót dla wyłączonej flagi ``document_templates`` miał gdzie stać.
PDF_AUTHOR = "Olimpiada Kwantowa"


def document_fallback(kind: str) -> dict[str, str]:
    """Dzisiejsze napisy dokumentu tego rodzaju – **odwrót** dla szablonów tekstu (§ 1.1.3).

    Wołający podaje literał, a ``apps.tenancy.documents.render_document`` rozstrzyga, czy na
    papier wyjdzie on, czy wiersz z bazy – dokładnie tak, jak przy marce listów
    (``apps/tenancy/branding.py``). Dopóki flaga ``document_templates`` jest wyłączona, dokument
    składa się z tych czterech napisów i **nie pada ani jedno zapytanie** do tabeli szablonów.

    Nieznany rodzaj dostaje napisy „uczestnika” – ta sama reguła, co przed tą zmianą.
    """
    return {
        "title": DOCUMENT_TITLES.get(kind, DOCUMENT_TITLES[CertificateKind.UCZESTNIK]),
        "statement": DOCUMENT_STATEMENTS.get(kind, DOCUMENT_STATEMENTS[CertificateKind.UCZESTNIK]),
        "signature_line": SIGNATURE_LINE,
        "author": PDF_AUTHOR,
    }


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


# --- rejestr ------------------------------------------------------------------------------------


def number_prefix(competition=None) -> str:
    """Prefiks numeru dokumentu tego konkursu – ``"OK"`` dla Olimpiady Kwantowej.

    Prefiks jest własnością konkursu (``Competition.certificate_prefix``, § 3.3), bo numer trafia
    na papier i do pism: dwa konkursy jednej instalacji nie mogą wystawiać dokumentów o numerach
    nierozróżnialnych na wydruku. Konkurs #1 dostał w migracji ``tenancy.0003`` dokładnie ``"OK"``,
    więc numery Olimpiady Kwantowej wyglądają i numerują się identycznie jak przed tą zmianą.

    Odwrót na stałą ``CERTIFICATE_NUMBER_PREFIX`` obsługuje wyłącznie instalację bez konkursów
    (baza sprzed ``tenancy.0002``) – tam nie ma czego pytać, a zachowanie ma być to sprzed etapu 1.
    """
    prefix = (getattr(competition, "certificate_prefix", "") or "").strip()
    return prefix or CERTIFICATE_NUMBER_PREFIX


def _next_number(year: int, competition=None) -> str:
    """Kolejny numer w roku: ``<prefiks konkursu>/<rok>/<liczba>``.

    Numerację prowadzimy po **roku kalendarzowym wystawienia**, a nie po edycji: tak numeruje się
    dokumenty w sekretariacie i tak są cytowane w pismach. Kolejność wyliczamy z najwyższego
    dotychczasowego numeru, a nie z licznika wierszy – skasowany kiedyś wiersz nie może sprawić,
    że numer zostanie wydany komuś drugi raz.

    Zapytanie idzie po **całej** tabeli, bez zawężenia do konkursu, i tak ma być: unikalność
    numeru zostaje globalna (§ 3.3), a prefiks konkursu jest w nim zawarty, więc ``startswith``
    i tak wybiera wyłącznie własną serię. Osobna numeracja per konkurs wychodzi z prefiksu, a nie
    z filtra – a gdyby wyszła z filtra, dwa konkursy o tym samym prefiksie nadałyby ten sam numer.
    """
    prefix = f"{number_prefix(competition)}/{year}/"
    taken = Certificate.objects.filter(number__startswith=prefix).values_list("number", flat=True)
    highest = 0
    for number in taken:
        tail = number.rsplit("/", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1}"


def _assert_recipient(entry: StageEntry | None, supervisor: SchoolSupervisor | None) -> None:
    if (entry is None) == (supervisor is None):
        raise _conflict(
            "Dokument wystawia się albo uczestnikowi, albo opiekunowi – dokładnie jednemu z nich.",
            "INVALID_RECIPIENT",
        )


def issue_certificate(
    *,
    edition: Edition,
    kind: str,
    entry: StageEntry | None = None,
    supervisor: SchoolSupervisor | None = None,
    actor=None,
    request=None,
) -> tuple[Certificate, bool]:
    """Wystawia dokument albo oddaje ten, który już istnieje. Zwraca ``(dokument, czy nowy)``.

    Powtórne kliknięcie „Wystaw” **nie** wypisuje drugiego dyplomu: numer raz nadany jest
    tożsamością dokumentu, a uczestnik z dwoma numerami na ten sam tytuł miałby problem przy
    pierwszej rekrutacji, w której trzeba podać numer. Unikalność pilnują constrainty
    (wpis + rodzaj, opiekun + edycja), a ta funkcja ich nie obchodzi – sprawdza je najpierw.

    Nadanie numeru jest w pętli ponowień, bo kolejny numer wylicza się z tego, co już jest
    w bazie: dwa równoległe „Wystaw wszystkim” mogą sięgnąć po tę samą liczbę i wtedy jedno
    z nich dostanie ``IntegrityError`` na unikalności numeru zamiast cicho ją zdublować.
    """
    _assert_recipient(entry, supervisor)
    if kind not in CertificateKind.values:
        raise _conflict(f"Nieznany rodzaj dokumentu: {kind}.", "INVALID_CERTIFICATE_KIND")

    existing = (
        Certificate.objects.filter(entry=entry, kind=kind).first()
        if entry is not None
        else Certificate.objects.filter(supervisor=supervisor, edition=edition).first()
    )
    if existing is not None:
        return existing, False

    year = timezone.now().year
    # Konkurs bierzemy z **edycji dokumentu**, a nie z kontekstu przebiegu: dyplom wystawiony
    # w konkursie A ma nosić prefiks A także wtedy, gdy składa go przebieg wsadowy konkursu B.
    # Ta sama zasada, co przy adresie weryfikacji w kodzie QR (``verification_url``).
    competition = edition.competition
    # Poza pętlą ponowień: wersja tekstu jest ta sama przy każdej próbie nadania numeru, a przy
    # wyłączonej fladze ``document_templates`` to wywołanie nie pyta bazy ani razu.
    template_version = current_version(competition, kind)
    for attempt in range(MAX_NUMBER_ATTEMPTS):
        try:
            with transaction.atomic():
                certificate = Certificate.objects.create(
                    edition=edition,
                    entry=entry,
                    supervisor=supervisor,
                    kind=kind,
                    number=_next_number(year, competition),
                    # Wersja tekstu z chwili wystawienia – kopia napisu, nie klucz obcy (§ 1.1.3).
                    # Przy wyłączonej fladze jest pusta i nie kosztuje zapytania, a pusta znaczy
                    # „układ wbudowany”, czyli dzisiejsze stałe tego modułu.
                    template_version=template_version,
                    issued_by=actor if getattr(actor, "is_authenticated", False) else None,
                )
        except IntegrityError:
            if attempt == MAX_NUMBER_ATTEMPTS - 1:
                raise
            continue
        audit(
            actor,
            "certificate.issued",
            certificate,
            {
                "kind": kind,
                "number": certificate.number,
                "edition_id": edition.pk,
                # Identyfikator odbiorcy, nigdy jego nazwisko – tak samo jak w reszcie audytu.
                "entry_id": entry.pk if entry is not None else None,
                "supervisor_id": supervisor.pk if supervisor is not None else None,
            },
            request=request,
        )
        logger.info("Wystawiono dokument %s (%s) dla edycji %s.", certificate.number, kind, edition.pk)
        return certificate, True
    raise _conflict("Nie udało się nadać numeru dokumentu. Spróbuj jeszcze raz.", "NUMBER_CONFLICT")


# --- wystawianie hurtowe ------------------------------------------------------------------------


def supervisors_with_participants(edition: Edition) -> list[dict]:
    """Opiekunowie mający w tej edycji **choć jednego** ucznia, wraz z liczbą tych uczniów.

    Krąg odbiorców zaświadczenia dla opiekuna jest tu inny niż na ekranie dyplomów jednego etapu
    (tam: ci, którzy potwierdzili udział szkoły) i jest to różnica świadoma. Zaświadczenie
    poświadcza **pracę z uczniami**, a nie złożenie oświadczenia; nauczyciel, którego ośmioro
    uczniów startowało w zawodach, tę pracę wykonał, choćby nie kliknął „Potwierdzam udział”.
    Liczba uczniów jedzie razem z opiekunem, bo to ona jest w panelu jedynym widocznym
    uzasadnieniem, dlaczego ktoś jest na tej liście – i ona odróżnia opiekuna od pustego konta.

    Wiązanie idzie po adresie e-mail (``Participant.supervisor_email``), bo to uczeń wskazuje
    swojego opiekuna – i to jest **jedyne** źródło tej relacji w systemie. Liczymy je hurtem:
    jedno zapytanie po adresy uczniów z wpisami w edycji i jedno po profile opiekunów.
    """
    from apps.accounts.models import Participant
    from apps.accounts.supervisors import normalize_supervisor_email

    counts: dict[str, int] = {}
    # Para (uczestnik, adres) z ``distinct``, a nie same adresy: złączenie po wpisach do etapów
    # powiela uczestnika tyle razy, w ilu etapach startuje, a my liczymy **uczniów**, nie starty.
    students = (
        Participant.objects.filter(stage_entries__stage__edition=edition)
        .exclude(supervisor_email="")
        .values_list("id", "supervisor_email")
        .distinct()
    )
    for _participant_id, email in students:
        normalized = normalize_supervisor_email(email)
        if normalized:
            counts[normalized] = counts.get(normalized, 0) + 1
    if not counts:
        return []
    rows = []
    # Zawężenie do konkursu edycji, a nie do całej instalacji: dopasowanie idzie **po adresie
    # e-mail**, a ten sam nauczyciel bywa opiekunem w dwóch olimpiadach z tego samego adresu.
    # Bez tego filtru zaświadczenie z edycji konkursu A trafiłoby na jego profil w konkursie B.
    for supervisor in (
        SchoolSupervisor.objects.for_competition(edition.competition)
        .select_related("user")
        .order_by("user__last_name", "user__first_name", "id")
    ):
        students = counts.get(normalize_supervisor_email(supervisor.user.email), 0)
        if students:
            rows.append({"supervisor": supervisor, "students": students})
    return rows


def issue_supervisor_certificates(edition: Edition, *, actor=None, request=None) -> list[Certificate]:
    """Zaświadczenia dla wszystkich opiekunów z uczniami w tej edycji. Idempotentne.

    Opiekun, który ma już dokument w tej edycji, dostaje ten sam numer (unikalność opiekun +
    edycja pilnuje tego w bazie). Powtórne „Wystaw wszystkim” jest więc bezpieczne – i musi być,
    bo pierwsze bywa klikane w trakcie gali, kiedy lista jeszcze rośnie.
    """
    return [
        issue_certificate(
            edition=edition,
            kind=CertificateKind.OPIEKUN,
            supervisor=row["supervisor"],
            actor=actor,
            request=request,
        )[0]
        for row in supervisors_with_participants(edition)
    ]


def participants_with_workshops(edition: Edition) -> list[dict]:
    """Uczestnicy z odhaczoną **co najmniej jedną** obecnością na warsztatach, z ich wpisem do etapu.

    Zaświadczenie musi wisieć na czymś, co da się wskazać kluczem obcym, a ``Certificate`` zna
    dwa rodzaje odbiorcy: wpis do etapu albo opiekuna. Warsztaty nie są etapem, więc dokument
    przypinamy do **najwcześniejszego wpisu uczestnika w tej edycji**. To nie jest obejście:
    wpis jest tu identyfikatorem uczestnika w edycji (i tym, co daje unikalność „jeden taki
    dokument na osobę i edycję”), a nie twierdzeniem o etapie – na papierze nie ma ani słowa
    o etapie, jest lista warsztatów.

    Uczestnik bez żadnego wpisu w edycji nie dostanie zaświadczenia i tak ma być: system nie wie
    wtedy, do której edycji dokument przypisać, a warsztaty odbywają się w rytmie edycji.
    """
    from apps.cms.models import WorkshopAttendance

    participant_ids = set(WorkshopAttendance.objects.values_list("participant_id", flat=True).distinct())
    if not participant_ids:
        return []
    rows: dict[int, StageEntry] = {}
    for entry in (
        StageEntry.objects.filter(participant_id__in=participant_ids, stage__edition=edition)
        .select_related("participant__user", "stage")
        .order_by("stage__opens_at", "stage_id")
    ):
        rows.setdefault(entry.participant_id, entry)
    return [
        {"participant": entry.participant, "entry": entry}
        for entry in sorted(rows.values(), key=lambda item: item.participant_id)
    ]


def issue_workshop_certificates(edition: Edition, *, actor=None, request=None) -> list[Certificate]:
    """Zaświadczenia z warsztatów dla wszystkich, którzy byli na choć jednych zajęciach."""
    return [
        issue_certificate(
            edition=edition,
            kind=CertificateKind.WARSZTATY,
            entry=row["entry"],
            actor=actor,
            request=request,
        )[0]
        for row in participants_with_workshops(edition)
    ]


# --- treść dokumentu ----------------------------------------------------------------------------


@dataclass(frozen=True)
class CertificateContent:
    """Gotowa treść jednego dokumentu – wszystko, co ma stanąć na papierze, i nic poza tym."""

    title: str
    statement: str
    recipient: str
    school: str
    edition: str
    number: str
    code: str
    issued_on: str
    #: Wiersze listy warsztatów („12.11.2026 – Kubity i bramki, prowadzi: dr Kowalska”). Puste
    #: dla każdego innego rodzaju dokumentu – zaświadczenie o udziale w zawodach nie ma czego
    #: wyliczać, a blok listy po prostu nic wtedy nie rysuje.
    workshops: tuple[str, ...] = ()
    #: Adres strony weryfikacji – ten sam, który koduje QR. W treści, a nie w składzie, bo to
    #: fakt o dokumencie (jak numer), a nie decyzja graficzna.
    verification_url: str = ""
    #: Nazwa organu nad kreską podpisu, gdy szablon graficzny nie ma ani jednego bloku podpisu.
    #: W treści, a nie w ``_draw_signatures``, bo od etapu 2 jej źródłem bywa konfiguracja
    #: konkursu (``tenancy.DocumentTemplate.signature_line``), a skład ma dostać gotowy napis.
    signature_line: str = SIGNATURE_LINE
    #: Autor w metadanych pliku (``setAuthor``) – patrz :data:`PDF_AUTHOR`.
    author: str = PDF_AUTHOR
    #: Wersja tekstu, z której ten dokument powstał. Pusto = układ wbudowany, czyli stałe wyżej.
    #: Trafia do ``Certificate.template_version`` przy wystawieniu i stamtąd wraca przy każdym
    #: pobraniu – dokument wydany rok temu ma wyjść z drukarki tak samo, jak wtedy.
    template_version: str = ""


def _recipient_name(certificate: Certificate) -> str:
    """Imię i nazwisko odbiorcy. Konto zanonimizowane zostawia numer – dokument i tak istnieje."""
    user = (
        certificate.supervisor.user if certificate.is_for_supervisor else certificate.entry.participant.user
    )
    return user.get_full_name() or user.email


def _recipient_school(certificate: Certificate) -> str:
    if certificate.is_for_supervisor:
        return certificate.supervisor.school
    return certificate.entry.participant.school


def verification_url(code: str, competition=None) -> str:
    """Bezwzględny adres strony weryfikacji dokumentu o tym kodzie.

    Bez żądania – dokument składa się także w zadaniu wsadowym („Wystaw wszystkim”), więc domenę
    podaje **konkurs**: ten wskazany wprost albo ten z kontekstu (``absolute_url``). Dotąd stało tu
    odwołanie do ``settings.SITE_URL`` – ustawienia, którego nie definiuje ani ``config/settings``,
    ani ``.env.example``, więc kod QR na każdym dyplomie niósł adres **względny**, czyli nie
    prowadził nigdzie. To jest naprawa tamtej dziury przy okazji zakresowania, a nie nowa funkcja.

    Konkurs podajemy wprost, bo dokument bywa składany w pętli po wielu konkursach: adres ma
    wskazywać domenę organizatora, który dyplom wystawił, a nie tę, spod której ktoś go pobiera.
    Gdy nie ma żadnego źródła, zostaje sama ścieżka – dokument ma wyjść mimo wszystko.
    """
    from apps.accounts.activation import absolute_url

    return absolute_url(reverse("web:certificate-verify", args=[code]), competition=competition)


def _workshop_lines(certificate: Certificate) -> tuple[str, ...]:
    """Wiersze listy warsztatów – wyłącznie dla zaświadczenia z warsztatów.

    Import ``apps.cms`` jest w środku funkcji, bo to zależność w poprzek warstw: harmonogram
    warsztatów jest **treścią redakcyjną** (blok ``schedule`` na stronie „Warsztaty”), a wyniki
    zawodów nie mają powodu ciągnąć CMS-u przy starcie procesu. Na poziomie pliku byłoby to
    zresztą ryzyko cyklu przy ładowaniu aplikacji.
    """
    if certificate.kind != CertificateKind.WARSZTATY or certificate.entry_id is None:
        return ()
    from apps.cms.workshops import attended_workshops

    return tuple(
        ", ".join(part for part in (f"{row['date']} – {row['topic']}", row["lecturer"]) if part)
        # Warsztaty tego konkursu, a nie konkursu z kontekstu żądania: dyplom bywa generowany
        # z zadania w tle, gdzie kontekst może wskazywać inny konkurs niż ten, którego jest dyplom.
        for row in attended_workshops(certificate.entry.participant, certificate.edition.competition)
    )


def certificate_content(certificate: Certificate) -> CertificateContent:
    """Składa treść dokumentu z faktów w bazie. Jedno miejsce dla PDF-a i dla testów.

    Napisy idą przez ``apps.tenancy.documents.render_document`` i **wersją zapisaną przy
    wystawieniu**, a nie wersją obowiązującą dziś: dokument pobrany po poprawce tekstu ma wyjść
    taki sam, jak ten, który odbiorca trzyma w ręku. Puste ``template_version`` (dokumenty sprzed
    etapu 2 i wszystkie przy wyłączonej fladze) znaczy „układ wbudowany”, czyli stałe tego modułu –
    i wtedy nie pada ani jedno dodatkowe zapytanie.
    """
    # Konkurs z edycji dokumentu, a nie z kontekstu: dyplom wystawiony w konkursie A ma nieść
    # markę i adres weryfikacji A także wtedy, gdy składa go przebieg wsadowy konkursu B.
    competition = certificate.edition.competition
    recipient = _recipient_name(certificate)
    school = _recipient_school(certificate)
    edition = certificate.edition.year_label
    issued_on = timezone.localtime(certificate.issued_at).strftime("%d.%m.%Y")
    document = render_document(
        competition,
        certificate.kind,
        version=certificate.template_version,
        fallback=document_fallback(certificate.kind),
        recipient=recipient,
        school=school,
        edition=edition,
        number=certificate.number,
        code=certificate.code,
        date=issued_on,
        # Kod uczestnika mamy tu za darmo (odbiorcę czytaliśmy przed chwilą tą samą drogą),
        # a zaświadczenie opiekuna go po prostu nie ma. Znacznika ``{stage}`` w kontekście
        # dyplomu **nie ma** z premedytacją: etap nie stoi na papierze, a jego nazwa kosztowałaby
        # zapytanie przy każdym pobraniu dokumentu.
        participant_code=certificate.entry.participant.public_code if certificate.entry_id else "",
    )
    return CertificateContent(
        title=document.title,
        statement=document.statement,
        recipient=recipient,
        school=school,
        edition=edition,
        number=certificate.number,
        code=certificate.code,
        issued_on=issued_on,
        workshops=_workshop_lines(certificate),
        verification_url=verification_url(certificate.code, competition),
        # Bez „albo stała”: odwrót jest w ``document_fallback``, a pusta linia podpisu w szablonie
        # jest decyzją organizatora, a nie brakiem danych do uzupełnienia za jego plecami.
        signature_line=document.signature_line,
        author=document.author,
        template_version=document.version,
    )


# --- szablon graficzny --------------------------------------------------------------------------


def templates_of(competition=None):
    """Szablony jednego konkursu – wejście do każdego odczytu tej tabeli.

    Odwrót („nie zawężaj”) należy do instalacji bez konkursów i jest tą samą regułą, co w całej
    domenie zawodów (``apps.competitions.scoping.scope_to_competition``). Szablon ma od wydania D
    własną kolumnę, więc „szablon dla wszystkich edycji” jest szablonem wszystkich edycji
    **tego** konkursu, a nie wspólną półką instalacji.
    """
    return scope_to_competition(CertificateTemplate.objects.all(), competition)


def resolve_template(kind: str, edition: Edition | None, competition=None) -> CertificateTemplate | None:
    """Szablon dla tego dokumentu albo ``None`` – wtedy obowiązuje układ wbudowany.

    Kolejność dopasowania idzie od najbardziej szczegółowego do najogólniejszego: (rodzaj, edycja)
    → (rodzaj, wszystkie edycje) → (wszystkie rodzaje, edycja) → (wszystkie, wszystkie). Rodzaj
    jest **przed** edycją z premedytacją: dyplom laureata ma prawo wyglądać inaczej niż reszta
    dokumentów w tej samej edycji, a odwrotna kolejność kazałaby kopiować szablon laureata do
    każdej edycji z osobna, żeby nie przykryła go jubileuszowa winieta wspólna dla wszystkich.

    Konkurs jest **przed** wszystkim (§ 3.5): domyślnie bierze się z edycji dokumentu, a nie
    z kontekstu przebiegu – dyplom konkursu A ma dostać winietę A także wtedy, gdy składa go
    przebieg wsadowy konkursu B. ``competition`` podaje wprost wyłącznie podgląd szablonu
    w panelu, bo tam edycji może nie być wcale.

    Czytamy to przy każdym pobraniu dokumentu, więc każde zapytanie jest po indeksowanych
    kolumnach i bez ``JOIN``-ów – a najczęstszy przypadek (nie ma ani jednego szablonu) kończy
    się po pierwszym z nich.
    """
    if competition is None and edition is not None:
        competition = edition.competition
    scoped = templates_of(competition).filter(is_active=True)
    if not scoped.exists():
        return None
    edition_id = edition.pk if edition is not None else None
    candidates = (
        {"kind": kind, "edition_id": edition_id},
        {"kind": kind, "edition_id": None},
        {"kind": "", "edition_id": edition_id},
        {"kind": "", "edition_id": None},
    )
    seen: list[dict] = []
    for filters in candidates:
        # Dokument zawsze należy do edycji (pole jest wymagane), więc powtórki tu nie ma;
        # ``seen`` pilnuje jej na wypadek wywołania z ``edition=None`` w podglądzie szablonu.
        if filters in seen:
            continue
        seen.append(filters)
        template = scoped.filter(**filters).first()
        if template is not None:
            return template
    return None


def make_default_template(template: CertificateTemplate) -> int:
    """Czyni ten szablon obowiązującym dla jego rodzaju i edycji. Zwraca liczbę wyłączonych.

    Reguła dopasowania (``resolve_template``) bierze przy remisie szablon **najnowszy**, więc
    „ustaw jako domyślny” nie jest osobnym polem w tabeli, tylko czynnością: włącz ten, wyłącz
    pozostałe pasujące dokładnie tak samo. Flagi „domyślny” nie ma w modelu z premedytacją –
    byłaby drugą, niezależną prawdą obok ``is_active`` i pierwszy rozjazd między nimi dałby
    ekran, na którym domyślny szablon jest wyłączony.

    Wyłączone szablony zostają w bazie razem z plikami: powrót do poprzedniej winiety ma być
    jednym kliknięciem, a nie ponownym wgrywaniem tła z czyjegoś dysku.
    """
    # Zawężamy do konkursu **tego** szablonu, a nie do konkursu kontekstu: „ustaw jako domyślny”
    # ma wyłączyć rywali w tej samej półce, a półka jest własnością organizatora.
    replaced = (
        CertificateTemplate.objects.for_competition(template.competition)
        .filter(kind=template.kind, edition_id=template.edition_id, is_active=True)
        .exclude(pk=template.pk)
        .update(is_active=False)
    )
    if not template.is_active:
        template.is_active = True
        template.save(update_fields=["is_active"])
    return replaced


# --- skład PDF ----------------------------------------------------------------------------------


def register_fonts() -> None:
    """Rejestruje kroje DejaVu w reportlabie. Idempotentne – rejestracja jest globalna na proces.

    Import reportlaba jest wewnątrz funkcji, a nie na poziomie modułu, z tego samego powodu,
    co w innych modułach generujących dokumenty: moduł czyta też panel koordynatora (rejestr
    dokumentów), a ten nie ma powodu ciągnąć całej biblioteki składu przy starcie procesu.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    registered = set(pdfmetrics.getRegisteredFontNames())
    for name, filename in ((FONT_REGULAR, "DejaVuSans.ttf"), (FONT_BOLD, "DejaVuSans-Bold.ttf")):
        if name in registered:
            continue
        path = FONT_DIR / filename
        if not path.exists():  # pragma: no cover - brak kroju w repozytorium byłby błędem wdrożenia
            raise _conflict(
                f"Brak kroju {filename} w {FONT_DIR}. Bez niego dokument nie ma polskich znaków.",
                "CERTIFICATE_FONT_MISSING",
            )
        pdfmetrics.registerFont(TTFont(name, str(path)))


def _font(settings: dict) -> str:
    """Krój bloku. Jedno rozstrzygnięcie – tło i wielkość mają własne klucze układu."""
    return FONT_BOLD if settings.get("bold") else FONT_REGULAR


def _text(canvas, text: str, settings: dict, *, width: float, height: float) -> None:
    """Jedna linia w miejscu opisanym układem. Brak ``x`` = wyśrodkowanie na stronie.

    Pusty napis nie jest błędem i nie rysuje niczego: „szkoła” bywa nieuzupełniona, a lista
    warsztatów istnieje tylko na jednym rodzaju dokumentu. Blok wyłączony (``show: false``)
    zachowuje się tak samo – dyplom na gotowym tle z nadrukowanym nagłówkiem nie ma powodu
    dokładać drugiego napisu „OLIMPIADA KWANTOWA”.
    """
    if not text or not is_visible(settings):
        return
    canvas.setFont(_font(settings), settings.get("size", 12))
    y = height - float(settings.get("y", 0))
    x = settings.get("x")
    if x is None:
        canvas.drawCentredString(width / 2, y, text)
    else:
        canvas.drawString(float(x), y, text)


def _image_bytes(field) -> bytes | None:
    """Zawartość pliku z prywatnego storage albo ``None``, gdy pliku nie ma.

    Brakujący plik (usunięty z bucketu, nieudane wgranie) **nie** wywraca dokumentu: dyplom ma
    wyjść bez winiety, a nie zamienić się w błąd 500 w dniu gali. Ślad zostaje w logu.
    """
    if not field:
        return None
    try:
        with field.open("rb") as handle:
            return handle.read()
    except (OSError, ValueError):
        logger.warning("Nie udało się wczytać pliku szablonu dyplomu: %s.", getattr(field, "name", "?"))
        return None


def _draw_picture(canvas, data: bytes | None, *, x: float, y: float, width: float, height: float) -> None:
    """Obraz w prostokącie, z zachowaniem proporcji. ``mask='auto'`` zostawia przezroczystość PNG."""
    if not data:
        return
    from reportlab.lib.utils import ImageReader

    canvas.drawImage(
        ImageReader(BytesIO(data)),
        x,
        y,
        width=width,
        height=height,
        preserveAspectRatio=True,
        anchor="c",
        mask="auto",
    )


def _draw_background(canvas, template, *, width: float, height: float) -> None:
    """Tło obrazkowe na całą stronę. Tło w PDF-ie idzie inną drogą (``_merge_background``)."""
    if template is None or not template.background:
        return
    if Path(template.background.name).suffix.lower() == ".pdf":
        return
    data = _image_bytes(template.background)
    if data is None:
        return
    from reportlab.lib.utils import ImageReader

    # Bez ``preserveAspectRatio``: tło ma pokryć kartkę w całości, a projekt z drukarni i tak
    # jest w proporcjach A4. Biały pasek przy krawędzi wygląda na wadę wydruku, nie na decyzję.
    canvas.drawImage(ImageReader(BytesIO(data)), 0, 0, width=width, height=height, mask="auto")


def _draw_signatures(
    canvas,
    template,
    *,
    layout: dict,
    width: float,
    height: float,
    signature_line: str = SIGNATURE_LINE,
) -> None:
    """Bloki podpisu: kreska, a pod nią nazwisko i funkcja. Nad kreską – podpis odręczny z pliku.

    Bez szablonu (albo gdy szablon nie ma ani jednego podpisu) zostaje jeden blok z nazwą organu,
    czyli dokładnie to, co dyplom miał od początku: dokument wychodzi z systemu także wtedy, gdy
    pieczęci elektronicznej nie skonfigurowano, więc miejsce na podpis odręczny musi na nim być.

    ``signature_line`` jest **napisem gotowym** – rozstrzygnął go już ``certificate_content``
    (stała albo tekst z konfiguracji konkursu, § 1.1.3). Domyślna wartość jest dzisiejsza, żeby
    wołający, który jej nie podaje, składał dokument tak, jak przed etapem 2.
    """
    settings = block(layout, "signatures")
    if not is_visible(settings):
        return
    blocks = template.signatures() if template is not None else []
    if not blocks:
        blocks = [{"image": None, "name": "", "title": signature_line}]
    line_width = float(settings.get("width", 240))
    size = settings.get("size", 10)
    baseline = height - float(settings.get("y", 445))
    slot = width / (len(blocks) + 1)
    canvas.setLineWidth(0.7)
    for index, signature in enumerate(blocks, start=1):
        center = slot * index
        _draw_picture(
            canvas,
            _image_bytes(signature["image"]),
            x=center - line_width / 2,
            y=baseline + 6,
            width=line_width,
            height=46,
        )
        canvas.line(center - line_width / 2, baseline, center + line_width / 2, baseline)
        canvas.setFont(FONT_REGULAR, size)
        offset = baseline - size - 7
        for line in (signature["name"], signature["title"]):
            if not line:
                continue
            canvas.drawCentredString(center, offset, line)
            offset -= size + 3


def _draw_footer(canvas, content: CertificateContent, *, layout: dict, width: float, height: float) -> None:
    """Pas identyfikujący dokument: numer i kod po lewej, data i adres weryfikacji po prawej."""
    settings = block(layout, "footer")
    if not is_visible(settings):
        return
    size = settings.get("size", 9)
    margin = float(settings.get("margin", 60))
    top = height - float(settings.get("y", 525))
    canvas.setFont(FONT_REGULAR, size)
    canvas.drawString(margin, top, f"Numer dokumentu: {content.number}")
    canvas.drawString(margin, top - size - 5, f"Kod weryfikacyjny: {content.code}")
    right = _footer_right_edge(content, layout=layout, width=width, height=height, top=top, size=size)
    canvas.drawRightString(right, top, f"Data wystawienia: {content.issued_on}")
    canvas.drawRightString(right, top - size - 5, "Weryfikacja: /dyplomy/<kod>/ w serwisie olimpiady")


#: Odstęp między kodem QR a prawą kolumną stopki, gdy stopka musi się przed nim cofnąć.
QR_FOOTER_GAP = 8


def _qr_box(content: CertificateContent, *, layout: dict, height: float) -> tuple[float, float, float] | None:
    """``(x, dół, bok)`` kodu QR w układzie strony albo ``None``, gdy kod nie będzie rysowany.

    Jedno miejsce z regułą „czy i gdzie stoi kod”: czyta ją rysowanie kodu i stopka, która ma się
    z nim nie pokrywać. Dwie kopie tej reguły rozjechałyby się przy pierwszej zmianie układu.
    """
    settings = block(layout, "qr")
    if not is_visible(settings) or not content.verification_url:
        return None
    size = float(settings.get("size", 74))
    bottom = height - float(settings.get("y", 455)) - size
    return float(settings.get("x", 712)), bottom, size


def _footer_right_edge(
    content: CertificateContent, *, layout: dict, width: float, height: float, top: float, size: float
) -> float:
    """Prawa krawędź prawej kolumny stopki: margines strony albo lewa krawędź kodu QR.

    W domyślnym układzie kod QR (bok 74 pt od ``y=455``) sięga 4 pt poniżej pierwszego wiersza
    stopki (``y=525``) i zasłaniał końcówkę napisu „Data wystawienia”. Stopka cofa się przed
    kodem tylko wtedy, gdy oba prostokąty naprawdę na siebie zachodzą – szablon, który przesunął
    kod wyżej albo stopkę niżej, zachowuje wyrównanie do marginesu.
    """
    edge = width - margin_of(layout)
    box = _qr_box(content, layout=layout, height=height)
    if box is None:
        return edge
    qr_x, qr_bottom, qr_size = box
    footer_top = top + size
    footer_bottom = top - size - 5 - 2
    overlaps_vertically = qr_bottom < footer_top and qr_bottom + qr_size > footer_bottom
    overlaps_horizontally = qr_x < edge
    if overlaps_vertically and overlaps_horizontally:
        return qr_x - QR_FOOTER_GAP
    return edge


def margin_of(layout: dict) -> float:
    """Margines stopki z układu (``footer.margin``), w punktach."""
    return float(block(layout, "footer").get("margin", 60))


def _draw_qr(canvas, content: CertificateContent, *, layout: dict, height: float) -> None:
    """Kod QR z adresem weryfikacji – dla tych, którzy dostają dyplom jako zdjęcie w telefonie.

    Kod nie zastępuje napisu w stopce i nie może go zastąpić: kod weryfikacyjny bywa przepisywany
    do wniosku ręcznie, a QR jest wyłącznie skrótem drogi. Rysujemy go z ``reportlab.graphics``,
    więc nie dochodzi żadna zależność.
    """
    box = _qr_box(content, layout=layout, height=height)
    if box is None:
        return
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing

    x, bottom, size = box
    widget = qr.QrCodeWidget(content.verification_url)
    bounds = widget.getBounds()
    # Widget ma własną, naturalną wielkość w punktach; ``transform`` skaluje go do kwadratu
    # zamówionego w układzie. Bez tego kod wychodzi w rozmiarze zależnym od długości adresu.
    drawing = Drawing(
        size,
        size,
        transform=[size / (bounds[2] - bounds[0]), 0, 0, size / (bounds[3] - bounds[1]), 0, 0],
    )
    drawing.add(widget)
    renderPDF.draw(drawing, canvas, x, bottom)


def compose_pdf(content: CertificateContent, template: CertificateTemplate | None = None) -> bytes:
    """Bajty gotowego dokumentu – bez pieczęci i bez sięgania do rejestru.

    Osobno od ``render_pdf``, bo tę samą kartę składa **podgląd szablonu** w panelu: koordynator
    ogląda tam dane przykładowe, żeby zobaczyć układ, zanim wystawi komukolwiek dokument. Gdyby
    podgląd szedł przez ``render_pdf``, musiałby wcześniej wystawić dyplom „na niby” i nadać mu
    numer z tej samej puli, co dokumentom prawdziwym.
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas as pdf_canvas

    register_fonts()
    layout = template.layout if template is not None else {}
    buffer = BytesIO()
    width, height = landscape(A4)
    canvas = pdf_canvas.Canvas(buffer, pagesize=landscape(A4))
    canvas.setTitle(f"{content.title} {content.number}")
    # Metadane dokumentu są częścią jego treści – w podglądzie PDF-a widać je przed otwarciem.
    canvas.setAuthor(content.author)
    canvas.setSubject(f"{content.title}, edycja {content.edition}")

    _draw_background(canvas, template, width=width, height=height)
    if template is not None:
        logo = block(layout, "logo")
        if is_visible(logo):
            _draw_picture(
                canvas,
                _image_bytes(template.logo),
                x=float(logo.get("x", 60)),
                y=height - float(logo.get("y", 40)) - float(logo.get("height", 60)),
                width=float(logo.get("width", 140)),
                height=float(logo.get("height", 60)),
            )

    organiser = block(layout, "organiser")
    _text(canvas, organiser.get("text", "OLIMPIADA KWANTOWA"), organiser, width=width, height=height)
    _text(canvas, f"edycja {content.edition}", block(layout, "edition"), width=width, height=height)
    _text(canvas, content.title, block(layout, "title"), width=width, height=height)
    _text(canvas, content.recipient, block(layout, "recipient"), width=width, height=height)
    _text(canvas, content.school, block(layout, "school"), width=width, height=height)
    _text(canvas, content.statement, block(layout, "statement"), width=width, height=height)
    _draw_workshops(canvas, content, layout=layout, width=width, height=height)
    _draw_signatures(
        canvas,
        template,
        layout=layout,
        width=width,
        height=height,
        signature_line=content.signature_line,
    )
    _draw_footer(canvas, content, layout=layout, width=width, height=height)
    _draw_qr(canvas, content, layout=layout, height=height)

    canvas.showPage()
    canvas.save()
    overlay = buffer.getvalue()
    return _merge_background(overlay, template)


def _draw_workshops(
    canvas, content: CertificateContent, *, layout: dict, width: float, height: float
) -> None:
    """Lista warsztatów: nagłówek i po jednym wierszu na zajęcia, od najwcześniejszych.

    Zaświadczenie z warsztatów bez wyliczenia tematów poświadcza „był na czymś” i jest w praktyce
    bezużyteczne – szkoła i komisja stypendialna pytają, **na czym**. Dlatego lista jest treścią
    tego dokumentu, a nie ozdobą, i dlatego rośnie w dół: przy dwunastu zajęciach kartka i tak
    kończy się wcześniej niż stopka, a wtedy pozostałe wiersze zastępuje jedno zdanie.
    """
    settings = block(layout, "workshops")
    if not content.workshops or not is_visible(settings):
        return
    size = settings.get("size", 11)
    leading = float(settings.get("leading", 15))
    y = height - float(settings.get("y", 372))
    # Dolna granica: pas stopki. Poniżej niej lista nachodziłaby na numer dokumentu.
    limit = height - float(block(layout, "signatures").get("y", 445)) + leading
    canvas.setFont(FONT_BOLD, size)
    canvas.drawCentredString(width / 2, y, WORKSHOP_LIST_HEADING)
    canvas.setFont(FONT_REGULAR, size)
    for index, line in enumerate(content.workshops):
        y -= leading
        if y < limit:
            canvas.drawCentredString(width / 2, y, f"… oraz {len(content.workshops) - index} kolejnych zajęć")
            return
        canvas.drawCentredString(width / 2, y, line)


def _merge_background(overlay: bytes, template: CertificateTemplate | None) -> bytes:
    """Nakłada złożoną stronę na **pierwszą stronę** tła w PDF-ie. Bez takiego tła – bez zmian.

    Dlaczego w ogóle przyjmujemy tło jako PDF, skoro obrazek jest prostszy. Bo projekt dyplomu
    przychodzi z drukarni jako PDF w CMYK-u, z osadzonymi krojami i spadami; przerobienie go na
    PNG kosztuje jakość, której na papierze nie da się odzyskać, a redaktor i tak nie ma czym
    tego zrobić.

    Skalowanie nie jest kosmetyką: projekt bywa w formacie ze spadami (nieco większym niż A4),
    a wtedy tekst złożony w A4 wylądowałby w lewym dolnym rogu arkusza zamiast na środku karty.
    """
    if template is None or not template.background:
        return overlay
    if Path(template.background.name).suffix.lower() != ".pdf":
        return overlay
    data = _image_bytes(template.background)
    if not data:
        return overlay
    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.errors import PyPdfError

    try:
        page = PdfReader(BytesIO(data), strict=False).pages[0]
        drawn = PdfReader(BytesIO(overlay), strict=False).pages[0]
        page.merge_transformed_page(
            drawn,
            Transformation().scale(
                float(page.mediabox.width) / PAGE_WIDTH,
                float(page.mediabox.height) / PAGE_HEIGHT,
            ),
        )
        writer = PdfWriter()
        writer.add_page(page)
        merged = BytesIO()
        writer.write(merged)
        return merged.getvalue()
    except (PyPdfError, IndexError, ValueError):
        # Uszkodzone albo puste tło nie może zabrać uczestnikowi dyplomu – wychodzi sam tekst.
        logger.warning("Nie udało się nałożyć dokumentu na tło PDF szablonu %s.", template.pk)
        return overlay


def render_pdf(certificate: Certificate) -> bytes:
    """Składa dokument, pieczętuje go (gdy skonfigurowano) i zwraca bajty PDF-a.

    Układ jest celowo prosty i **poziomy** (A4 landscape): dyplom jest jedną stroną z nazwiskiem
    pośrodku, a nie formularzem. Wszystko, co identyfikuje dokument (numer, kod weryfikacyjny,
    adres strony weryfikacji, QR), stoi w stopce – tam szuka się tego, sprawdzając cudzy dyplom.
    """
    content = certificate_content(certificate)
    template = resolve_template(certificate.kind, certificate.edition)
    signed = sign_document(compose_pdf(content, template))
    _remember_signature(certificate, signed)
    return signed.data


def _remember_signature(certificate: Certificate, signed: SignedDocument) -> None:
    """Zapisuje przy dokumencie, czy **ten** skład wyszedł z pieczęcią.

    Zapis w trakcie pobierania wygląda na dziwactwo, a jest jedyną uczciwą odpowiedzią na to, jak
    ten dokument istnieje: PDF powstaje przy każdym pobraniu, więc pieczęć jest własnością chwili
    składu, a nie rejestru. Certyfikat mógł wygasnąć, plik klucza zniknąć z wolumenu – i wtedy
    dokument nadal wychodzi, tylko bez pieczęci. Strona weryfikacji ma o tym mówić prawdę.

    Piszemy **wyłącznie przy zmianie** i tylko trzy kolumny: pobranie paczki z całym finałem nie
    ma robić kilkuset zapisów po to, żeby przepisać te same wartości.
    """
    if certificate.pk is None:
        return
    unchanged = certificate.signed == signed.signed and certificate.signer_name == signed.signer_name
    if unchanged:
        return
    certificate.signed = signed.signed
    certificate.signer_name = signed.signer_name
    certificate.signed_at = timezone.now() if signed.signed else None
    certificate.save(update_fields=["signed", "signed_at", "signer_name"])


def pdf_filename(certificate: Certificate) -> str:
    """Nazwa pliku do pobrania: ``dyplom-OK-2026-12.pdf``.

    Bez nazwiska: plik bywa przekazywany dalej i zapisywany na wspólnych dyskach, a numer
    identyfikuje dokument jednoznacznie. Ukośniki numeru zamieniamy na myślniki – inaczej
    nazwa pliku wyglądałaby jak ścieżka.
    """
    return f"dyplom-{certificate.number.replace('/', '-')}.pdf"


# --- paczka ZIP ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class CertificateArchive:
    """Gotowe archiwum: otwarty plik tymczasowy na początku i liczba dokumentów w środku."""

    stream: BinaryIO
    count: int


def build_certificates_zip(certificates: list[Certificate]) -> CertificateArchive:
    """Paczka ZIP z gotowymi dokumentami.

    Własny, krótki budowniczy zamiast ``apps.submissions.packaging.build_zip``: tamten przepisuje
    do archiwum **strumienie ze storage** dla podanych rozwiązań i cała jego wartość polega na
    tym, że nie trzyma plików w pamięci. Tutaj plików w storage nie ma – każdy dokument powstaje
    w locie i ma kilkadziesiąt kilobajtów – więc dopasowanie tamtego podpisu do bajtów znaczyłoby
    przerobienie działającego kodu ścieżki krytycznej po to, żeby oszczędzić dwadzieścia linii.

    Archiwum powstaje mimo to w pliku tymczasowym, a nie w pamięci: przy całym finale to nadal
    kilkaset dokumentów, a ``FileResponse`` i tak zamknie strumień po wysłaniu.
    """
    stream = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for certificate in certificates:
                archive.writestr(pdf_filename(certificate), render_pdf(certificate))
    except BaseException:
        stream.close()
        raise
    stream.seek(0)
    return CertificateArchive(stream=stream, count=len(certificates))


# --- weryfikacja publiczna ----------------------------------------------------------------------


def _may_show_name(certificate: Certificate) -> bool:
    """Czy na stronie weryfikacji wolno podać nazwisko odbiorcy.

    Dla uczestnika: wyłącznie przy aktywnej zgodzie na publikację pełnych danych. Sprawdzamy
    **dowód** (``ConsentRecord``) razem z projekcją w profilu, bo to zgoda jest tu podstawą,
    a nie wygodne pole – a dowód potrafi być wycofany bez dotknięcia projekcji przez kogoś,
    kto pisze do bazy z boku.

    Dla opiekuna: nigdy. Opiekun nie przechodzi przez blok zgód rejestracji uczestnika, więc
    nie ma czego sprawdzać – a milczenie nie jest zgodą.
    """
    if certificate.is_for_supervisor:
        return False
    participant = certificate.entry.participant
    if not participant.publish_full_name:
        return False
    from apps.accounts.consents import ConsentKind

    return participant.consents.filter(kind=ConsentKind.PUBLISH_NAME, withdrawn_at__isnull=True).exists()


def verify(code: str) -> dict | None:
    """Dane strony ``/dyplomy/<kod>/`` albo ``None``, gdy takiego dokumentu nie ma.

    Zwracamy słownik, a nie obiekt ``Certificate``: strona jest publiczna, więc to **tu** musi
    zapaść decyzja, co w ogóle opuszcza system. Szablon, który dostałby cały obiekt, mógłby
    sięgnąć po dowolne pole odbiorcy przy pierwszej nieuważnej zmianie.
    """
    cleaned = (code or "").strip().upper()
    if not cleaned:
        return None
    certificate = (
        Certificate.objects.filter(code=cleaned)
        .select_related(
            "edition",
            "entry__participant__user",
            "supervisor__user",
        )
        .first()
    )
    if certificate is None:
        return None
    content = certificate_content(certificate)
    return {
        "valid": True,
        "kind": certificate.get_kind_display(),
        "title": content.title,
        "edition": content.edition,
        "number": content.number,
        "issued_on": content.issued_on,
        # ``None`` znaczy „odbiorca nie zgodził się na publikację nazwiska”, a nie „brak danych”.
        # Strona mówi to wprost, żeby nikt nie wziął pustego miejsca za wadę dokumentu.
        "recipient": content.recipient if _may_show_name(certificate) else None,
        # Pieczęć elektroniczna dotyczy **pliku**, a nie wpisu w rejestrze, więc strona mówi
        # o niej osobno: dokument jest ważny także bez niej (potwierdza go ten właśnie kod),
        # a jej obecność odpowiada dodatkowo na pytanie „czy tego PDF-a nie ruszono”.
        "signed": certificate.signed,
        "signer_name": certificate.signer_name,
    }


# --- podgląd szablonu ---------------------------------------------------------------------------

#: Dane przykładowe podglądu. Nazwisko z kompletem polskich znaków, bo to jest pierwsza rzecz,
#: którą trzeba zobaczyć na cudzym tle: czy krój ze szablonu ma ``ą``, ``ś`` i ``ż``.
SAMPLE_NUMBER = "OK/2026/000"
SAMPLE_CODE = "PRZYKLADOWY1"


def sample_content(kind: str, edition: Edition | None = None) -> CertificateContent:
    """Treść dokumentu „na niby” – do podglądu szablonu w panelu.

    Numer jest spoza puli (``000``), a kod jawnie nieprawdziwy: podgląd bywa drukowany i pokazywany
    na posiedzeniu komitetu, a wydruk z numerem wyglądającym na prawdziwy to dokument, którego
    nikt nie wystawił.

    Napisy idą wersją **obowiązującą** (``version=None``), a nie zapamiętaną – podgląd pokazuje
    dokument, jaki wyjdzie po dzisiejszym „Wystaw”. Konkurs bierze się z edycji; bez edycji nie
    ma czyjego szablonu czytać i zostają dzisiejsze stałe.
    """
    competition = edition.competition if edition is not None else None
    recipient = "Łucja Śniadecka"
    school = "XIV Liceum Ogólnokształcące w Warszawie"
    year_label = edition.year_label if edition is not None else "2026/2027"
    issued_on = timezone.localtime(timezone.now()).strftime("%d.%m.%Y")
    document = render_document(
        competition,
        kind,
        fallback=document_fallback(kind),
        recipient=recipient,
        school=school,
        edition=year_label,
        number=SAMPLE_NUMBER,
        code=SAMPLE_CODE,
        date=issued_on,
    )
    return CertificateContent(
        title=document.title,
        statement=document.statement,
        recipient=recipient,
        school=school,
        edition=year_label,
        number=SAMPLE_NUMBER,
        code=SAMPLE_CODE,
        issued_on=issued_on,
        workshops=(
            "12.11.2026 – Kubity i bramki kwantowe, prowadzi: dr Anna Kowalska",
            "10.12.2026 – Splątanie i nierówności Bella, prowadzi: prof. Jan Nowak",
        )
        if kind == CertificateKind.WARSZTATY
        else (),
        # Bez ``competition``: adres podglądu zostaje taki, jak przed etapem 2 (konkurs
        # z kontekstu żądania), bo podgląd ogląda się zawsze we własnym panelu.
        verification_url=verification_url(SAMPLE_CODE),
        signature_line=document.signature_line,
        author=document.author,
        template_version=document.version,
    )
