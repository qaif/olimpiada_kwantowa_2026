"""Wyświetlenia materiałów: licznik łącznie i liczba unikalnych widzów – bez historii oglądania.

Koordynator dostaje dwie liczby na materiał: **ile razy** go otwarto i **ile różnych kont** to
zrobiło. Pierwsza to zwykły licznik w wierszu materiału (``view_count``, ``UPDATE … + 1`` bez
odczytu). Druga wymaga rozpoznania „to samo konto co wtedy”, i tylko do tego służy
``WorkshopMaterialViewer``:

``viewer_hash = HMAC-SHA256(klucz, "<id materiału>:<id konta>")``

- klucz jest wyprowadzony z ``SECRET_KEY`` z osobnym kontekstem (``KEY_CONTEXT``) – ta sama
  konstrukcja, co pseudonim IP przy plakatach (``apps.promo.tracking``), więc klucza nie ma w bazie
  ani w jej kopii zapasowej,
- identyfikator **materiału** jest w treści skrótu: ta sama osoba przy dwóch materiałach ma dwa
  niepowiązane pseudonimy, więc z tabeli nie da się złożyć, co jedna osoba oglądała,
- nie zapisujemy adresu IP, przeglądarki ani godziny każdego wyświetlenia – jedynie chwilę
  **pierwszego** (do retencji). Kto chce wiedzieć, kto konkretnie oglądał, nie dowie się tego
  z serwisu, i tak ma być: to są materiały edukacyjne, a nie lista obecności (ta jest osobno
  i wpisuje ją koordynator – ``cms.WorkshopAttendance``).

Wyświetleń koordynatora tego konkursu nie liczymy – sprawdza, co wgrał, i zawyżałby liczby,
o które sam pyta (ta sama reguła, co przy plakatach).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from functools import lru_cache

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Count, F

from .models import WorkshopMaterial, WorkshopMaterialViewer

logger = logging.getLogger(__name__)

#: Kontekst wyprowadzenia klucza – osobny napis dla osobnego zastosowania ``SECRET_KEY``.
KEY_CONTEXT = "workshop-material-viewer"


@lru_cache(maxsize=4)
def _key(secret: str) -> bytes:
    return hashlib.sha256(f"{KEY_CONTEXT}:{secret}".encode()).digest()


def viewer_hash(material_id: int, user_id: int) -> str:
    """Pseudonim pary (materiał, konto) – 64 znaki szesnastkowe."""
    message = f"{material_id}:{user_id}".encode()
    return hmac.new(_key(settings.SECRET_KEY), message, hashlib.sha256).hexdigest()


def record_view(material: WorkshopMaterial, user) -> None:
    """Dolicza wyświetlenie i (przy pierwszym razie) unikalnego widza. Awaria bazy nie psuje odtwarzania.

    Licznik idzie ``F()``-em, więc dwa równoległe wyświetlenia nie gubią jednego. Widz idzie przez
    ``bulk_create(ignore_conflicts=True)`` na unikalnym ograniczeniu – bez odczytu przed zapisem
    i bez wyjątku przy wyścigu dwóch kart tej samej osoby.
    """
    try:
        WorkshopMaterial.objects.filter(pk=material.pk).update(view_count=F("view_count") + 1)
        WorkshopMaterialViewer.objects.bulk_create(
            [WorkshopMaterialViewer(material_id=material.pk, viewer_hash=viewer_hash(material.pk, user.pk))],
            ignore_conflicts=True,
        )
    except DatabaseError:
        # Statystyka jest dodatkiem: widz ma obejrzeć film nawet wtedy, gdy licznik się nie zapisał.
        logger.warning("Nie udało się zapisać wyświetlenia materiału #%s.", material.pk, exc_info=True)


def unique_viewers(materials) -> dict[int, int]:
    """``{id materiału: liczba unikalnych widzów}`` jednym zapytaniem dla całej listy."""
    ids = [material.pk for material in materials]
    if not ids:
        return {}
    rows = (
        WorkshopMaterialViewer.objects.filter(material_id__in=ids)
        .values("material_id")
        .annotate(count=Count("id"))
    )
    return {row["material_id"]: row["count"] for row in rows}
