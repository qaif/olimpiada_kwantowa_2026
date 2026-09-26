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

import re
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


#: Endpointy, których listy **nie** wolno pokazać z kopii „stale” (``client.SHORT_TTL_ENDPOINT_RE``):
#: zadania i tabele wyników. Otwarcie etapu albo publikację wyników da się w aplikacji głównej
#: wycofać – kopia sprzed wycofania pokazywałaby przez ``DJCMS_API_STALE_SECONDS`` treść, której
#: już nie ma. Przy braku świeżych danych strona mówi „chwilowo niedostępne”.
FRESH_ONLY_ENDPOINT_RE = client.SHORT_TTL_ENDPOINT_RE


@dataclass(frozen=True)
class LiveData:
    """Jedna odpowiedź API przygotowana dla szablonu."""

    result: client.ApiResult
    data: dict = field(default_factory=dict)
    #: Dane z kopii „stale” się nie liczą (``FRESH_ONLY_ENDPOINT_RE``) – ``available`` jest wtedy fałszem.
    fresh_only: bool = False

    @property
    def available(self) -> bool:
        return self.result.ok and not (self.fresh_only and self.result.stale)

    @property
    def stale(self) -> bool:
        return self.result.stale

    @property
    def stale_label(self) -> str:
        return stale_label(self.result)


def fetch(endpoint: str, request) -> LiveData:
    """Dane endpointu dla tej odsłony (pamięć żądania → bufor → API → kopia → brak).

    Zadania i wyniki (``FRESH_ONLY_ENDPOINT_RE``) z kopii „stale” = brak danych: pusty słownik
    i ``available`` fałszywe – szablon pokazuje komunikat, a nie listę sprzed wycofania.
    """
    result = client.get(endpoint, request=request)
    fresh_only = bool(FRESH_ONLY_ENDPOINT_RE.match(endpoint))
    usable = isinstance(result.data, dict) and not (fresh_only and result.stale)
    data = clean_urls(result.data) if usable else {}
    return LiveData(result=result, data=data, fresh_only=fresh_only)


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


# --- tekst formatowany z API i strony-dane (DJ-02 D9) ---------------------------------------------

#: ``href`` ze ścieżką od korzenia witryny (``/zadania/``), a nie adresem bezwzględnym ani ``//host``.
ROOT_HREF_RE = re.compile(r"""(\bhref\s*=\s*)(["'])/(?!/)""", re.IGNORECASE)


def prefix_links(html: str, prefix: str) -> str:
    """Odnośniki do stron od korzenia witryny (``href="/zadania/"``) pod prefiksem konkursu.

    Tekst z Wagtaila (API i paczka) niesie ścieżki stron **względem korzenia witryny konkursu**
    (``export_richtext``); konkurs pod prefiksem ścieżki (``/druga/``) ma je pod prefiksem – tak
    jak rysuje je Wagtail (``apps.tenancy.page_urls``). Adresy aplikacji przychodzą już bezwzględne
    (``_front_href``), kotwice i adresy z hostem zostają bez zmian. ``prefix`` = ``"/"`` – nic.
    """
    prefix = "/" + (prefix or "").strip("/")
    if prefix == "/" or not html:
        return html
    return ROOT_HREF_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{prefix}/", html)


def rich_text(value: Any) -> str:
    """Tekst formatowany z API → HTML **po sanityzatorze** djangocms-text, gotowy do szablonu.

    API oddaje pola ``RichTextField`` Wagtaila (``workshops.page.intro``, ``partners.page.intro``,
    ``become_partner_body``) **bez** sanityzacji – robi ją odbiorca, tak jak przy imporcie paczki
    (reguła 4 z § 7 DJ-01). Tu ta sama funkcja, co importer i pola ``HTMLField``:
    ``djangocms_text.html.clean_html`` (``nh3``; przez ``sanitize_and_mark_safe``, żeby wynik –
    i wyłącznie on – był oznaczony jako bezpieczny). ``<script>``, atrybuty ``on*`` i adresy
    ``javascript:`` znikają. Prefiks konkursu dokładamy **przed** sanityzacją (``prefix_links``).
    """
    from django.urls import get_script_prefix
    from djangocms_text.fields import sanitize_and_mark_safe

    if not isinstance(value, str) or not value.strip():
        return ""
    return sanitize_and_mark_safe(prefix_links(value, get_script_prefix()))


def _email(value: Any) -> str:
    """Adres do ``mailto:`` – wyłącznie poprawny e-mail (API go nie sprawdza, pole Wagtaila tak)."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    email = value.strip() if isinstance(value, str) else ""
    try:
        validate_email(email)
    except ValidationError:
        return ""
    return email


def _image(value: Any) -> dict | None:
    """``{"src", "width", "height"}`` obrazu z API – ``src`` przez ``safe_href``, wymiary liczbami."""
    if not isinstance(value, dict):
        return None
    src = safe_href(value.get("src"))
    if not src:
        return None
    width, height = value.get("width"), value.get("height")
    return {
        "src": src,
        "width": width if isinstance(width, int) and not isinstance(width, bool) else "",
        "height": height if isinstance(height, int) and not isinstance(height, bool) else "",
    }


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def workshop_schedules(live: LiveData) -> list[dict]:
    """Tabele harmonogramu (``schedules``) w kształcie ``cms/blocks/schedule.html``: napisy i flagi."""
    tables = []
    for raw in dicts(live.data.get("schedules")):
        rows = [
            {key: _text(row.get(key)) for key in ("topic", "date", "time", "lecturer")}
            for row in dicts(raw.get("rows"))
        ]
        tables.append(
            {
                **{
                    key: _text(raw.get(key)) for key in ("caption", "topic_label", "date_label", "time_label")
                },
                "lecturer_label": _text(raw.get("lecturer_label")),
                # Ta sama reguła co ``ScheduleValue.has_time``/``has_lecturer`` – liczona tu z wierszy,
                # a nie wzięta na wiarę z API (kolumna bez żadnej wartości znika w obu wersjach).
                "has_time": any(row["time"].strip() for row in rows),
                "has_lecturer": any(row["lecturer"].strip() for row in rows),
                "rows": rows,
            }
        )
    return tables


def workshops_context(live: LiveData, part: str) -> dict:
    """Kontekst wtyczki ``WorkshopSchedulePlugin``: wprowadzenie albo tabele strony „Warsztaty”.

    ``page`` = ``None`` (konkurs bez opublikowanej, publicznej strony „Warsztaty” w Wagtailu – np.
    nowy konkurs z szablonu, który jej nie zakłada) to stan pusty, a nie błąd: wprowadzenie nie
    rysuje niczego, a harmonogram – komunikat „zostanie opublikowany”.
    """
    page = mapping(live.data.get("page")) if live.available else {}
    return {
        "live": live,
        "part": part,
        "has_page": bool(page),
        "intro": rich_text(page.get("intro")),
        "schedules": workshop_schedules(live) if page else [],
    }


def partner_groups(live: LiveData) -> list[dict]:
    """Partnerzy pogrupowani po poziomie w kolejności ``levels`` (``PartnersPage.groups``).

    Kolejność w grupie = kolejność na stronie Wagtaila (redakcyjna). Partner z poziomem spoza
    słownika odpowiedzi nie trafia do żadnej grupy – tak samo jak w ``PartnersPage.groups``.
    """
    levels = [
        (item[0], item[1])
        for item in live.data.get("levels") or []
        if isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item)
    ]
    partners = [
        {
            "name": _text(item.get("name")),
            "level": _text(item.get("level")),
            "url": safe_href(item.get("url")),
            "logo": _image(item.get("logo")),
            "description": _text(item.get("description")),
            "initials": _text(item.get("initials")),
            "is_wide": item.get("is_wide") is True,
        }
        for item in dicts(live.data.get("partners"))
    ]
    groups = []
    for key, label in levels:
        members = [partner for partner in partners if partner["level"] == key]
        if members:
            groups.append({"level": key, "label": label, "partners": members})
    return groups


def partners_context(live: LiveData) -> dict:
    """Kontekst wtyczki ``PartnersLivePlugin`` – cała treść strony partnerów z ``GET partners``.

    Tekst formatowany (``intro``, ``become_partner_body``) przez sanityzator (``rich_text``);
    ``contact_email`` tylko jako poprawny adres; zaproszenie „Zostań partnerem” – jak w Wagtailu –
    dopiero przy nagłówku **i** treści.
    """
    page = mapping(live.data.get("page")) if live.available else {}
    title = _text(page.get("become_partner_title")).strip()
    body = rich_text(page.get("become_partner_body"))
    return {
        "live": live,
        "has_page": bool(page),
        "intro": rich_text(page.get("intro")),
        "groups": partner_groups(live) if page else [],
        "become_partner": {"title": title, "body": body, "contact_email": _email(page.get("contact_email"))}
        if title and body
        else None,
    }


def partners_strip_entries(live: LiveData) -> list[dict]:
    """Pas partnerów strony głównej z ``GET partners`` (strona partnerów na żywo): kolejność strony."""
    if not live.available or not mapping(live.data.get("page")):
        return []
    return [
        {
            "name": _text(item.get("name")),
            "url": safe_href(item.get("url")),
            "logo": _image(item.get("logo")),
            "is_wide": item.get("is_wide") is True,
        }
        for item in dicts(live.data.get("partners"))
    ]
