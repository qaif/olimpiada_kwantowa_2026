"""Oś grupowania statystyk: **szkoła** dziś, **delegacja** IQO później (STAT-01 § 1).

Po co abstrakcja przy jednej implementacji: Olimpiada Kwantowa liczy uczniów po szkołach
i województwach, a IQO będzie liczyć po delegacjach i krajach. Agregaty, próg k-anonimowości,
pamięć podręczna i ekrany są w obu przypadkach te same – różni je wyłącznie **klucz grupy** i
**region porównawczy**. Gdyby serwisy liczące zakładały szkołę wprost, delegacja znaczyłaby
drugą kopię całego modułu; z osią znaczy jedną nową klasę i jedną gałąź w :func:`axis_for`.

Oś czyta krotkę wpisu (:class:`EntryRow`), a nie model – serwis pobiera wpisy jednym zapytaniem
``values_list`` i oś nie ma prawa dociągać niczego z bazy na wiersz. Wynikiem osi jest
:class:`MemberRow` – ta sama postać, którą ma przynależność **zamrożona** przy publikacji
(``apps.school_stats.models.FrozenMembership``), więc agregaty nie rozróżniają obu źródeł.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from apps.accounts.models import Voivodeship
from apps.accounts.profile import ANONYMISED_SCHOOL
from apps.core.text import fold


class EntryRow(NamedTuple):
    """Jeden wpis do etapu w postaci potrzebnej statystyce – bez imienia, nazwiska i adresu.

    Kolejność pól jest kolejnością kolumn ``values_list`` w ``services._entry_rows``.
    """

    entry_id: int
    stage_id: int
    participant_id: int
    status: str
    school_ref_id: int | None
    school_text: str
    district: str
    school_name: str | None
    school_city: str | None
    school_rspo: int | None
    has_submission: bool
    has_late: bool
    anonymised: bool


class Group(NamedTuple):
    """Grupa wpisu: stabilny klucz, podpis dla człowieka, miejscowość i wiersz wykazu (albo ``None``)."""

    key: str
    label: str
    city: str
    school_id: int | None
    rspo: int | None = None


class MemberRow(NamedTuple):
    """Przynależność wpisu do grupy i regionu plus to, co z niego liczy agregat.

    ``participant_id`` jest ``None`` w wierszu zamrożonym – zamrożenie nie przechowuje identyfikatora
    osoby, bo agregatowi nie jest potrzebny (liczebność osób w grupie liczy się z wpisów żywych).
    ``anonymised`` – konto po anonimizacji w chwili odczytu: taki wpis nie ma grupy ani regionu,
    zostaje wyłącznie w „całości” (STAT-01 § 2).
    """

    entry_id: int
    stage_id: int
    participant_id: int | None
    qualified: bool
    group: Group | None
    region: str
    has_submission: bool
    has_late: bool
    anonymised: bool = False

    @property
    def group_key(self) -> str | None:
        return self.group.key if self.group is not None else None


@dataclass(frozen=True)
class GroupAxis:
    """Interfejs osi. ``kind`` trafia do klucza pamięci podręcznej – dwie osie nie dzielą wpisów."""

    kind: str

    def group_of(self, row: EntryRow) -> Group | None:  # pragma: no cover - interfejs
        raise NotImplementedError

    def region_of(self, row: EntryRow) -> str:  # pragma: no cover - interfejs
        raise NotImplementedError

    def region_label(self, code: str) -> str:  # pragma: no cover - interfejs
        raise NotImplementedError

    def member(self, row: EntryRow, *, qualified_status: str) -> MemberRow:
        """Wpis → przynależność. Konto po anonimizacji nie ma ani grupy, ani regionu (STAT-01 M1)."""
        anonymised = bool(row.anonymised)
        return MemberRow(
            entry_id=row.entry_id,
            stage_id=row.stage_id,
            participant_id=row.participant_id,
            qualified=row.status == qualified_status,
            group=None if anonymised else self.group_of(row),
            region="" if anonymised else self.region_of(row),
            has_submission=bool(row.has_submission),
            has_late=bool(row.has_late),
            anonymised=anonymised,
        )


class SchoolAxis(GroupAxis):
    """Szkoła z wykazu RSPO, a bez dowiązania – nazwa wpisana ręcznie; regionem jest województwo.

    Szkoła wpisana ręcznie grupuje się po **złożonej** nazwie (bez diakrytyków, małymi literami):
    „XIV LO” i „xiv lo” to jedna szkoła – ta sama reguła, co przy progu inicjałów w publikacji
    wyników (``apps.results.services._school_key``), tylko odporna także na brak polskich znaków.
    Pusta nazwa nie tworzy grupy: wpis wchodzi wtedy do województwa i całości, ale do żadnej szkoły.

    Regionem jest ``Participant.district`` – województwo z profilu uczestnika, to samo, które stoi
    w ogłoszonej tabeli przy trybie ``CODE``. Nie województwo szkoły z wykazu: uczeń bez dowiązania
    do wykazu też ma województwo, a dwie definicje regionu dawałyby dwie różne sumy kontrolne.
    """

    def __init__(self):
        super().__init__(kind="school")

    def group_of(self, row: EntryRow) -> Group | None:
        if row.school_ref_id is not None:
            return Group(
                f"s{row.school_ref_id}",
                row.school_name or row.school_text,
                row.school_city or "",
                row.school_ref_id,
                row.school_rspo,
            )
        text = " ".join((row.school_text or "").split())
        # Kreska wpisywana przy anonimizacji konta (``ANONYMISED_SCHOOL``) nie jest szkołą – bez tej
        # reguły wszystkie usunięte konta zlewałyby się w jedną „szkołę” w rankingu.
        if not text or text == ANONYMISED_SCHOOL:
            return None
        return Group(f"t{fold(text)}", text, "", None)

    def region_of(self, row: EntryRow) -> str:
        return row.district or ""

    def region_label(self, code: str) -> str:
        if not code:
            return ""
        try:
            return Voivodeship(code).label
        except ValueError:
            return code


#: Jedyna oś zaimplementowana w STAT-01. Instancja modułowa, bo oś jest bezstanowa.
SCHOOL_AXIS = SchoolAxis()


def axis_for(competition) -> GroupAxis:
    """Oś statystyk konkursu. Dziś zawsze szkoła.

    Punkt zaczepienia dla IQO: konkurs z delegacjami (DEL-01) dostanie tu ``DelegationAxis`` (klucz –
    delegacja uczestnika, region – ``Participant.country``). Do tego czasu również IQO liczy po
    szkołach z profilu – to poprawna, choć uboższa odpowiedź.
    """
    return SCHOOL_AXIS
