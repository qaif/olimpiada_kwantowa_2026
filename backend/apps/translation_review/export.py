"""Przeniesienie zatwierdzonych nakładek do katalogów ``.po`` (L10N-01 § 7).

Nakładka w bazie jest rozwiązaniem **na teraz**: działa od razu, ale żyje poza repozytorium, więc
nie przechodzi recenzji kodu i nie trafia na świeżą instalację. Docelowym miejscem poprawki jest
``msgstr`` w katalogu – stąd trzy kroki, które robi człowiek, a nie automat z prawem zapisu do gita:

1. produkcja: ``export_translations --to-json plik.json`` (sam tekst tłumaczeń, bez danych osób),
2. checkout dewelopera: ``export_translations --from-json plik.json`` → diff w ``.po`` → PR,
3. po wdrożeniu PR-a: ``export_translations --prune`` – nakładki z tekstem identycznym z katalogiem
   znikają z bazy (nie są już potrzebne), pozostałe zostają.

Przed zapisem do pliku tekst przechodzi tę samą walidację, co w panelu – plik JSON przyjechał
spoza repozytorium i niczego nie zakładamy o jego drodze.
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
from .models import TranslationOverride, string_key
from .validation import clean_translation

FORMAT_VERSION = 1


@dataclass
class Report:
    entries: int = 0
    files: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)


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
            "text": row.text,
        }
        for row in rows
    ]


def dump(items: list[dict]) -> str:
    return json.dumps({"format": FORMAT_VERSION, "overrides": items}, ensure_ascii=False, indent=2) + "\n"


def load(text: str) -> list[dict]:
    payload = json.loads(text)
    if not isinstance(payload, dict) or payload.get("format") != FORMAT_VERSION:
        raise ValueError("Nieznany format pliku z tłumaczeniami.")
    items = payload.get("overrides")
    if not isinstance(items, list):
        raise ValueError("Plik nie zawiera listy „overrides”.")
    for item in items:
        if not isinstance(item, dict) or not {"language", "msgid", "text"} <= set(item):
            raise ValueError("Wpis bez języka, napisu albo tłumaczenia.")
    return items


def write(items: list[dict], *, dry_run: bool = False) -> dict[str, Report]:
    """Zapisuje tłumaczenia do każdego katalogu języka, w którym stoi dany napis."""
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
        updates: dict[tuple[str | None, str], dict[int, str]] = defaultdict(dict)
        for item in group:
            form = item.get("plural_index")
            row = index.by_key.get(string_key(item.get("msgctxt") or None, item["msgid"], form))
            if row is None:
                report.stale.append(item["msgid"][:60])
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


@transaction.atomic
def prune(languages: list[str]) -> int:
    """Usuwa nakładki, których tekst jest już w katalogu (po wdrożeniu eksportu)."""
    removed = 0
    for language in languages:
        index = catalogs.index(language)
        for override in TranslationOverride.objects.filter(language=language):
            row = index.by_key.get(override.key)
            if row is not None and row.translation == override.text:
                audit(None, "translation.pruned", override, {"language": language, "key": override.key})
                override.delete()
                removed += 1
        transaction.on_commit(lambda language=language: runtime.publish(language))
    return removed
