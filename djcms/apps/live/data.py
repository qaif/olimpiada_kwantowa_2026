"""Dane zawodów dla wtyczek żywych i szablonów stron (DJ-01f).

Warstwa między klientem API (``apps.live.client``) a szablonami. Robi trzy rzeczy, których
szablon nie powinien robić sam:

- **czyści adresy.** Każde pole, które trafia do ``href`` (``results_url``, ``statement_url``,
  ``page_path``, ``login_url``, ``materials_url``, ``url``), przechodzi przez
  ``apps.live.chrome.safe_href`` – druga warstwa po ``api_href`` aplikacji głównej; autoescape
  nie chroni przed ``javascript:`` w atrybucie,
- **mówi, czy dane są.** ``LiveData.available`` jest fałszem, gdy API nie oddało nic ani
  z bufora, ani z kopii – szablon pokazuje wtedy ``{% dj_unavailable %}``; ``stale_label`` to
  dopisek „stan na HH:MM” przy danych z kopii awaryjnej,
- **pilnuje jawności zadań** (``problems_context``) – patrz docstring tej funkcji.

Brak API to zawsze „brak danych”, a nigdy wyjątek: klient nie podnosi błędów, a ta warstwa
na każdy nieoczekiwany kształt odpowiedzi (``None`` zamiast listy, lista zamiast słownika)
odpowiada pustą wartością.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from . import client
from .chrome import safe_href, stale_label

#: Klucze odpowiedzi ``stages``/``problems``/``results``/``editions/<id>/results``/``workshops``,
#: których wartości trafiają do ``href``.
URL_KEYS = frozenset({"url", "results_url", "statement_url", "page_path", "login_url", "materials_url"})

#: Komunikat przed otwarciem etapu, gdy redaktor nie wpisał własnego – ten sam napis, co
#: w ``ProblemsPage.get_context`` aplikacji głównej.
DEFAULT_CLOSED_NOTICE = (
    "Zadań jeszcze nie ogłoszono. Treści zadań tego etapu zostaną opublikowane w chwili jego otwarcia."
)

#: Nazwa relacji odwrotnej ``PageContent`` → ``ArchiveMeta`` (``PageContentExtension`` z DJ-01e;
#: django nadaje ją z nazwy modelu). Jedyne miejsce, które zna tę nazwę.
ARCHIVE_META_ACCESSOR = "archivemeta"


def clean_urls(value: Any, key: str | None = None) -> Any:
    """Kopia struktury z każdym adresem (klucze ``URL_KEYS``) przepuszczonym przez ``safe_href``."""
    if isinstance(value, dict):
        return {
            k: safe_href(v) if k in URL_KEYS and isinstance(v, str) else clean_urls(v, k)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [clean_urls(item) for item in value]
    return value


@dataclass(frozen=True)
class LiveData:
    """Jedna odpowiedź API przygotowana dla szablonu."""

    result: client.ApiResult
    data: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.result.ok

    @property
    def stale(self) -> bool:
        return self.result.stale

    @property
    def stale_label(self) -> str:
        return stale_label(self.result)


def fetch(endpoint: str, request) -> LiveData:
    """Dane endpointu dla tej odsłony (pamięć żądania → bufor → API → kopia → brak)."""
    result = client.get(endpoint, request=request)
    data = clean_urls(result.data) if isinstance(result.data, dict) else {}
    return LiveData(result=result, data=data)


def dicts(value) -> list[dict]:
    """Lista słowników z pola odpowiedzi – wszystko inne (``None``, napis, liczba) odpada."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def mapping(value) -> dict:
    """Słownik z pola odpowiedzi – wszystko inne to pusty słownik."""
    return value if isinstance(value, dict) else {}


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if timezone.is_aware(parsed) else None


def opened_by_clock(stage: dict | None, now: datetime | None = None) -> bool:
    """Czy ``opens_at`` etapu już minął według zegara ``dj.``. Brak/zła data = **nie** (zamknięte)."""
    if not isinstance(stage, dict):
        return False
    opens_at = _parse(stage.get("opens_at"))
    return opens_at is not None and opens_at <= (now or timezone.now())


def problems_context(live: LiveData, notice: str = "", now: datetime | None = None) -> dict:
    """Kontekst wtyczki zadań – reguła 2 z § 7 („treść zadań jawna dopiero po ``opens_at``”).

    Źródłem decyzji jest aplikacja główna: ``problems_state`` oddaje pustą listę, dopóki etap się
    nie otworzy, więc przed ``opens_at`` w odpowiedzi nie ma ani tytułu, ani adresu PDF. Tu tę
    decyzję tylko **zawężamy**, nigdy nie poszerzamy:

    - tabela zadań etapu stoi wyłącznie przy ``stage_has_opened is True`` **i** ``opens_at`` już
      minionym według zegara ``dj.``. Gdyby odpowiedź kiedykolwiek przyniosła zadania przy
      ``stage_has_opened: false`` (błąd po stronie API, niezgodna wersja), strona ich nie pokaże,
    - arkusz treningowy – ta sama reguła zegara (``training_problems`` API liczy tak samo:
      pusto, dopóki trening się nie otworzy).

    Kopia awaryjna („stale”) nie może pokazać zadań za wcześnie: zapisana przed otwarciem ma
    pustą listę, a zapisana po otwarciu – zadania, które w tej chwili już są jawne.
    """
    data = live.data
    stage = data.get("stage") if isinstance(data.get("stage"), dict) else None
    training = data.get("training_stage") if isinstance(data.get("training_stage"), dict) else None
    now = now or timezone.now()
    has_opened = stage is not None and data.get("stage_has_opened") is True and opened_by_clock(stage, now)
    training_open = training is not None and opened_by_clock(training, now)
    return {
        "live": live,
        "stage": stage,
        "stage_has_opened": has_opened,
        "problems": dicts(data.get("problems")) if has_opened else [],
        "notice": notice or DEFAULT_CLOSED_NOTICE,
        "training_stage": training,
        "training_problems": dicts(data.get("training_problems")) if training_open else [],
    }


def results_context(live: LiveData) -> dict:
    """Tabele bieżącej edycji i odnośniki archiwalne; wiersze tabel w postaci gotowej dla szablonu.

    Szablon Wagtaila składa komórkę punktów filtrem ``dict_get`` + ``points``; tu ta sama rzecz
    jest policzona z góry (``points_display`` z API), żeby szablon ``dj.`` nie formatował danych
    zawodów sam (§ 1.2 p. 3). Kolumna „Województwo” jest zawsze (jak w Wagtailu), a pusta, gdy
    snapshot nie niesie okręgu.
    """
    tables = []
    for table in dicts(live.data.get("tables")):
        raw_numbers = table.get("problem_numbers")
        numbers = [str(number) for number in raw_numbers] if isinstance(raw_numbers, list) else []
        maxima = mapping(table.get("problem_maxima"))
        rows = []
        for row in dicts(table.get("rows")):
            shown = mapping(row.get("points_display"))
            rows.append({**row, "cells": [shown.get(number, "") for number in numbers]})
        tables.append(
            {
                **table,
                "columns": [{"number": number, "maximum": maxima.get(number, "")} for number in numbers],
                "rows": rows,
                "colspan": len(numbers) + 5,
            }
        )
    return {"live": live, "tables": tables, "archive": dicts(live.data.get("archive"))}


# --- archiwum edycji ------------------------------------------------------------------------------


def archive_edition_id(content) -> int | None:
    """Identyfikator edycji z rozszerzenia ``ArchiveMeta`` treści strony (``None`` = brak powiązania)."""
    if content is None:
        return None
    try:
        meta = getattr(content, ARCHIVE_META_ACCESSOR)
    except AttributeError, ObjectDoesNotExist:
        return None
    value = getattr(meta, "edition_id", None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def current_content(context) -> Any:
    """Renderowana właśnie ``PageContent`` – z paska narzędzi (wersja publiczna, robocza, podgląd).

    Pasek ustawia obiekt w każdym widoku, który rysuje stronę (``cms.views.details`` i widoki
    edycji/podglądu); ``current_pagecontent`` z kontekstu to zapas na render bez paska.
    """
    request = context.get("request")
    toolbar = getattr(request, "toolbar", None)
    obj = toolbar.get_object() if toolbar is not None else None
    return obj if obj is not None else context.get("current_pagecontent")


@dataclass(frozen=True)
class ArchiveResults:
    """Odnośniki do tabel wyników jednej edycji archiwalnej (``GET editions/<id>/results``)."""

    edition_id: int | None
    live: LiveData | None

    @property
    def linked(self) -> bool:
        """Strona ma wskazaną edycję – bez niej nie ma o co pytać API."""
        return self.edition_id is not None

    @property
    def available(self) -> bool:
        return self.live is not None and self.live.available

    @property
    def edition(self) -> dict | None:
        value = self.live.data.get("edition") if self.live is not None else None
        return value if isinstance(value, dict) else None

    @property
    def links(self) -> list[dict]:
        if self.live is None:
            return []
        return [
            link
            for link in dicts(self.live.data.get("links"))
            if link.get("results_url") and isinstance(link.get("stage"), dict)
        ]


def archive_results(content, request) -> ArchiveResults:
    edition_id = archive_edition_id(content)
    if edition_id is None:
        return ArchiveResults(edition_id=None, live=None)
    return ArchiveResults(edition_id=edition_id, live=fetch(f"editions/{edition_id}/results", request))
