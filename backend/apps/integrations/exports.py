"""Eksporty na zewnątrz: lista dla kuratorium, protokół etapu, listy pobytowe i zrzut edycji.

Cztery różne dokumenty, bo cztery różne pytania z zewnątrz:

- **lista dla kuratorium** (CSV/XLSX, jedno województwo) – kurator pyta „którzy uczniowie z mojego
  województwa wzięli udział i z jakim skutkiem”. Kolumny są takie, jakich kuratoria żądają
  w pismach o zwolnienie z egzaminu: kod, imię, nazwisko, szkoła, miejscowość, klasa, wynik,
  kwalifikacja. Zbiór danych powstaje tutaj, a nie w ``apps.core.exports``, bo to eksport
  **na zewnątrz**, o innym odbiorcy i innej podstawie prawnej niż arkusze robocze koordynatora,
- **protokół etapu** (PDF) – dokument, który komitet podpisuje i wkłada do teczki zawodów.
  Dlatego jest PDF-em z blokiem podpisów, a nie arkuszem: arkusza się nie podpisuje,
- **listy pobytowe** (PDF: obecności, noclegowa, żywieniowa) – recepcja obiektu i kuchnia pytają
  „ile osób i kto”, a komisja niesie na salę dokument z rubryką na podpis. Istnieją wyłącznie
  w konkursie z flagą ``onsite_logistics`` (§ 1.5.2) i składają się tą samą ścieżką ReportLab,
  co protokół – nowy generator PDF nie powstaje,
- **zrzut edycji** (JSON) – przeniesienie zawodów do innego systemu albo archiwum. Struktura plus
  kody publiczne, **bez ani jednego nazwiska**: migracja danych osobowych jest osobną decyzją
  i osobną umową, a nie skutkiem ubocznym eksportu struktury.

Wspólne dla wszystkich czterech: wychodzą wyłącznie z panelu koordynatora, a każde pobranie
zostawia wpis audytowy z liczbą wierszy (robi to widok, tak samo jak przy eksportach w
``apps.core.exports``). Eksport jest jedyną drogą, którą komplet danych opuszcza system –
i ma być drogą widoczną.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from io import BytesIO

from django.utils import timezone

from apps.accounts.models import Voivodeship
from apps.core.exports import Dataset

logger = logging.getLogger(__name__)

#: Nagłówek listy dla kuratorium. Kolejność jest częścią kontraktu z odbiorcą: pisma kuratoriów
#: wskazują kolumny w tej właśnie kolejności, a arkusz, w którym trzeba je przestawiać, wraca
#: do nas z prośbą o poprawkę.
KURATORIUM_HEADER = [
    "kod publiczny",
    "imię",
    "nazwisko",
    "szkoła",
    "miejscowość",
    "klasa",
    "wynik",
    "kwalifikacja",
]

#: Etykieta kwalifikacji w arkuszu. Słowo, a nie „tak/nie”: kurator czyta wiersz bez nagłówka
#: nad palcem i „zakwalifikowany” znaczy to samo w każdym kontekście.
QUALIFICATION_LABELS = {True: "zakwalifikowany", False: "niezakwalifikowany"}
DISQUALIFIED_LABEL = "zdyskwalifikowany"


def voivodeship_label(value: str) -> str:
    """Etykieta województwa z jego sluga („lodzkie” → „łódzkie”). Nieznana wartość wraca bez zmian."""
    try:
        return Voivodeship(value).label
    except ValueError:
        return value


def _participant_details(rows: list[dict]) -> dict[int, tuple[str, int | None]]:
    """Miejscowość szkoły i klasa uczestników z tabeli wyników – jednym zapytaniem na całą listę.

    Obie kolumny są dobierane osobno, bo ``compute_stage_results`` ich nie niesie: tamta funkcja
    liczy **punkty**, a kuratorium pyta też o kontekst szkolny. Dokładanie pól do wiersza wyników
    tylko dla tego eksportu obciążyłoby publikację wyników danymi, których publikacja nie używa.

    Miejscowość ma wyłącznie szkoła ze słownika SIO. Przy szkole wpisanej ręcznie kolumna zostaje
    pusta – wyłuskiwanie miasta z nazwy zgadywaniem dałoby dane, którym w piśmie do kuratorium
    nie wolno ufać.
    """
    from apps.accounts.models import Participant

    ids = [row["participant_id"] for row in rows]
    return {
        pk: (city or "", grade)
        for pk, city, grade in Participant.objects.filter(pk__in=ids).values_list(
            "pk", "school_ref__city", "grade"
        )
    }


def kuratorium_rows(stage, voivodeship: str) -> tuple[list[dict], set[int]]:
    """Wiersze tabeli wyników etapu zawężone do jednego województwa plus zbiór zakwalifikowanych.

    Liczymy tą samą funkcją, co eksport wyników koordynatora (``compute_stage_results`` w trybie
    podglądu), żeby lista dla kuratorium i arkusz w panelu nie mogły pokazać dwóch różnych
    wyników tego samego ucznia. Praca bez oceny liczy się wtedy jako zero punktów.
    """
    from apps.competitions.models import StageEntryStatus
    from apps.results.services import _qualified_entry_ids, compute_stage_results

    label = voivodeship_label(voivodeship)
    rows = [row for row in compute_stage_results(stage, preview=True) if row["district"] == label]
    rule = getattr(stage, "qualification_rule", None)
    qualified: set[int] = set()
    if rule is not None:
        candidates = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
        qualified = _qualified_entry_ids(candidates, rule)
    return rows, qualified


def kuratorium_dataset(stage, voivodeship: str) -> Dataset:
    """Lista uczestników jednego województwa w jednym etapie – dla kuratorium oświaty.

    Zawężenie do województwa jest **warunkiem**, a nie filtrem do wyboru: kuratorium ma prawo do
    danych uczniów ze swojego terenu i tylko z niego. Eksport „wszystkie województwa naraz” dla
    tego odbiorcy nie istnieje – od zestawień ogólnych są arkusze koordynatora.
    """
    from apps.competitions.models import StageEntryStatus

    rows, qualified = kuratorium_rows(stage, voivodeship)
    details = _participant_details(rows)

    def _rows() -> Iterator[list]:
        for row in rows:
            if row["status"] == StageEntryStatus.DISQUALIFIED:
                qualification = DISQUALIFIED_LABEL
            else:
                qualification = QUALIFICATION_LABELS[row["entry_id"] in qualified]
            city, grade = details.get(row["participant_id"], ("", None))
            yield [
                row["public_code"],
                row["first_name"],
                row["last_name"],
                row["school"],
                city,
                grade if grade is not None else "",
                row["total"],
                qualification,
            ]

    return Dataset(
        header=list(KURATORIUM_HEADER),
        rows=_rows(),
        count=len(rows),
        title=f"Kuratorium {voivodeship}"[:31],
        filename=f"kuratorium-{voivodeship}-etap-{stage.pk}",
    )


# --- protokół etapu (PDF) -----------------------------------------------------------------------

#: Podpisy pod blokiem podpisów protokołu. Trzy, bo tyle podpisów nosi protokół zawodów:
#: przewodniczący komitetu, sekretarz i członek komitetu jako świadek.
PROTOCOL_SIGNATURES = (
    "Przewodniczący Komitetu Sterującego",
    "Sekretarz Komitetu Sterującego",
    "Członek Komitetu Sterującego",
)

#: Ile wierszy tabeli mieści się na stronie protokołu. Tabela idzie kawałkami (``splitByRow``),
#: więc to tylko wskazówka dla nagłówka powtarzanego na każdej stronie.
PROTOCOL_ROWS_PER_PAGE = 28


def protocol_filename(stage) -> str:
    """Nazwa pliku protokołu: ``protokol-etap-<id>-<data>.pdf``. Bez nazwisk – plik krąży dalej."""
    return f"protokol-etap-{stage.pk}-{timezone.localtime().strftime('%Y%m%d')}.pdf"


def render_stage_protocol(stage) -> bytes:
    """Składa protokół etapu: nagłówek, tabela wyników i blok podpisów. Zwraca bajty PDF-a.

    Kroje DejaVu rejestruje ``apps.results.certificates.register_fonts`` – ta sama funkcja, co
    przy dyplomach, bo problem jest ten sam: wbudowane w reportlab fonty Type1 nie mają polskich
    znaków, a protokół z „Łukasz Śliwiński” złożonym z pustych prostokątów nie jest dokumentem.

    Skład idzie przez ``platypus`` (a nie po współrzędnych, jak dyplom), bo tabela protokołu ma
    nieznaną z góry długość: przy dwustu uczestnikach dokument ma się **sam** podzielić na strony
    i powtórzyć nagłówek tabeli. Dyplom jest jedną kartą i tam współrzędne są prostsze.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        LongTable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        TableStyle,
    )

    from apps.competitions.models import StageEntryStatus
    from apps.results.certificates import FONT_BOLD, FONT_REGULAR, register_fonts
    from apps.results.services import _qualified_entry_ids, compute_stage_results

    register_fonts()
    rows = compute_stage_results(stage, preview=True)
    rule = getattr(stage, "qualification_rule", None)
    qualified: set[int] = set()
    if rule is not None:
        candidates = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
        qualified = _qualified_entry_ids(candidates, rule)

    title_style = ParagraphStyle("title", fontName=FONT_BOLD, fontSize=15, leading=19, spaceAfter=6)
    meta_style = ParagraphStyle("meta", fontName=FONT_REGULAR, fontSize=9.5, leading=13)
    sign_style = ParagraphStyle("sign", fontName=FONT_REGULAR, fontSize=9, leading=22)

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Protokół etapu {stage.pk}",
        author="Olimpiada Kwantowa",
    )

    table_data = [["Lp.", "Kod", "Imię i nazwisko", "Szkoła", "Woj.", "Punkty", "Kwalifikacja"]]
    for row in rows:
        if row["status"] == StageEntryStatus.DISQUALIFIED:
            qualification = DISQUALIFIED_LABEL
        else:
            qualification = QUALIFICATION_LABELS[row["entry_id"] in qualified]
        table_data.append(
            [
                str(row["rank"]),
                row["public_code"],
                f"{row['first_name']} {row['last_name']}".strip(),
                row["school"],
                row["district"],
                str(row["total"]),
                qualification,
            ]
        )

    table = LongTable(
        table_data,
        colWidths=[12 * mm, 22 * mm, 42 * mm, 46 * mm, 22 * mm, 16 * mm, 26 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                ("FONTNAME", (0, 1), (-1, -1), FONT_REGULAR),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                ("ALIGN", (5, 0), (5, -1), "RIGHT"),
            ]
        )
    )

    story = [
        Paragraph("Protokół z przebiegu etapu – Olimpiada Kwantowa", title_style),
        Paragraph(
            f"Edycja: {stage.edition.year_label}<br/>Etap: {stage.display_name}<br/>"
            f"Liczba uczestników: {len(rows)}<br/>Zakwalifikowanych: {len(qualified)}<br/>"
            f"Protokół sporządzono: {timezone.localtime().strftime('%Y-%m-%d %H:%M')}",
            meta_style,
        ),
        Spacer(1, 8 * mm),
        table,
        Spacer(1, 14 * mm),
        Paragraph(
            "Komitet Sterujący stwierdza zgodność powyższego zestawienia ze stanem systemu "
            "w chwili sporządzenia protokołu.",
            meta_style,
        ),
        Spacer(1, 12 * mm),
    ]
    for caption in PROTOCOL_SIGNATURES:
        story.append(Paragraph(f"…………………………………………&nbsp;&nbsp;{caption}", sign_style))
        story.append(Spacer(1, 6 * mm))

    document.build(story)
    return buffer.getvalue()


# --- listy do druku etapu stacjonarnego (PDF) ---------------------------------------------------
#
# Trzy listy, które organizator niesie ze sobą na salę i do recepcji obiektu: obecności, noclegowa
# i żywieniowa (``docs/UNIWERSALNY-ETAP-2.md`` § 1.5.2). Idą **tą samą** ścieżką ReportLab, co
# protokół etapu wyżej – ``register_fonts`` plus ``LongTable`` z ``repeatRows=1`` – bo problem jest
# ten sam: nieznana z góry liczba wierszy, polskie znaki i nagłówek powtarzany na każdej stronie.
# Nowy generator PDF nie powstaje ani tutaj, ani nigdzie indziej w tym wydaniu.
#
# Wszystkie trzy istnieją wyłącznie dla konkursu z flagą ``onsite_logistics``; dla pozostałych
# (w tym dla Konkursu #1) dane biorą się z funkcji, które oddają pustkę, więc lista wychodzi pusta
# i nie pada ani jedno zapytanie o logistykę.

#: Rodzaje list. Napisy, a nie ``TextChoices``: to nie jest kolumna w bazie, tylko wybór na ekranie
#: i fragment nazwy pliku.
LIST_ATTENDANCE = "attendance"
LIST_ACCOMMODATION = "accommodation"
LIST_MEAL = "meal"

#: Dzisiejszy tytuł na papierze – **odwrót** dla :func:`logistics_list_title`, a zarazem jedyne
#: miejsce, w którym ten napis stoi w kodzie. Od wejścia ``tenancy.DocumentTemplate`` rodzaju
#: ``ATTENDANCE_LIST`` (§ 1.1.3, zadania T12 i T13) tytuł listy obecności bierze się z szablonu
#: konkursu, a te wartości obowiązują przy wyłączonej fladze ``document_templates``.
LOGISTICS_LIST_TITLES = {
    LIST_ATTENDANCE: "Lista obecności",
    LIST_ACCOMMODATION: "Lista noclegowa",
    LIST_MEAL: "Lista żywieniowa",
}

#: Pusta rubryka do podpisu ręcznego. Lista obecności jest dokumentem, który podpisuje uczestnik
#: przy wejściu na salę – bez tej kolumny byłaby wydrukiem ekranu, a nie dokumentem.
SIGNATURE_COLUMN = "Podpis"


def logistics_list_title(stage, kind: str) -> str:
    """Tytuł listy na papierze: z szablonu konkursu albo z :data:`LOGISTICS_LIST_TITLES`.

    Szew do ``tenancy.DocumentTemplate`` (§ 1.1.3) jest tutaj, a nie w trzech wywołaniach w trzech
    funkcjach. Przy wyłączonej fladze ``document_templates`` – a więc w Olimpiadzie Kwantowej –
    ``render_document`` oddaje dosłownie dzisiejszą stałą i **nie pyta bazy o szablon ani razu**;
    to ten warunek z § 5.6, na którym stoją budżety zapytań.

    **Szablon dotyczy wyłącznie listy obecności** i to nie jest niedopatrzenie: rodzaj
    ``DocumentKind.ATTENDANCE_LIST`` opisuje jeden dokument – ten, który uczestnik podpisuje przy
    wejściu na salę. Lista noclegowa i żywieniowa są wydrukami roboczymi dla hotelu i dla kuchni,
    nie wychodzą poza organizatora i nie mają własnego rodzaju; puszczenie ich przez ten sam
    szablon dałoby trzy wydruki zatytułowane „Lista obecności”. Gdyby organizator kiedyś chciał
    zmieniać także tamte dwa tytuły, odpowiedzią są dwa nowe rodzaje dokumentu, a nie wspólny.
    """
    fallback = LOGISTICS_LIST_TITLES[kind]
    if kind != LIST_ATTENDANCE:
        return fallback
    from apps.competitions.logistics import competition_of_stage
    from apps.tenancy.documents import DocumentKind, render_document

    return render_document(
        competition_of_stage(stage),
        DocumentKind.ATTENDANCE_LIST,
        fallback={"title": fallback},
        stage=stage.display_name,
        date=timezone.localtime().strftime("%d.%m.%Y"),
    ).title


def logistics_list_filename(stage, kind: str) -> str:
    """Nazwa pliku listy: ``lista-<rodzaj>-etap-<id>-<data>.pdf``. Bez nazwisk – plik krąży dalej."""
    return f"lista-{kind}-etap-{stage.pk}-{timezone.localtime().strftime('%Y%m%d')}.pdf"


def logistics_list_rows(
    stage, kind: str, *, include_deleted: bool = True
) -> tuple[list[str], list[list[str]]]:
    """Nagłówek i wiersze jednej listy. Rozdzielone od składu, bo to jest to, co się sprawdza.

    Reguła danych szczególnych (decyzja organizatora D21) jest widoczna w kształcie wyniku:
    kolumna „Uwagi” istnieje **wyłącznie** na liście żywieniowej i **wyłącznie** w konkursie, który
    świadomie włączył zbieranie potrzeb szczególnych. Tam jest jedyne miejsce, w którym ta treść ma
    adresata – kuchnia musi wiedzieć, co przygotować. Na liście obecności i noclegowej uwagi nie ma
    w żadnym wariancie, bo tam nie jest do niczego potrzebna, a lista obecności krąży po sali.

    ``include_deleted`` – czy na liście stoją konta usunięte na żądanie (v0.34.0). Ekran
    koordynatora podaje tu stan przełącznika „Pokaż usunięte konta”, więc domyślnie wydruk ich
    nie ma; pokazane dostają w kolumnie nazwiska „Konto usunięte” zamiast pustego pola.
    """
    from apps.accounts.anonymised import DELETED_ACCOUNT_LABEL
    from apps.competitions.logistics import (
        LogisticsNeed,
        arrival_rows,
        attendance_rows,
        collects_special_needs,
        competition_of_stage,
        need_labels,
    )

    if kind == LIST_ATTENDANCE:
        header = ["Lp.", "Kod", "Imię i nazwisko", "Szkoła", "Miejsce", "Obecność", SIGNATURE_COLUMN]
        rows = [
            [
                str(number),
                row["public_code"],
                _logistics_name(row, DELETED_ACCOUNT_LABEL),
                row["school"],
                row["venue"],
                "obecny" if row["present"] else "",
                "",
            ]
            for number, row in enumerate(attendance_rows(stage, include_deleted=include_deleted), start=1)
        ]
        return header, rows

    if kind == LIST_ACCOMMODATION:
        header = ["Lp.", "Kod", "Imię i nazwisko", "Miejsce", "Przyjazd", "Wyjazd"]
        rows = [
            [
                str(number),
                row["public_code"],
                _logistics_name(row, DELETED_ACCOUNT_LABEL),
                row["venue"],
                row["arrives_on"].isoformat() if row["arrives_on"] else "",
                row["departs_on"].isoformat() if row["departs_on"] else "",
            ]
            for number, row in enumerate(
                [
                    row
                    for row in arrival_rows(stage, include_deleted=include_deleted)
                    if LogisticsNeed.ACCOMMODATION in row["needs"]
                ],
                start=1,
            )
        ]
        return header, rows

    if kind == LIST_MEAL:
        special = collects_special_needs(competition_of_stage(stage))
        header = ["Lp.", "Kod", "Imię i nazwisko", "Potrzeby"]
        if special:
            header.append("Uwagi")
        meal_needs = {LogisticsNeed.MEAL, LogisticsNeed.DIET}
        rows = []
        for number, row in enumerate(
            [
                row
                for row in arrival_rows(stage, include_deleted=include_deleted)
                if meal_needs & set(row["needs"])
            ],
            start=1,
        ):
            line = [
                str(number),
                row["public_code"],
                _logistics_name(row, DELETED_ACCOUNT_LABEL),
                ", ".join(need_labels(row["needs"])),
            ]
            if special:
                line.append(row.get("note", ""))
            rows.append(line)
        return header, rows

    raise ValueError(f"Nieznany rodzaj listy: {kind}")


def _logistics_name(row: dict, deleted_label: str) -> str:
    """Imię i nazwisko do kolumny listy; konto po anonimizacji – neutralny podpis zamiast pustki."""
    if row.get("deleted"):
        return deleted_label
    return f"{row['first_name']} {row['last_name']}".strip()


def render_logistics_list(stage, kind: str, *, include_deleted: bool = True) -> bytes:
    """Składa listę do druku i zwraca bajty PDF-a – ta sama ścieżka, co protokół etapu.

    Szerokości kolumn liczone są z ich liczby, a nie wypisane per lista: kolumn jest od czterech do
    siedmiu, pierwsza („Lp.”) zawsze wąska, a rozpisywanie trzech tabel szerokości znaczyłoby trzy
    miejsca do poprawienia przy dołożeniu jednej kolumny.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    from apps.results.certificates import FONT_BOLD, FONT_REGULAR, register_fonts

    register_fonts()
    header, rows = logistics_list_rows(stage, kind, include_deleted=include_deleted)
    title = logistics_list_title(stage, kind)

    title_style = ParagraphStyle("title", fontName=FONT_BOLD, fontSize=15, leading=19, spaceAfter=6)
    meta_style = ParagraphStyle("meta", fontName=FONT_REGULAR, fontSize=9.5, leading=13)

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{title} – etap {stage.pk}",
    )

    usable = 174 * mm
    first = 12 * mm
    rest = (usable - first) / max(len(header) - 1, 1)
    table = LongTable([header, *rows], colWidths=[first, *[rest] * (len(header) - 1)], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                ("FONTNAME", (0, 1), (-1, -1), FONT_REGULAR),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                # Wiersze wyższe niż tekst: rubryka podpisu bez miejsca na podpis nie jest rubryką.
                ("TOPPADDING", (0, 1), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
            ]
        )
    )

    story = [
        Paragraph(title, title_style),
        Paragraph(
            f"Edycja: {stage.edition.year_label}<br/>Etap: {stage.display_name}<br/>"
            f"Liczba osób na liście: {len(rows)}<br/>"
            f"Listę sporządzono: {timezone.localtime().strftime('%Y-%m-%d %H:%M')}",
            meta_style,
        ),
        Spacer(1, 8 * mm),
        table,
    ]
    document.build(story)
    return buffer.getvalue()


# --- zrzut edycji (JSON) ------------------------------------------------------------------------


def edition_export(edition) -> dict:
    """Struktura edycji wraz z kodami publicznymi uczestników – do migracji albo archiwum.

    Co jest w środku: edycja, jej etapy z kalendarzem, zadania, wpisy uczestników (kod publiczny,
    województwo, klasa, stan, suma punktów) i – dla etapów z ogłoszonymi wynikami – zamrożony
    snapshot publikacji w postaci, w jakiej został ogłoszony.

    Czego **nie ma i nie będzie**: imion, nazwisk, adresów e-mail, telefonów, plików rozwiązań,
    treści recenzji i uzasadnień decyzji. Zrzut opisuje **zawody**, a nie ludzi; system, który
    przejmuje dane osobowe, dostaje je osobną drogą, po osobnej decyzji administratora danych.

    Wersja formatu jest w polu ``format``: odbiorca zrzutu ma jak sprawdzić, czy umie go przeczytać,
    zanim zacznie zgadywać po kształcie.
    """
    from apps.results.models import ResultsPublication

    stages = list(edition.stages.order_by("opens_at", "id").prefetch_related("problems"))
    publications = {
        publication.stage_id: publication
        for publication in ResultsPublication.objects.filter(stage__edition=edition)
    }
    entries_by_stage: dict[int, list[dict]] = {stage.pk: [] for stage in stages}
    for row in _entry_rows([stage.pk for stage in stages]):
        entries_by_stage[row.pop("stage_id")].append(row)

    return {
        "format": "olimpiada.edition.v1",
        "generated_at": timezone.now().isoformat(),
        "edition": {
            "id": edition.pk,
            "year_label": edition.year_label,
            "is_current": edition.is_current,
            "created_at": edition.created_at.isoformat(),
        },
        "stages": [_stage_export(stage, publications, entries_by_stage) for stage in stages],
    }


def _entry_rows(stage_ids: list[int]) -> list[dict]:
    """Wpisy do etapów w postaci słowników – jedno zapytanie na całą edycję."""
    from apps.competitions.models import StageEntry

    return [
        {
            "stage_id": row["stage_id"],
            "public_code": row["participant__public_code"],
            "voivodeship": row["participant__district"],
            "grade": row["participant__grade"],
            "status": row["status"],
            "total_points": row["total_points"],
        }
        for row in StageEntry.objects.filter(stage_id__in=stage_ids)
        .values(
            "stage_id",
            "status",
            "total_points",
            "participant__public_code",
            "participant__district",
            "participant__grade",
        )
        .order_by("stage_id", "participant__public_code")
    ]


def _stage_export(stage, publications: dict, entries_by_stage: dict) -> dict:
    """Jeden etap zrzutu: kalendarz, zadania, wpisy i – jeśli są – ogłoszone wyniki."""
    publication = publications.get(stage.pk)
    return {
        "id": stage.pk,
        "kind": stage.kind,
        "name": stage.name,
        "display_name": stage.display_name,
        "format": stage.format,
        "opens_at": stage.opens_at.isoformat(),
        "deadline_at": stage.deadline_at.isoformat(),
        "review_deadline_at": stage.review_deadline_at.isoformat(),
        "appeal_window_opens_at": stage.appeal_window_opens_at.isoformat(),
        "appeal_window_closes_at": stage.appeal_window_closes_at.isoformat(),
        "results_published_at": stage.results_published_at.isoformat()
        if stage.results_published_at
        else None,
        "closed_at": stage.closed_at.isoformat() if stage.closed_at else None,
        "problems": [
            {
                "number": problem.number,
                "title": problem.title,
                "allowed_formats": list(problem.allowed_formats or []),
                "max_points": problem.max_points,
            }
            for problem in stage.problems.all()
        ],
        "entries": entries_by_stage.get(stage.pk, []),
        "results": {
            "published_at": publication.published_at.isoformat(),
            "anonymization": publication.anonymization,
            "rows": publication.snapshot or [],
        }
        if publication is not None
        else None,
    }
