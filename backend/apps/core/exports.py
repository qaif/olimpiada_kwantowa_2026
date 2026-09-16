"""Eksport danych edycji do CSV i XLSX (panel koordynatora).

Moduł ma dwie warstwy i celowo trzyma je osobno:

- **zbiory danych** (``participant_dataset``, ``stage_results_dataset``, ``stage_reviews_dataset``) –
  każdy zwraca ``Dataset``: nagłówek, **generator** wierszy i ich liczbę. Generator, a nie lista,
  bo eksport uczestników edycji to kilka tysięcy wierszy i odpowiedź ma zacząć się sypać do
  przeglądarki, zanim policzymy ostatni z nich,
- **formaty** (``csv_response``, ``xlsx_response``) – zamiana tego samego ``Dataset`` w plik.
  Dzięki temu dołożenie trzeciego zbioru danych nie dotyka formatów, a dołożenie formatu nie
  dotyka zapytań.

Liczba wierszy jest częścią zbioru danych, a nie skutkiem ubocznym wysyłania pliku, i to nie jest
drobiazg: wpis audytowy ``export.generated`` musi powstać **przed** oddaniem strumienia. Gdyby
liczbę dawało dopiero przejście generatora, audyt zapisywałby się już w trakcie odpowiedzi – czyli
także wtedy, gdy klient rozłączy się w połowie, i nie zapisywałby się wcale, gdyby połączenie
padło na pierwszym wierszu.

Dlaczego CSV leci strumieniem, a XLSX nie: plik .xlsx jest archiwum ZIP, którego nie da się
zamknąć, zanim zapisze się ostatni wiersz – strumień nic by tu nie dał. CSV natomiast jest
tekstem linia po linii i ``StreamingHttpResponse`` pozwala oddać go bez trzymania całości
w pamięci procesu.

RODO: eksport jest **jedynym** miejscem poza panelem, z którego dane osobowe wychodzą z systemu
w komplecie, i wolno go użyć wyłącznie koordynatorowi (widok pilnuje roli). Sam fakt pobrania
zostaje w audycie razem z rodzajem eksportu i liczbą wierszy – nigdy z danymi. Bez tego wpisu nie
da się później odpowiedzieć, kto i kiedy wyniósł listę uczestników.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Znacznik kolejności bajtów na początku pliku CSV. Bez niego Excel w polskiej lokalizacji
#: czyta UTF-8 jako stronę kodową Windows i „Łódź” zamienia w „ĹĂłdĹş”. Koordynator otwiera ten
#: plik w Excelu, a nie w edytorze tekstu, więc BOM jest tu wygodą, a nie ozdobą.
BOM = "﻿"

#: Separator pól. Średnik, nie przecinek: polski Excel dzieli kolumny po średniku (bo przecinek
#: jest separatorem dziesiętnym), a plik z przecinkami otwiera jako jedną kolumnę.
CSV_DELIMITER = ";"


@dataclass(frozen=True)
class Dataset:
    """Jeden zbiór danych do eksportu: nagłówek, wiersze i ich liczba.

    ``rows`` jest generatorem i wolno przejść go **raz** – oba formaty tak właśnie robią.
    ``count`` pochodzi z osobnego, taniego zapytania (albo z długości już policzonej listy), więc
    da się go odczytać bez ruszania generatora.
    """

    header: list[str]
    rows: Iterator[list]
    count: int
    title: str
    filename: str = field(default="eksport")


class _Echo:
    """Bufor, który niczego nie buforuje – ``write`` zwraca to, co dostał.

    ``csv.writer`` umie pisać wyłącznie do obiektu z metodą ``write``. Podstawiając mu taki
    „bufor”, dostajemy z powrotem gotowy wiersz jako napis i możemy go oddać generatorem zamiast
    zbierać całość w pamięci (wzorzec z dokumentacji Django).
    """

    def write(self, value: str) -> str:
        return value


def _stamp(now=None) -> str:
    """Znacznik czasu w nazwie pliku. Lokalny, bo nazwę czyta człowiek, a nie maszyna."""
    return timezone.localtime(now or timezone.now()).strftime("%Y%m%d-%H%M")


def _cell(value) -> str | int:
    """Wartość do komórki: ``None`` jako pusta, data w czasie lokalnym, prawda/fałsz po polsku.

    Daty sprowadzamy do czasu lokalnego w jednym miejscu, bo arkusz z godzinami w UTC jest
    pułapką: nikt przy nim nie pamięta o przesunięciu, a różnica jest akurat na tyle mała,
    żeby wyglądać wiarygodnie. Liczby zostają liczbami – w arkuszu mają się sumować.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "tak" if value else "nie"
    if isinstance(value, datetime):
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, int):
        return value
    return str(value)


def csv_response(dataset: Dataset) -> StreamingHttpResponse:
    """Odpowiedź CSV oddawana strumieniem, wiersz po wierszu."""
    writer = csv.writer(_Echo(), delimiter=CSV_DELIMITER, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

    def _stream() -> Iterator[str]:
        yield BOM + writer.writerow(dataset.header)
        for row in dataset.rows:
            yield writer.writerow([_cell(value) for value in row])

    response = StreamingHttpResponse(_stream(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{dataset.filename}-{_stamp()}.csv"'
    return response


def xlsx_response(dataset: Dataset) -> HttpResponse:
    """Odpowiedź XLSX: jeden arkusz, pierwszy wiersz pogrubiony i zamrożony.

    ``write_only`` w ``openpyxl``: arkusz w tym trybie oddaje wiersze do pliku na bieżąco i nie
    trzyma komórek w pamięci. Przy kilku tysiącach uczestników zwykły tryb potrafi zająć kilkaset
    megabajtów, a przedmiotem eksportu jest tabela, nie formatowany dokument – jedynym stylem
    jest tu pogrubiony nagłówek, który w tym trybie zapisuje się jako ``WriteOnlyCell``.
    """
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook(write_only=True)
    # Nazwa arkusza w formacie XLSX ma twardy limit 31 znaków – dłuższą Excel odrzuca jako
    # uszkodzony plik, więc przycinamy ją tutaj, a nie w wołających.
    sheet = workbook.create_sheet(title=dataset.title[:31])
    sheet.freeze_panes = "A2"
    bold = Font(bold=True)
    header_cells = []
    for index, label in enumerate(dataset.header, start=1):
        # Szerokości kolumn ustawia się przed zapisaniem pierwszego wiersza; po nagłówku, bo
        # ``write_only`` nie pozwala wrócić do tego, co już poszło do pliku.
        sheet.column_dimensions[get_column_letter(index)].width = max(12, min(40, len(label) + 4))
        cell = WriteOnlyCell(sheet, value=label)
        cell.font = bold
        header_cells.append(cell)
    sheet.append(header_cells)
    for row in dataset.rows:
        sheet.append([_cell(value) for value in row])
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{dataset.filename}-{_stamp()}.xlsx"'
    workbook.save(response)
    return response


#: Nazwa formatu w adresie → funkcja budująca odpowiedź. Zamknięta lista, bo parametr przychodzi
#: z adresu: nieznany format ma dać 404, a nie próbę zawołania czegokolwiek.
FORMATS = {"csv": csv_response, "xlsx": xlsx_response}


def build_response(dataset: Dataset, fmt: str) -> HttpResponse | StreamingHttpResponse:
    """Plik w zadanym formacie. ``KeyError`` przy nieznanym – wołający zamienia go na 404."""
    return FORMATS[fmt](dataset)


# --- zbiory danych ------------------------------------------------------------------------------


def _consent_columns() -> list[str]:
    """Kolumny zgód – po trzy na każdy rodzaj, w kolejności z ``apps.accounts.consents``.

    Kolejność jest ta sama, co w formularzu rejestracji, żeby arkusz dało się czytać obok niego.
    Lista rodzajów pochodzi z modułu zgód, a nie z literału tutaj: dołożenie zgody ma dołożyć
    kolumny samo, bo eksport bez jednej ze zgód jest gorszy niż brak eksportu.
    """
    from apps.accounts.consents import CONSENTS

    columns: list[str] = []
    for consent in CONSENTS:
        label = consent.kind
        columns += [f"{label}: stan", f"{label}: wersja dokumentu", f"{label}: data"]
    return columns


PARTICIPANT_HEADER_BASE = [
    "kod publiczny",
    "imię",
    "nazwisko",
    "e-mail",
    "szkoła",
    "klasa",
    "województwo",
    "rok urodzenia",
    "telefon",
    "konto aktywne",
    "adres potwierdzony",
]


def participant_dataset(edition) -> Dataset:
    """Uczestnicy edycji: dane kontaktowe, szkoła i komplet zgód z wersjami dokumentów.

    „Uczestnik edycji” to ktoś z wpisem do któregokolwiek jej etapu – ta sama definicja, co
    w wysyłce komunikatów (``apps.accounts.messaging``). Lista kont, które nigdy się nie zapisały,
    jest czymś innym i ma własne miejsce (``/coordinator/accounts/``).

    Zgody bierzemy z **dowodów** (``ConsentRecord``), a nie z projekcji na profilu: eksport służy
    do odpowiadania na pytania „na co ta osoba się zgodziła i w jakiej wersji dokumentu”, a to wie
    wyłącznie rejestr zdarzeń. Z każdego rodzaju pokazujemy wpis najnowszy – ``prefetch_related``
    czyta je hurtem, więc kolumny zgód nie kosztują zapytania na wiersz.
    """
    from apps.accounts.consents import CONSENTS
    from apps.accounts.models import Participant

    participants = (
        Participant.objects.filter(stage_entries__stage__edition=edition)
        .select_related("user")
        .prefetch_related("consents")
        .distinct()
        .order_by("public_code")
    )

    def _rows() -> Iterator[list]:
        for participant in participants:
            user = participant.user
            latest = {}
            for record in participant.consents.all():
                current = latest.get(record.kind)
                if current is None or record.given_at > current.given_at:
                    latest[record.kind] = record
            row = [
                participant.public_code,
                user.first_name,
                user.last_name,
                user.email,
                participant.school,
                participant.grade,
                participant.get_district_display(),
                participant.birth_year,
                participant.phone,
                user.is_active,
                user.email_verified_at,
            ]
            for consent in CONSENTS:
                record = latest.get(consent.kind)
                if record is None:
                    row += ["brak", "", ""]
                else:
                    row += [
                        "wycofana" if record.withdrawn_at else "aktywna",
                        record.document_version,
                        record.withdrawn_at or record.given_at,
                    ]
            yield row

    return Dataset(
        header=[*PARTICIPANT_HEADER_BASE, *_consent_columns()],
        rows=_rows(),
        count=participants.count(),
        title="Uczestnicy",
        filename=f"uczestnicy-{edition.year_label}".replace("/", "-").replace(" ", "_"),
    )


def stage_results_dataset(stage) -> Dataset:
    """Wyniki etapu: kod, nazwisko, punkty za zadania, suma, miejsce i kwalifikacja.

    Źródłem są **dane bieżące**, a nie snapshot publikacji, i to jest świadome: koordynator
    eksportuje wyniki po to, żeby je sprawdzić albo przekazać komisji, a nie żeby odtworzyć
    ogłoszoną tabelę – ta jest pod publicznym adresem i z założenia zamrożona. Dlatego arkusz
    niesie też nazwiska, których publikacja nie pokazuje.

    Liczymy w trybie podglądu (``compute_stage_results(preview=True)``), więc eksport działa także
    w trakcie oceniania: praca bez oceny liczy się wtedy jako zero punktów. Kolumna „kwalifikuje
    się” korzysta z progu zapisanego przy etapie; etap bez progu ma tam wszędzie „nie”.
    """
    from apps.competitions.models import StageEntryStatus
    from apps.results.services import _qualified_entry_ids, compute_stage_results

    problems = list(stage.problems.order_by("number", "id"))
    header = [
        "miejsce",
        "kod publiczny",
        "imię",
        "nazwisko",
        "szkoła",
        "województwo",
        *[f"zadanie {problem.number}" for problem in problems],
        "razem",
        "status wpisu",
        "kwalifikuje się",
    ]
    rows = compute_stage_results(stage, preview=True)
    rule = getattr(stage, "qualification_rule", None)
    qualified: set[int] = set()
    if rule is not None:
        candidates = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
        qualified = _qualified_entry_ids(candidates, rule)

    def _rows() -> Iterator[list]:
        for row in rows:
            yield [
                row["rank"],
                row["public_code"],
                row["first_name"],
                row["last_name"],
                row["school"],
                row["district"],
                *[row["points"].get(str(problem.number), 0) for problem in problems],
                row["total"],
                row["status"],
                row["entry_id"] in qualified,
            ]

    return Dataset(
        header=header,
        rows=_rows(),
        count=len(rows),
        title="Wyniki etapu",
        filename=f"wyniki-etap-{stage.pk}",
    )


def stage_reviews_dataset(stage) -> Dataset:
    """Recenzje etapu: identyfikator, pseudonim pracy, zadanie, recenzent, stan, punkty, daty.

    Arkusz jest materiałem do rozliczenia pracy komitetu, więc niesie adres recenzenta – to
    jedyna kolumna z danymi osobowymi i jedyny sposób, żeby powiedzieć, czyja to recenzja.
    Uczestnik występuje wyłącznie pod pseudonimem: ocenianie jest ślepe, a zestawienie recenzji
    nie jest powodem, żeby to znosić.
    """
    from apps.grading.models import Review

    header = [
        "id recenzji",
        "kod uczestnika",
        "zadanie",
        "runda",
        "recenzent (e-mail)",
        "stan",
        "punkty",
        "przydzielona",
        "wystawiona",
    ]
    reviews = (
        Review.objects.filter(submission__entry__stage=stage)
        .select_related("reviewer__user", "submission__problem", "submission__entry__participant")
        .order_by("submission__problem__number", "submission_id", "round", "id")
    )

    def _rows() -> Iterator[list]:
        for review in reviews:
            yield [
                review.pk,
                review.submission.entry.participant.public_code,
                review.submission.problem.number,
                review.round,
                review.reviewer.user.email,
                review.get_status_display(),
                review.score,
                review.assigned_at,
                review.submitted_at,
            ]

    return Dataset(
        header=header,
        rows=_rows(),
        count=reviews.count(),
        title="Recenzje etapu",
        filename=f"recenzje-etap-{stage.pk}",
    )
