"""Zmiany plakatów: zapis z plikami, publikacja, kolejność, archiwizacja i skasowanie.

Każda funkcja dostaje konkurs **wprost** i działa wyłącznie na plakatach tego konkursu – widok
podaje ``request.competition``, więc nie ma tu ścieżki, którą dałoby się ruszyć plakat sąsiada.

Po każdej zmianie widoczności (zapis, publikacja, archiwizacja, skasowanie) wołamy
``availability.refresh``: sygnał ``post_save``/``post_delete`` już wyczyścił pamięć „czy są
plakaty” (odnośnik w stopce), a to przeliczenie zapisuje wartość od razu – koszt zapytania płaci
koordynator, który kliknął, a nie pierwszy gość po nim. Pamięć stron publicznych unieważnia sygnał
w ``apps.web.page_cache``; zmiana kolejności idzie ``bulk_update`` bez sygnałów, więc tam oba
unieważnienia są wołane jawnie.

**Skasowanie a statystyki.** Plakat, który ktoś już pobrał, nie jest kasowany, tylko
archiwizowany: znika z ``/plakaty/``, a jego pobrania zostają w statystykach i w eksporcie CSV.
Organizator prosił o liczby; „Usuń”, które po cichu zabiera część liczb, byłoby odpowiedzią na
inne pytanie. Plakat bez pobrań kasuje się naprawdę – razem z plikami w storage.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from . import availability
from .models import PromoMaterial
from .previews import make_thumbnail
from .validators import IMAGE_FORMATS

logger = logging.getLogger(__name__)

#: Kierunki przesunięcia na liście – zamknięta lista, bo wartość przychodzi z formularza.
MOVE_UP = "up"
MOVE_DOWN = "down"


def _delete_stored(field_file) -> None:
    """Kasuje plik ze storage, nie ruszając wiersza. Awaria storage nie wywraca zapisu plakatu.

    Plik, którego nie udało się skasować, jest sierotą w buckecie – kosztem miejsca, a nie
    błędem, który koordynator mógłby naprawić z formularza. Log niesie nazwę obiektu, żeby operator
    mógł go sprzątnąć ręcznie.
    """
    name = getattr(field_file, "name", "")
    if not name:
        return
    try:
        field_file.storage.delete(name)
    except Exception:  # noqa: BLE001 - patrz docstring
        logger.warning("Nie udało się skasować pliku %s ze storage.", name, exc_info=True)


def _next_position(competition) -> int:
    top = PromoMaterial.objects.for_competition(competition).aggregate(top=Max("position"))["top"]
    return 0 if top is None else top + 1


def save_material(form, competition, *, user=None) -> tuple[PromoMaterial, dict]:
    """Zapisuje plakat z formularza ``PromoMaterialForm``. Zwraca ``(plakat, zmiany)``.

    ``zmiany`` to słownik do wpisu audytowego – nazwy zmienionych pól i format pliku, nigdy treść
    ani nazwa pliku od przesyłającego.

    Reguła podglądu (``apps.promo.previews``):

    - wgrany podgląd wygrywa zawsze i staje się „własnym” (nie jest później nadpisywany),
    - „usuń własny podgląd” wraca do miniatury automatycznej (JPG/PNG) albo do ikony (PDF),
    - nowy plik plakatu przelicza miniaturę **wyłącznie** wtedy, gdy obecna była automatyczna
      albo jej nie było – własnego podglądu podmiana pliku nie rusza.
    """
    material = form.instance
    creating = material.pk is None
    changed = [name for name in form.changed_data if name not in ("file", "preview", "clear_preview")]
    upload = form.cleaned_data.get("file")
    preview_upload = form.cleaned_data.get("preview")
    clear_preview = form.cleaned_data.get("clear_preview", False)

    old_file = material.file if (upload and not creating) else None
    old_file_name = old_file.name if old_file else ""
    old_preview_name = material.preview.name if material.preview else ""
    stale_files = []

    material = form.save(commit=False)
    material.competition = competition
    if creating:
        material.created_by = user if getattr(user, "is_authenticated", False) else None
        material.position = _next_position(competition)
    if upload:
        material.file_format = form.detected_format
        material.file_size = upload.size
        material.file = upload
        changed.append("file")
        if old_file_name:
            stale_files.append((material.file.storage, old_file_name))

    regenerate = False
    if preview_upload:
        material.preview = preview_upload
        material.preview_is_generated = False
        changed.append("preview")
    elif clear_preview:
        material.preview = ""
        material.preview_is_generated = False
        regenerate = True
        changed.append("preview")
    elif upload and (not old_preview_name or material.preview_is_generated):
        material.preview = ""
        material.preview_is_generated = False
        regenerate = True
    if regenerate and material.file_format in IMAGE_FORMATS:
        if upload:
            thumbnail = make_thumbnail(upload)
        else:
            # „Usuń własny podgląd” bez nowego pliku – miniatura z pliku, który już leży w storage.
            with material.file.open("rb") as handle:
                thumbnail = make_thumbnail(handle)
        if thumbnail is not None:
            material.preview = thumbnail
            material.preview_is_generated = True
    if old_preview_name and material.preview.name != old_preview_name:
        stale_files.append((material.preview.storage, old_preview_name))

    with transaction.atomic():
        material.save()
    # Stare pliki kasujemy **po** zapisie wiersza: gdyby zapis się nie udał, plakat wskazywałby
    # dalej na plik, którego już nie ma.
    for storage, name in stale_files:
        try:
            storage.delete(name)
        except Exception:  # noqa: BLE001 - patrz ``_delete_stored``
            logger.warning("Nie udało się skasować pliku %s ze storage.", name, exc_info=True)
    availability.refresh(competition)
    return material, {"fields": sorted(set(changed)), "format": material.file_format, "created": creating}


def set_published(material: PromoMaterial, published: bool) -> bool:
    """Publikuje albo zdejmuje plakat. ``False``, gdy nic się nie zmieniło albo plakat jest w archiwum."""
    if material.is_archived or material.is_published == published:
        return False
    material.is_published = published
    material.save(update_fields=["is_published", "updated_at"])
    availability.refresh(material.competition)
    return True


def move(material: PromoMaterial, direction: str) -> bool:
    """Przesuwa plakat o jedno miejsce na liście. ``False``, gdy już stoi na brzegu.

    Numeracja jest przepisywana od zera po każdym przesunięciu, a nie zamieniana parami: po
    skasowaniach i archiwizacjach w ``position`` zostają dziury i duplikaty (dwa plakaty dodane
    w tej samej chwili), a zamiana dwóch równych liczb nie przestawia niczego.
    """
    from apps.web.page_cache import invalidate_competition

    competition = material.competition
    ordered = list(
        PromoMaterial.objects.for_competition(competition)
        .filter(archived_at__isnull=True)
        .order_by("position", "id")
    )
    index = next((i for i, item in enumerate(ordered) if item.pk == material.pk), None)
    if index is None:
        return False
    target = index - 1 if direction == MOVE_UP else index + 1
    if direction not in (MOVE_UP, MOVE_DOWN) or not 0 <= target < len(ordered):
        return False
    ordered[index], ordered[target] = ordered[target], ordered[index]
    for position, item in enumerate(ordered):
        item.position = position
    PromoMaterial.objects.bulk_update(ordered, ["position"])
    # ``bulk_update`` nie wysyła sygnałów – oba unieważnienia jawnie (docstring modułu).
    invalidate_competition(competition.pk)
    availability.refresh(competition)
    return True


def remove(material: PromoMaterial) -> str:
    """Kasuje plakat albo – gdy ma pobrania – archiwizuje go. Zwraca ``"deleted"``/``"archived"``."""
    competition = material.competition
    if material.downloads.exists():
        material.archived_at = timezone.now()
        material.is_published = False
        material.save(update_fields=["archived_at", "is_published", "updated_at"])
        availability.refresh(competition)
        return "archived"
    file_field, preview_field = material.file, material.preview
    material.delete()
    _delete_stored(file_field)
    _delete_stored(preview_field)
    availability.refresh(competition)
    return "deleted"


def restore(material: PromoMaterial) -> bool:
    """Wyjmuje plakat z archiwum – jako **szkic**, na końcu listy. Publikacja to osobna decyzja."""
    if not material.is_archived:
        return False
    material.archived_at = None
    material.is_published = False
    material.position = _next_position(material.competition)
    material.save(update_fields=["archived_at", "is_published", "position", "updated_at"])
    availability.refresh(material.competition)
    return True
