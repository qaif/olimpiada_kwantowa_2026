"""Przeniesienie zatwierdzonych nakładek do katalogów ``.po`` (L10N-01 § 7).

Nakładka w bazie jest rozwiązaniem **na teraz**: działa od razu, ale żyje poza repozytorium, więc
nie przechodzi recenzji kodu i nie trafia na świeżą instalację. Docelowym miejscem poprawki jest
``msgstr`` w katalogu – stąd trzy kroki, które robi człowiek, a nie automat z prawem zapisu do gita:

1. produkcja: ``export_translations --to-json plik.json`` (sam tekst tłumaczeń, bez danych osób),
2. checkout dewelopera: ``export_translations --from-json plik.json`` → diff w ``.po`` → PR,
3. po wdrożeniu PR-a: ``export_translations --prune`` – nakładki, których tekst jest już
   w **skompilowanym** katalogu (``.mo`` – to on trafia do gettext), znikają z bazy; pozostałe zostają.

Przed zapisem do pliku tekst przechodzi tę samą walidację, co w panelu – plik JSON przyjechał
spoza repozytorium i niczego nie zakładamy o jego drodze. Potwierdzenie („obecne tłumaczenie jest
dobre”) zapisuje wyłącznie znacznik ``# l10n-reviewed`` – ``msgstr`` zostaje bajt w bajt. Poprawka
podjęta wobec innego ``msgstr`` niż ten, który dziś stoi w katalogu (wydanie zmieniło go w międzyczasie),
nie nadpisuje nowszego tekstu – trafia do raportu jako konflikt.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.models import audit

from . import catalogs, runtime
from .models import OverrideKind, TranslationOverride, string_key
from .validation import clean_translation

#: 2 – wpisy niosą ``kind`` i ``base_text``. Plik w wersji 1 (bez nich) wczytujemy nadal: brak
#: rodzaju to poprawka, brak tekstu bazowego – brak kontroli konfliktu.
FORMAT_VERSION = 2
READABLE_VERSIONS = (1, 2)


@dataclass
class Report:
    entries: int = 0
    files: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def collect(languages: list[str]) -> list[dict]:
    """Nakładki z bazy jako zwykłe słowniki – ten sam kształt, co w pliku JSON."""
    rows = TranslationOverride.objects.filter(language__in=languages).order_by(
        "language", "msgid", "plural_index"
    )
    return [
        {
            "language": row.language,
            "msgctxt": row.msgctxt,
            "msgid": row.msgid,
            "plural_index": row.plural_index,
            "kind": row.kind,
            "text": row.text,
            "base_text": row.base_text,
        }
        for row in rows
    ]


def dump(items: list[dict]) -> str:
    return json.dumps({"format": FORMAT_VERSION, "overrides": items}, ensure_ascii=False, indent=2) + "\n"


def load(text: str) -> list[dict]:
    payload = json.loads(text)
    if not isinstance(payload, dict) or payload.get("format") not in READABLE_VERSIONS:
        raise ValueError("Nieznany format pliku z tłumaczeniami.")
    items = payload.get("overrides")
    if not isinstance(items, list):
        raise ValueError("Plik nie zawiera listy „overrides”.")
    for item in items:
        if not isinstance(item, dict) or not {"language", "msgid", "text"} <= set(item):
            raise ValueError("Wpis bez języka, napisu albo tłumaczenia.")
        if item.get("kind", OverrideKind.CHANGE) not in OverrideKind.values:
            raise ValueError("Nieznany rodzaj wpisu.")
    return items


def write(items: list[dict], *, dry_run: bool = False) -> dict[str, Report]:
    """Zapisuje tłumaczenia (i znaczniki potwierdzeń) do każdego katalogu języka z danym napisem."""
    reports: dict[str, Report] = {}
    by_language: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_language[item["language"]].append(item)
    for language, group in by_language.items():
        report = reports.setdefault(language, Report())
        if language not in catalogs.review_languages():
            report.invalid.extend(item["msgid"][:60] for item in group)
            continue
        index = catalogs.index(language)
        updates: dict[tuple[str | None, str], dict[int, str | None]] = defaultdict(dict)
        for item in group:
            form = item.get("plural_index")
            row = index.by_key.get(string_key(item.get("msgctxt") or None, item["msgid"], form))
            if row is None:
                report.stale.append(item["msgid"][:60])
                continue
            if item.get("kind", OverrideKind.CHANGE) == OverrideKind.CONFIRMATION:
                # Sam znacznik – potwierdzono tekst katalogu, więc nie ma czego przepisywać. Gdy
                # katalog od potwierdzenia się zmienił, potwierdzenie dotyczyło innego tekstu.
                if row.translation != item["text"]:
                    report.conflicts.append(item["msgid"][:60])
                    continue
                updates[(row.msgctxt or None, row.msgid)][form or 0] = None
                continue
            base = item.get("base_text")
            if base is not None and base != row.translation and row.translation != item["text"]:
                report.conflicts.append(item["msgid"][:60])
                continue
            try:
                text = clean_translation(
                    item["text"],
                    msgid=row.msgid,
                    msgid_plural=row.msgid_plural,
                    plural_index=row.plural_index,
                )
            except ValidationError:
                report.invalid.append(item["msgid"][:60])
                continue
            updates[(row.msgctxt or None, row.msgid)][form or 0] = text
        if not updates:
            continue
        for path in catalogs.catalog_paths(language):
            original = path.read_text(encoding="utf-8")
            content, changed = catalogs.rewrite(original, updates)
            if not changed or content == original:
                continue
            report.entries += changed
            report.files.append(catalogs.relative(path))
            if not dry_run:
                Path(path).write_text(content, encoding="utf-8", newline="")
    catalogs.forget()
    return reports


@dataclass
class PruneReport:
    removed: int = 0
    kept: int = 0
    stale: list[str] = field(default_factory=list)


@transaction.atomic
def prune(languages: list[str], *, delete_stale: bool = False) -> PruneReport:
    """Usuwa nakładki, które są już w **wdrożonym** katalogu – nigdy wcześniej.

    - poprawka: wyłącznie gdy skompilowany ``.mo`` oddaje dokładnie jej tekst (``.po`` bez ``.mo``
      albo ``.mo`` sprzed kompilacji to jeszcze nie wdrożenie – nakładka zostaje),
    - potwierdzenie: gdy wpis w katalogu ma już znacznik ``# l10n-reviewed``,
    - napis, którego nie ma już w żadnym katalogu: tylko raport; usunięcie wyłącznie
      z ``delete_stale`` (``--prune-stale``) – brak napisu bywa chwilowy (gałąź bez tej zmiany).
    """
    report = PruneReport()
    for language in languages:
        index = catalogs.index(language)
        for override in TranslationOverride.objects.filter(language=language):
            row = index.by_key.get(override.key)
            if row is None:
                report.stale.append(f"{language}: {override.msgid[:60]}")
                if not delete_stale:
                    report.kept += 1
                    continue
            elif override.kind == OverrideKind.CONFIRMATION:
                if not row.reviewed:
                    report.kept += 1
                    continue
            else:
                compiled = catalogs.compiled_text(language, row.msgctxt, row.msgid, row.plural_index)
                if compiled != override.text:
                    report.kept += 1
                    continue
            audit(None, "translation.pruned", override, {"language": language, "key": override.key})
            override.delete()
            report.removed += 1
        transaction.on_commit(lambda language=language: runtime.publish(language))
    return report
