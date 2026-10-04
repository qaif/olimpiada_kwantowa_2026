"""Nagrody w eksporcie danych konta (RODO, art. 15 i 20) – sekcja ``medale`` pliku ``dane.json``.

Osobny moduł, a nie funkcja w ``apps.accounts.data_export``, z tego samego powodu, co sekcje innych
aplikacji: konta nie wiedzą, jak medale są zapisane, a medale – jak wygląda reszta pliku.
"""

from __future__ import annotations


def participant_medals(participant) -> list[dict]:
    """Ogłoszone nagrody i ręczne zmiany dotyczące wpisów tego profilu. Bez autora zmiany."""
    if participant is None:
        return []
    from .models import Award, MedalOverride, MedalScheme

    labels = dict(Award.choices)
    entry_ids = {
        entry_id: stage for entry_id, stage in participant.stage_entries.values_list("pk", "stage_id")
    }
    if not entry_ids:
        return []
    rows: list[dict] = []
    for scheme in MedalScheme.objects.filter(stage_id__in=set(entry_ids.values()), frozen_at__isnull=False):
        for entry_id in entry_ids:
            item = (scheme.awards or {}).get(str(entry_id))
            if item is None:
                continue
            rows.append(
                {
                    "etap_id": scheme.stage_id,
                    "nagroda": str(labels.get(item["award"], item["award"])),
                    "nagroda_wyliczona": str(labels.get(item["computed"], item["computed"])),
                    "miejsce": item["rank"],
                    "suma_punktow": item["total"],
                    "ogloszono": scheme.frozen_at.isoformat(),
                }
            )
    for override in MedalOverride.objects.filter(entry_id__in=list(entry_ids)).select_related("scheme"):
        rows.append(
            {
                "etap_id": override.scheme.stage_id,
                "zmiana_reczna": str(labels.get(override.award, override.award)),
                "uzasadnienie": override.justification,
                "data": override.updated_at.isoformat(),
            }
        )
    return rows
