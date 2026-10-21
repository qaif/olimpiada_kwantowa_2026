"""Rama panelu koordynatora: menu policzone raz na żądanie, okruszki, przypięte pozycje (UX-PANEL-04).

Źródłem prawdy o menu zostaje ``apps.web.coordinator_nav`` – ten moduł niczego w nim nie zmienia (UX-PANEL-04
D2: sekcje i pozycje „bajt w bajt”). Dokłada **obok** to, czego potrzebują nowe elementy ramy:

- **jedno** wyliczenie menu na żądanie (``navigation``). Menu czytają teraz cztery miejsca naraz – kolumna
  menu, okruszki, przycisk „Przypnij” i kafel „Przypięte” na Starcie; bez pamięci na żądaniu każde z nich
  płaciłoby osobno za etapy i liczniki,
- ikony nagłówków sekcji, rozwinięcie dzieci **jednego** etapu (D3), sumę liczników na zwiniętym menu,
- okruszki z pozycji bieżącej (D8) i płaską listę pozycji – do palety poleceń i do walidacji przypinania,
- przypięte pozycje (D9): podpis bierze się z menu w chwili wyświetlenia, więc pozycja za wyłączoną flagą
  albo po skasowanym etapie znika, zamiast prowadzić do 404.

Menu **nie jest zabezpieczeniem** – dostęp rozstrzyga ``CoordinatorRequiredMixin`` i serwisy.
"""

from __future__ import annotations

from django.core.cache import cache
from django.db import DatabaseError
from django.urls import get_script_prefix

from apps.web import coordinator_nav

_MEMO = "_coordinator_panel_nav"
_PINS_MEMO = "_coordinator_panel_pins"

#: Ikona nagłówka sekcji – po kluczu sekcji (``slugify`` etykiety w ``coordinator_nav.navigation``). Sekcja
#: spoza słownika (dopisana później) dostaje ikonę ogólną, a nie pusty kwadrat.
GROUP_ICONS = {
    "pulpit": "home",
    "etapy": "flag",
    "ocenianie": "scale",
    "uczestnicy-i-konta": "users",
    "komitet": "committee",
    "komunikacja": "mail",
    "raporty": "chart",
    "ustawienia": "settings",
    "konkursy-platformy": "globe",
}
DEFAULT_GROUP_ICON = "dot"

#: Sekcja, w której pozycje mają dzieci (etapy). Dzieci rozwija się tylko przy jednym etapie (D3).
STAGE_GROUP = "etapy"
#: Sekcja pulpitu – jej jedyna pozycja to Start, czyli korzeń okruszków.
START_GROUP = "pulpit"

#: Najwięcej przypiętych pozycji na konto i konkurs. Osiem mieści się nad sekcjami menu bez przewijania
#: i w jednym kaflu Startu; więcej przestaje być skrótem, a staje się drugim menu.
MAX_PINS = 8
PINS_CACHE_SECONDS = 300


def pins_cache_key(user_id, competition_id) -> str:
    return f"coordinator-panel:pins:{competition_id or 'none'}:{user_id}"


def relative_path(url: str) -> str:
    """Adres z menu bez prefiksu konkursu i bez wiodącego ukośnika – postać zapisywana w ``PinnedItem``."""
    prefix = get_script_prefix()
    if url.startswith(prefix):
        url = url[len(prefix) :]
    return url.lstrip("/")


def navigation(request) -> dict:
    """Kontekst menu (``coordinator_nav.navigation``) plus dodatki ramy – raz na żądanie."""
    data = getattr(request, _MEMO, None)
    if data is not None:
        return data
    data = coordinator_nav.navigation(request)
    _decorate(data, request)
    setattr(request, _MEMO, data)
    return data


def _decorate(data: dict, request) -> None:
    stages = data.get("nav_stages") or []
    focus = coordinator_nav.focus_stage(stages)
    focus_url = coordinator_nav.resolve(("web:coordinator-stage-edit",), (focus.pk,)) if focus else None
    groups = data.get("nav_groups") or []
    for group in groups:
        group["icon"] = GROUP_ICONS.get(group["slug"], DEFAULT_GROUP_ICON)
        for item in group["items"]:
            item["expanded"] = bool(item["children"]) and (item["active"] or item["open"])
        if group["slug"] == STAGE_GROUP:
            with_children = [item for item in group["items"] if item["children"]]
            if with_children and not any(item["expanded"] for item in with_children):
                for item in with_children:
                    if item["url"] == focus_url:
                        item["expanded"] = True
                        break
    data["nav_flat"] = _flat(groups)
    data["nav_trail"] = _trail(groups)
    data["nav_current"] = data["nav_trail"][-1] if data["nav_trail"] else None
    data["nav_badge_total"] = sum(group.get("badge") or 0 for group in groups) or None
    data["nav_pins"] = pinned_items(request, data["nav_flat"])
    data["nav_pin_limit"] = MAX_PINS
    user = getattr(request, "user", None)
    competition = getattr(request, "competition", None)
    # Klucz „ostatnio używanych” w przeglądarce (D10): konto i konkurs, żeby wspólny komputer w sekretariacie
    # nie mieszał historii dwóch koordynatorów, a ta sama osoba w dwóch konkursach – dwóch menu.
    data["nav_recent_key"] = f"kp-recent:{getattr(competition, 'pk', 'none')}:{getattr(user, 'pk', 'anon')}"


def _flat(groups: list[dict]) -> list[dict]:
    """Pozycje menu jedna pod drugą: podpis, adres, sekcja i – dla dzieci etapu – etap."""
    rows = []
    for group in groups:
        for item in group["items"]:
            rows.append({"label": item["label"], "url": item["url"], "group": group["label"], "parent": ""})
            for child in item["children"]:
                rows.append(
                    {
                        "label": child["label"],
                        "url": child["url"],
                        "group": group["label"],
                        "parent": item["label"],
                    }
                )
    return rows


def _trail(groups: list[dict]) -> list[dict]:
    """Okruszki: sekcja › pozycja (› dziecko etapu) dla pozycji bieżącej; pusta lista na Starcie.

    Pierwsze trafienie wygrywa. Dziecko etapu ma pierwszeństwo przed samym etapem: na ekranie „Zadania”
    świeci się dziecko, a okruszek etapu prowadzi do jego terminów (ten sam adres, co pozycja etapu).
    """
    for group in groups:
        for item in group["items"]:
            child = next((child for child in item["children"] if child["active"]), None)
            if child is not None:
                return [
                    {"label": group["label"], "url": ""},
                    {"label": item["label"], "url": item["url"]},
                    {"label": child["label"], "url": child["url"]},
                ]
            if item["active"]:
                if group["slug"] == START_GROUP:
                    return []
                return [{"label": group["label"], "url": ""}, {"label": item["label"], "url": item["url"]}]
    return []


# --- przypięte pozycje (D9) ----------------------------------------------------------------------------


def _pinned_paths(user, competition) -> list[str]:
    """Ścieżki przypięte przez konto w konkursie – z pamięci podręcznej (5 min, unieważniana zapisem)."""
    if competition is None or not getattr(user, "is_authenticated", False):
        return []
    key = pins_cache_key(user.pk, competition.pk)
    paths = cache.get(key)
    if paths is None:
        from .models import PinnedItem

        try:
            paths = list(
                PinnedItem.objects.for_competition(competition)
                .filter(user=user)
                .order_by("position", "pk")
                .values_list("path", flat=True)
            )
        except DatabaseError:  # pragma: no cover - baza bez migracji tej aplikacji
            return []
        cache.set(key, paths, PINS_CACHE_SECONDS)
    return paths


def pinned_items(request, flat: list[dict]) -> list[dict]:
    """Przypięte pozycje z podpisem z menu. Ścieżka, której w menu nie ma, jest pomijana (nie kasowana)."""
    memo = getattr(request, _PINS_MEMO, None)
    if memo is not None:
        return memo
    by_path = {}
    for row in flat:
        by_path.setdefault(relative_path(row["url"]), row)
    items = []
    for path in _pinned_paths(getattr(request, "user", None), getattr(request, "competition", None)):
        row = by_path.get(path)
        if row is None:
            continue
        label = f"{row['parent']} › {row['label']}" if row["parent"] else row["label"]
        items.append({"label": label, "url": row["url"], "path": path, "group": row["group"]})
    setattr(request, _PINS_MEMO, items)
    return items


def forget_pins(request) -> None:
    """Zapomnij przypięte pozycje z pamięci żądania i pamięci podręcznej – po zapisie."""
    user = getattr(request, "user", None)
    competition = getattr(request, "competition", None)
    if user is not None and competition is not None:
        cache.delete(pins_cache_key(user.pk, competition.pk))
    for attr in (_PINS_MEMO, _MEMO):
        if hasattr(request, attr):
            delattr(request, attr)


def current_pin(request) -> dict | None:
    """Stan przycisku „Przypnij” dla bieżącego ekranu: ścieżka pozycji bieżącej i czy już jest przypięta.

    ``None`` na Starcie i na ekranach bez pozycji bieżącej – przypiąć wolno wyłącznie pozycję menu (D9).
    """
    data = navigation(request)
    current = data.get("nav_current")
    if not current or not current.get("url"):
        return None
    path = relative_path(current["url"])
    pinned = any(item["path"] == path for item in data["nav_pins"])
    return {
        "path": path,
        "pinned": pinned,
        "label": current["label"],
        "full": not pinned and len(data["nav_pins"]) >= MAX_PINS,
    }
