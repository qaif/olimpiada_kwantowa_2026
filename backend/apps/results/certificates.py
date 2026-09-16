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
from django.utils import timezone
from rest_framework import status as http

from apps.accounts.models import SchoolSupervisor
from apps.competitions.models import Edition, StageEntry
from apps.core.api import DomainError
from apps.core.models import audit

from .models import CERTIFICATE_NUMBER_PREFIX, Certificate, CertificateKind

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
}

#: Zdanie pod nazwiskiem. Rodzaj dokumentu mówi, **co** poświadczamy; to zdanie mówi to samo
#: językiem, w którym pisze się dokumenty – i to ono jest właściwą treścią dyplomu.
DOCUMENT_STATEMENTS = {
    CertificateKind.LAUREAT: "uzyskał(a) tytuł laureata Olimpiady Kwantowej",
    CertificateKind.FINALISTA: "uzyskał(a) tytuł finalisty Olimpiady Kwantowej",
    CertificateKind.UCZESTNIK: "brał(a) udział w Olimpiadzie Kwantowej",
    CertificateKind.OPIEKUN: "sprawował(a) opiekę nad uczestnikami Olimpiady Kwantowej",
}

#: Nazwa organizatora nad linią podpisu. Stała, bo na papierze podpisuje się komitet jako organ,
#: a nie osoba, która akurat kliknęła „Wystaw”.
SIGNATURE_LINE = "Przewodniczący Komitetu Głównego Olimpiady Kwantowej"


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


# --- rejestr ------------------------------------------------------------------------------------


def _next_number(year: int) -> str:
    """Kolejny numer w roku: ``OK/<rok>/<liczba>``.

    Numerację prowadzimy po **roku kalendarzowym wystawienia**, a nie po edycji: tak numeruje się
    dokumenty w sekretariacie i tak są cytowane w pismach. Kolejność wyliczamy z najwyższego
    dotychczasowego numeru, a nie z licznika wierszy – skasowany kiedyś wiersz nie może sprawić,
    że numer zostanie wydany komuś drugi raz.
    """
    prefix = f"{CERTIFICATE_NUMBER_PREFIX}/{year}/"
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
    for attempt in range(MAX_NUMBER_ATTEMPTS):
        try:
            with transaction.atomic():
                certificate = Certificate.objects.create(
                    edition=edition,
                    entry=entry,
                    supervisor=supervisor,
                    kind=kind,
                    number=_next_number(year),
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


def certificate_content(certificate: Certificate) -> CertificateContent:
    """Składa treść dokumentu z faktów w bazie. Jedno miejsce dla PDF-a i dla testów."""
    return CertificateContent(
        title=DOCUMENT_TITLES.get(certificate.kind, DOCUMENT_TITLES[CertificateKind.UCZESTNIK]),
        statement=DOCUMENT_STATEMENTS.get(certificate.kind, DOCUMENT_STATEMENTS[CertificateKind.UCZESTNIK]),
        recipient=_recipient_name(certificate),
        school=_recipient_school(certificate),
        edition=certificate.edition.year_label,
        number=certificate.number,
        code=certificate.code,
        issued_on=timezone.localtime(certificate.issued_at).strftime("%d.%m.%Y"),
    )


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


def _centered(canvas, text: str, *, y: float, width: float, font: str, size: int) -> None:
    """Jedna wyśrodkowana linia. Osobna funkcja, bo dyplom składa się prawie z samych takich."""
    canvas.setFont(font, size)
    canvas.drawCentredString(width / 2, y, text)


def render_pdf(certificate: Certificate) -> bytes:
    """Składa dokument i zwraca bajty PDF-a.

    Układ jest celowo prosty i **poziomy** (A4 landscape): dyplom jest jedną stroną z nazwiskiem
    pośrodku, a nie formularzem. Wszystko, co identyfikuje dokument (numer, kod weryfikacyjny,
    adres strony weryfikacji), stoi w stopce – tam szuka się tego, sprawdzając cudzy dyplom.
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas as pdf_canvas

    register_fonts()
    content = certificate_content(certificate)
    buffer = BytesIO()
    width, height = landscape(A4)
    canvas = pdf_canvas.Canvas(buffer, pagesize=landscape(A4))
    canvas.setTitle(f"{content.title} {content.number}")
    # Metadane dokumentu są częścią jego treści – w podglądzie PDF-a widać je przed otwarciem.
    canvas.setAuthor("Olimpiada Kwantowa")
    canvas.setSubject(f"{content.title}, edycja {content.edition}")

    _centered(canvas, "OLIMPIADA KWANTOWA", y=height - 90, width=width, font=FONT_BOLD, size=22)
    _centered(canvas, f"edycja {content.edition}", y=height - 120, width=width, font=FONT_REGULAR, size=13)
    _centered(canvas, content.title, y=height - 190, width=width, font=FONT_BOLD, size=28)
    _centered(canvas, content.recipient, y=height - 260, width=width, font=FONT_BOLD, size=24)
    if content.school:
        _centered(canvas, content.school, y=height - 288, width=width, font=FONT_REGULAR, size=13)
    _centered(canvas, content.statement, y=height - 330, width=width, font=FONT_REGULAR, size=15)

    # Linia podpisu: sama kreska i podpis pod nią. Dokument wychodzi z systemu bez podpisu
    # elektronicznego, więc miejsce na podpis odręczny musi na nim być naprawdę.
    canvas.setLineWidth(0.7)
    canvas.line(width / 2 - 120, 150, width / 2 + 120, 150)
    _centered(canvas, SIGNATURE_LINE, y=133, width=width, font=FONT_REGULAR, size=10)

    canvas.setFont(FONT_REGULAR, 9)
    canvas.drawString(60, 70, f"Numer dokumentu: {content.number}")
    canvas.drawString(60, 56, f"Kod weryfikacyjny: {content.code}")
    canvas.drawRightString(width - 60, 70, f"Data wystawienia: {content.issued_on}")
    canvas.drawRightString(width - 60, 56, "Weryfikacja: /dyplomy/<kod>/ w serwisie olimpiady")

    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


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
    }
