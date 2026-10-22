"""Ekrany panelu w wyszukiwarce koordynatora (UX-PANEL-04 D7).

Wyszukiwarka (``apps/web/coordinator_search.py``) znała dotąd wyłącznie obiekty – osoby, zadania, etapy,
zgłoszenia – a audyt pokazał, że drugim najczęstszym pytaniem jest „gdzie jest ekran X” („lejek”,
„zaświadczenia”, „obecność”). Odpowiedzią jest **menu**: te same pozycje, te same warunki widoczności
(flagi, etap w ognisku) i te same adresy, co w kolumnie menu – ``coordinator_nav.groups`` jest jedynym
miejscem, w którym zapisany jest podział panelu. Ekranu, którego nie ma w menu, wyszukiwarka nie wymyśla.

Porównanie bez wielkości liter i polskich znaków („zaswiadczenia” znajduje „Zaświadczenia”), po słowach:
każde słowo frazy musi wystąpić w podpisie pozycji, sekcji albo etapu.
"""

from __future__ import annotations

import unicodedata

from apps.web.coordinator_nav import current_stages, groups, resolve

#: Ile ekranów pokazuje grupa. Menu ma ok. 60 pozycji – fraza z jednego słowa trafia w kilka.
SCREENS_LIMIT = 12


def fold(text: str) -> str:
    """Napis do porównań: małe litery, bez znaków diakrytycznych (``ł`` → ``l``)."""
    text = (text or "").casefold().replace("ł", "l")
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))


def _url(item) -> str | None:
    url = resolve(item.names, item.args)
    if url is None:
        return None
    if item.query:
        url = f"{url}?{item.query}"
    if item.fragment:
        url = f"{url}#{item.fragment}"
    return url


def screen_hits(query: str, competition) -> list[tuple[str, str, str]]:
    """Pozycje menu pasujące do frazy: ``(podpis, sekcja [· etap], adres)``. Jedno zapytanie (etapy)."""
    words = [word for word in fold(query).split() if word]
    if not words:
        return []
    hits: list[tuple[str, str, str]] = []
    for group in groups(current_stages(competition), competition):
        for item in group.items:
            candidates = [(item, "")] + [(child, item.label) for child in item.children]
            for entry, parent in candidates:
                haystack = fold(f"{entry.label} {group.label} {parent}")
                if not all(word in haystack for word in words):
                    continue
                url = _url(entry)
                if url is None:
                    continue
                meta = f"{group.label} · {parent}" if parent else group.label
                label = f"{parent} › {entry.label}" if parent else entry.label
                hits.append((label, meta, url))
                if len(hits) >= SCREENS_LIMIT:
                    return hits
    return hits
