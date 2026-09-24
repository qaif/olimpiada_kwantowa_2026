"""Zadania Celery materiałów: skan ClamAV-em plików i sprzątanie porzuconych wgrywań.

**Pliki idą przez ClamAV, filmy – nie.** To jest decyzja, a nie przeoczenie, i ma trzy powody:

- ``StreamMaxLength`` clamd to 100 MB (``CLAMAV_STREAM_MAX_BYTES``), a nagranie warsztatu ma
  zwykle od kilkuset megabajtów do kilku gigabajtów. Podniesienie limitu znaczyłoby clamd trzymający
  gigabajty w pamięci w kontenerze z limitem 3 GB – na serwerze, na którym ta pamięć jest potrzebna
  bazie – a skan kilku gigabajtów to kilkanaście minut rdzenia procesora, który na tej maszynie
  i tak bywa zabierany przez hiperwizor,
- ClamAV w pliku MP4/WebM nie ma czego szukać: sygnatury dotyczą plików wykonywalnych, makr,
  skryptów i dokumentów. Film jest **dekodowany** przez odtwarzacz przeglądarki (w piaskownicy),
  a nie uruchamiany ani otwierany programem biurowym,
- bramką treści filmu jest sprawdzenie kontenera po bajtach (``formats.verify_video``): plik, który
  nie zaczyna się pudełkiem ``ftyp`` z marką MP4 albo nagłówkiem EBML z ``DocType webm``, w ogóle
  nie staje się materiałem. Adres podpisany dla widza wymusza przy tym ``Content-Type: video/*``,
  więc nawet plik z doklejoną treścią nie zostanie przez przeglądarkę potraktowany jak strona.

Pliki (PDF, prezentacje, notatniki, archiwa) są dokładnie tym, czym rozchodzi się złośliwa treść,
i mieszczą się w limicie skanera (``formats.file_max_bytes``) – więc idą przez ClamAV tak samo jak
rozwiązania uczestników: kolejka ``scan``, ponowienia przy niedostępnym skanerze z rosnącym odstępem.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.accounts.retention import add_months
from apps.submissions.antivirus import (
    VERDICT_INFECTED,
    ClamAVStreamTooLarge,
    ClamAVUnavailable,
    scan_stream,
)

from .models import VIEWER_RETENTION_MONTHS, MaterialStatus, WorkshopMaterial, WorkshopMaterialViewer
from .storage import abort_quietly, delete_quietly, get_material_storage

logger = logging.getLogger(__name__)

MAX_SCAN_RETRIES = 5
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 600
SCAN_TIMEOUT_SECONDS = 120

#: Po ilu godzinach wgrywanie bez „zakończ” uznajemy za porzucone. Doba – tyle samo, po ilu MinIO
#: sam kasuje niezłożone części (``api stale_uploads_expiry``, domyślnie 24 h). Czterogigabajtowy
#: plik przy łączu 10 Mb/s wgrywa się niecałą godzinę, więc doba nie odetnie nikomu pracy w toku.
STALE_UPLOAD_HOURS = 24


@shared_task(bind=True, max_retries=MAX_SCAN_RETRIES, name="apps.workshop_materials.tasks.scan_material")
def scan_material(self, material_id: int) -> str:
    """Skanuje plik materiału przez ClamAV i zapisuje werdykt (``services.apply_scan_verdict``).

    Materiał w innym stanie niż ``SCANNING`` jest pomijany – zadanie bywa dostarczone dwa razy
    (ponowne „Sprawdź ponownie”, restart workera), a werdykt ma zapaść raz.
    """
    from . import services

    material = WorkshopMaterial.objects.filter(pk=material_id).select_related("competition").first()
    if material is None or material.status != MaterialStatus.SCANNING:
        return "SKIPPED"
    storage = get_material_storage()
    try:
        stream = storage.open(material.object_key)
        try:
            verdict, signature = scan_stream(stream, timeout=SCAN_TIMEOUT_SECONDS, size=material.size_bytes)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
    except ClamAVStreamTooLarge:
        services.apply_scan_verdict(
            material,
            clean=False,
            note="Plik jest większy niż limit skanera antywirusowego – nie został przyjęty.",
        )
        return "TOO_LARGE"
    except ClamAVUnavailable as exc:
        countdown = min(RETRY_BASE_SECONDS * (2**self.request.retries), RETRY_MAX_SECONDS)
        logger.warning("ClamAV niedostępny przy skanie materiału %s: %s", material_id, exc)
        if self.request.retries >= MAX_SCAN_RETRIES:
            # Materiał zostaje w „sprawdzaniu”: koordynator ma przycisk „Sprawdź ponownie”, a plik
            # nie jest widoczny, dopóki werdykt nie zapadnie – domyślnie zamknięte.
            return "UNAVAILABLE"
        raise self.retry(exc=exc, countdown=countdown) from exc
    if verdict == VERDICT_INFECTED:
        logger.warning("Materiał %s zainfekowany (%s) – plik skasowany.", material_id, signature)
        services.apply_scan_verdict(
            material, clean=False, note=f"Skaner antywirusowy wykrył zagrożenie: {signature}."
        )
        return "INFECTED"
    services.apply_scan_verdict(material, clean=True)
    return "CLEAN"


def cleanup(now=None) -> dict:
    """Porzucone wgrywania i przeterminowane pseudonimy widzów. Zwraca liczby do logu.

    - materiał w stanie ``UPLOADING`` starszy niż ``STALE_UPLOAD_HOURS``: porzucamy wgrywanie
      w MinIO (części znikają od razu, a nie dopiero po dobie), kasujemy ewentualny obiekt i wiersz,
    - pseudonimy widzów starsze niż ``VIEWER_RETENTION_MONTHS``: kasowane (termin z rejestru
      czynności przetwarzania); licznik wyświetleń w materiale zostaje.

    Bez ``each_competition``: oba terminy są wspólne dla instalacji, a przebieg per konkurs byłby
    tym samym zapytaniem powtórzonym N razy (ten sam rachunek, co ``apps.promo.tasks``).
    """
    now = now or timezone.now()
    storage = get_material_storage()
    stale = list(
        WorkshopMaterial.objects.filter(
            status=MaterialStatus.UPLOADING, created_at__lt=now - timedelta(hours=STALE_UPLOAD_HOURS)
        )
    )
    for material in stale:
        abort_quietly(storage, material.object_key, material.upload_id)
        delete_quietly(storage, material.object_key)
        material.delete()
    viewers, _ = WorkshopMaterialViewer.objects.filter(
        first_seen_at__lt=add_months(now, -VIEWER_RETENTION_MONTHS)
    ).delete()
    if stale or viewers:
        logger.info(
            "Materiały z warsztatów: porzucone wgrywania %s, pseudonimy widzów %s.", len(stale), viewers
        )
    return {"stale_uploads": len(stale), "viewers": viewers}


@shared_task(name="apps.workshop_materials.tasks.cleanup")
def cleanup_task() -> dict:
    """Wywołanie z ``beat`` co godzinę (``CELERY_BEAT_SCHEDULE``).

    Co godzinę, a nie raz na dobę: porzucone wgrywanie zajmuje w magazynie tyle, ile zdążyło
    przyjść – przy filmie to bywają gigabajty – i nie ma powodu trzymać ich dłużej niż trzeba.
    """
    return cleanup()
