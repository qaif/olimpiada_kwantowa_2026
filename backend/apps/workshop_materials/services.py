"""Czynności na materiałach: wgrywanie (start → części → zakończenie), publikacja, kolejność,
przepięcie do warsztatu, usunięcie i ułożenie list do wyświetlenia.

Każda funkcja dostaje konkurs albo materiał **już zawężony** do konkursu żądania (widok bierze go
z ``WorkshopMaterial.objects.for_competition(request.competition)``) – nie ma tu ścieżki, którą
dałoby się ruszyć materiał sąsiada.

Pamięć stron publicznych (``apps.web.page_cache``) unieważnia sygnał ``post_save``/``post_delete``
materiału: strona ``/warsztaty/`` pokazuje gościom zapowiedź „są materiały – zaloguj się”, a ta
zależy od tego, czy jakiś materiał jest opublikowany. Zmiana kolejności idzie ``bulk_update`` bez
sygnałów, więc tam unieważnienie jest wołane jawnie – ten sam wzorzec, co ``apps.promo.services``.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.cms.workshops import workshop_rows, workshops_page

from . import formats
from .models import MaterialKind, MaterialStatus, WorkshopMaterial
from .storage import (
    MAX_PARTS,
    PART_SIZE,
    PARTS_PER_SIGN,
    abort_quietly,
    delete_quietly,
    get_material_storage,
)

logger = logging.getLogger(__name__)

#: Kierunki przesunięcia na liście – zamknięta lista, bo wartość przychodzi z formularza.
MOVE_UP = "up"
MOVE_DOWN = "down"


class UploadError(Exception):
    """Wgrywanie nie może być kontynuowane. Komunikat jest zdaniem dla koordynatora."""


# --- harmonogram ----------------------------------------------------------------------------------


def schedule(competition) -> list[dict]:
    """Wiersze harmonogramu warsztatów **tego** konkursu (z odczytaną datą), od najwcześniejszego.

    Jedno źródło: ``apps.cms.workshops.workshop_rows`` na stronie ``workshops_page(competition)`` –
    to samo, z którego biorą się kolumny tabeli obecności. Wiersz bez daty maszynowej nie ma klucza,
    więc nie da się do niego przypiąć materiału (ekran koordynatora mówi to wprost).
    """
    return workshop_rows(workshops_page(competition))


def row_by_key(competition, key: str) -> dict | None:
    return next((row for row in schedule(competition) if row["key"] == key), None)


def row_label(topic: str, when: date | None) -> str:
    """„Kubity i bramki · 12.11.2026” – etykieta warsztatu w listach wyboru i w nagłówkach."""
    return f"{topic} · {when:%d.%m.%Y}" if when else topic


# --- klucz i kolejność -----------------------------------------------------------------------------


def build_object_key(competition, extension: str) -> str:
    """``workshop-materials/<konkurs>/<losowy identyfikator>.<rozszerzenie>``.

    Nazwa pliku od przesyłającego nie wchodzi do klucza wcale – ta sama zasada, co przy
    rozwiązaniach (``apps.submissions.storage.build_object_key``). Rozszerzenie pochodzi z zamkniętej
    listy formatów, więc nie ma w nim ani kropki, ani ukośnika.
    """
    if extension not in formats.ALL_FORMATS:
        raise ValueError(f"Nieznany format: {extension!r}.")
    return f"workshop-materials/{int(competition.pk)}/{uuid.uuid4().hex}.{extension}"


def _next_position(competition, workshop_key: str) -> int:
    top = (
        WorkshopMaterial.objects.for_competition(competition)
        .filter(workshop_key=workshop_key)
        .aggregate(top=Max("position"))["top"]
    )
    return 0 if top is None else top + 1


def part_count(size: int) -> int:
    return max(1, math.ceil(size / PART_SIZE))


# --- wgrywanie pliku ------------------------------------------------------------------------------


def _max_bytes(kind: str) -> int:
    return formats.video_max_bytes() if kind == MaterialKind.VIDEO else formats.file_max_bytes()


def check_declared_file(kind: str, filename: str, size: int) -> str:
    """Wstępne sprawdzenie **deklaracji** przeglądarki: rozszerzenie z listy i rozmiar w limicie.

    To nie jest walidacja treści – tę robi ``complete_upload`` po bajtach z magazynu. Tu chodzi
    o to, żeby nie zakładać wgrywania czterogigabajtowego pliku ``.exe`` tylko po to, żeby odrzucić
    go po godzinie. Zwraca rozszerzenie (klucz formatu), pod którym wgrywanie zostanie założone.
    """
    extension = formats.normalise_extension(filename)
    allowed = formats.VIDEO_FORMATS if kind == MaterialKind.VIDEO else formats.FILE_FORMATS
    if extension not in allowed:
        listed = (
            formats.allowed_video_extensions()
            if kind == MaterialKind.VIDEO
            else formats.allowed_file_extensions()
        )
        raise UploadError(
            f"Pliki „.{extension or '?'}” nie są przyjmowane jako {kind_label(kind)}. Dozwolone: {listed}."
        )
    if size <= 0:
        raise UploadError("Plik jest pusty.")
    limit = _max_bytes(kind)
    if size > limit:
        raise UploadError(
            f"Plik ma {size / formats.MEGABYTE:.0f} MB – limit dla {kind_label(kind, genitive=True)} "
            f"to {limit // formats.MEGABYTE} MB."
        )
    if part_count(size) > MAX_PARTS:  # pragma: no cover - przy limicie 4 GB niemożliwe
        raise UploadError("Plik jest za duży, żeby wgrać go częściami.")
    return extension


def kind_label(kind: str, *, genitive: bool = False) -> str:
    if kind == MaterialKind.VIDEO:
        return "filmu" if genitive else "film"
    return "pliku" if genitive else "plik"


def start_upload(
    competition,
    *,
    row: dict,
    kind: str,
    title: str,
    description: str,
    is_published: bool,
    filename: str,
    size: int,
    user=None,
) -> WorkshopMaterial:
    """Zakłada materiał w stanie ``UPLOADING`` i wgrywanie wieloczęściowe w MinIO.

    Wiersz powstaje **przed** wgrywaniem, a nie po nim: bez niego porzucone wgrywanie nie miałoby
    w bazie śladu, po którym zadanie sprzątające (``tasks.cleanup_stale_uploads``) je znajdzie.
    Materiał w tym stanie nie jest widoczny dla nikogo poza koordynatorem.
    """
    if kind not in (MaterialKind.VIDEO, MaterialKind.FILE):
        raise UploadError("Wgrać można wyłącznie film albo plik.")
    extension = check_declared_file(kind, filename, size)
    material = WorkshopMaterial(
        competition=competition,
        workshop_key=row["key"],
        workshop_topic=row["topic"][:300],
        workshop_date=row["date_value"],
        kind=kind,
        title=title,
        description=description,
        is_published=is_published,
        file_format=extension,
        size_bytes=size,
        status=MaterialStatus.UPLOADING,
        position=_next_position(competition, row["key"]),
        object_key=build_object_key(competition, extension),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    storage = get_material_storage()
    try:
        material.upload_id = storage.create_upload(
            material.object_key, formats.ALL_FORMATS[extension].content_type
        )
    except Exception as exc:  # noqa: BLE001 - awaria magazynu to dla koordynatora „spróbuj później”
        logger.exception("Nie udało się założyć wgrywania materiału w magazynie.")
        raise UploadError("Magazyn plików nie odpowiada. Spróbuj ponownie za chwilę.") from exc
    material.save()
    return material


def sign_parts(material: WorkshopMaterial, numbers: list[int]) -> dict[int, str]:
    """Adresy części o podanych numerach. Wyłącznie numery z zakresu wynikającego z rozmiaru.

    Zakres jest tu jedyną kontrolą ilości: adres części nie ogranicza rozmiaru treści (tak działa
    S3), więc gdyby serwer podpisywał dowolne numery, konto koordynatora mogłoby dosypywać do
    magazynu części bez końca. Nadmiarowe albo za duże części odrzuci i tak ``complete_upload``.
    """
    if material.status != MaterialStatus.UPLOADING or not material.upload_id:
        raise UploadError("To wgrywanie jest już zamknięte.")
    total = part_count(material.size_bytes)
    wanted = sorted(set(numbers))
    if not wanted or len(wanted) > PARTS_PER_SIGN or wanted[0] < 1 or wanted[-1] > total:
        raise UploadError("Nieprawidłowe numery części.")
    storage = get_material_storage()
    return {
        number: storage.presign_part(material.object_key, material.upload_id, number) for number in wanted
    }


def _discard(material: WorkshopMaterial, storage) -> None:
    """Sprzątanie po nieudanym wgrywaniu: części w MinIO, złożony obiekt i sam wiersz."""
    abort_quietly(storage, material.object_key, material.upload_id)
    delete_quietly(storage, material.object_key)
    if material.pk:
        material.delete()


def _locked(material: WorkshopMaterial) -> WorkshopMaterial | None:
    """Ten sam wiersz, zablokowany do końca transakcji (``SELECT … FOR UPDATE``) – albo ``None``.

    „Zakończ” i „przerwij” zmieniają stan wgrywania na podstawie tego, co przeczytały chwilę
    wcześniej. Dwa takie żądania naraz (przeglądarka ponowiła żądanie po zerwanym połączeniu,
    koordynator kliknął w dwóch kartach) bez blokady kończyłyby się tym, że drugie – widząc
    „wgrywanie już złożone” w MinIO – uznałoby plik za zepsuty i **skasowało** poprawny materiał.
    Z blokadą drugie czeka na pierwsze, a potem widzi stan inny niż ``UPLOADING`` i odpada.
    """
    return WorkshopMaterial.objects.select_for_update().filter(pk=material.pk).first()


def complete_upload(material: WorkshopMaterial) -> WorkshopMaterial:
    """Składa plik z części, sprawdza go i przestawia materiał dalej – albo sprząta i podnosi błąd.

    Sprawdzenia, w kolejności kosztu:

    1. lista części **od MinIO** – komplet numerów od 1 do N, każda poza ostatnią dokładnie
       ``PART_SIZE`` bajtów, suma równa zadeklarowanemu rozmiarowi,
    2. złożenie i ``HeadObject`` – rozmiar obiektu równy deklaracji (druga, niezależna kontrola),
    3. pierwsze ``HEADER_PROBE_BYTES`` bajtów – format **po treści** (``apps.workshop_materials.formats``).

    Film po udanym sprawdzeniu jest od razu ``READY`` (dlaczego bez ClamAV-a – ``tasks``), plik
    przechodzi w ``SCANNING`` i czeka na werdykt skanera. Każda porażka kasuje wiersz i obiekt:
    materiał, którego nie da się pokazać, nie ma po co wisieć na liście.

    Całość dzieje się pod blokadą wiersza (``_locked``). Błąd jest **zwracany** z transakcji,
    a podnoszony dopiero po niej: wyjątek w środku wycofałby skasowanie wiersza, a obiekt
    w magazynie (którego transakcja bazy nie obejmuje) i tak by już zniknął.
    """
    with transaction.atomic():
        locked = _locked(material)
        error = _complete(locked) if locked is not None else "To wgrywanie jest już zamknięte."
    if error:
        raise UploadError(error)
    if locked.status == MaterialStatus.SCANNING:
        enqueue_scan(locked)
    return locked


def _complete(material: WorkshopMaterial) -> str:
    """Treść ``complete_upload`` pod blokadą. Zwraca komunikat błędu albo pusty napis."""
    if material.status != MaterialStatus.UPLOADING or not material.upload_id:
        return "To wgrywanie jest już zamknięte."
    storage = get_material_storage()
    expected = part_count(material.size_bytes)
    try:
        parts = sorted(
            storage.list_parts(material.object_key, material.upload_id), key=lambda p: p["PartNumber"]
        )
    except Exception:  # noqa: BLE001 - awaria magazynu: wiersz zostaje, można spróbować ponownie
        logger.exception("Nie udało się odczytać części wgrywania materiału #%s.", material.pk)
        return "Magazyn plików nie odpowiada. Spróbuj dokończyć za chwilę."

    numbers = [part["PartNumber"] for part in parts]
    sizes_ok = all(part["Size"] == PART_SIZE for part in parts[:-1])
    if (
        numbers != list(range(1, expected + 1))
        or not sizes_ok
        or sum(p["Size"] for p in parts) != material.size_bytes
    ):
        _discard(material, storage)
        return (
            "Do magazynu nie dotarł komplet pliku (brakujące albo nadmiarowe części). Materiał nie "
            "został zapisany – wgraj plik jeszcze raz."
        )
    try:
        storage.complete_upload(material.object_key, material.upload_id, parts)
        stored_size = storage.size(material.object_key)
        header = storage.read_head(material.object_key, formats.HEADER_PROBE_BYTES)
    except Exception:  # noqa: BLE001
        logger.exception("Nie udało się złożyć pliku materiału #%s.", material.pk)
        _discard(material, storage)
        return "Nie udało się złożyć pliku w magazynie. Wgraj go jeszcze raz."
    if stored_size != material.size_bytes:
        _discard(material, storage)
        return "Rozmiar zapisanego pliku nie zgadza się z wysłanym. Wgraj go jeszcze raz."
    try:
        if material.kind == MaterialKind.VIDEO:
            fmt = formats.verify_video(header)
        else:
            fmt = formats.verify_file(header, material.file_format)
    except formats.FormatError as exc:
        _discard(material, storage)
        return str(exc)

    material.file_format = fmt.key
    material.upload_id = ""
    if material.kind == MaterialKind.VIDEO:
        material.status = MaterialStatus.READY
        material.ready_at = timezone.now()
    else:
        material.status = MaterialStatus.SCANNING
    material.save(update_fields=["file_format", "upload_id", "status", "ready_at", "updated_at"])
    return ""


def enqueue_scan(material: WorkshopMaterial) -> None:
    """Skan ClamAV-em po zatwierdzeniu transakcji – zadanie nie może przeczytać wiersza sprzed zapisu."""
    from .tasks import scan_material

    transaction.on_commit(lambda: scan_material.delay(material.pk))


def abort_upload(material: WorkshopMaterial) -> None:
    """Koordynator przerwał wgrywanie (albo zamknął kartę i wrócił) – części i wiersz znikają."""
    with transaction.atomic():
        locked = _locked(material)
        closed = locked is None or locked.status != MaterialStatus.UPLOADING
        if not closed:
            _discard(locked, get_material_storage())
    if closed:
        raise UploadError("To wgrywanie jest już zamknięte.")


# --- odnośnik -------------------------------------------------------------------------------------


def create_link(
    competition, *, row: dict, title: str, description: str, url: str, is_published: bool, user=None
) -> WorkshopMaterial:
    """Odnośnik jest gotowy od razu – nie ma pliku do sprawdzenia."""
    return WorkshopMaterial.objects.create(
        competition=competition,
        workshop_key=row["key"],
        workshop_topic=row["topic"][:300],
        workshop_date=row["date_value"],
        kind=MaterialKind.LINK,
        title=title,
        description=description,
        url=url,
        is_published=is_published,
        status=MaterialStatus.READY,
        ready_at=timezone.now(),
        position=_next_position(competition, row["key"]),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )


# --- zmiany na liście -----------------------------------------------------------------------------


def attach(material: WorkshopMaterial, row: dict) -> bool:
    """Przypina materiał do wiersza harmonogramu (także „osierocony” – patrz ``models``).

    Migawka tematu i daty jest przepisywana z wiersza, na końcu listy nowego warsztatu. ``False``,
    gdy materiał już tam wisi i migawka się zgadza.
    """
    same_key = material.workshop_key == row["key"]
    if (
        same_key
        and material.workshop_topic == row["topic"][:300]
        and material.workshop_date == row["date_value"]
    ):
        return False
    if not same_key:
        material.position = _next_position(material.competition, row["key"])
    material.workshop_key = row["key"]
    material.workshop_topic = row["topic"][:300]
    material.workshop_date = row["date_value"]
    material.save(update_fields=["workshop_key", "workshop_topic", "workshop_date", "position", "updated_at"])
    return True


def set_published(material: WorkshopMaterial, published: bool) -> bool:
    """Publikuje albo zdejmuje materiał. ``False``, gdy nic się nie zmieniło.

    Materiał jeszcze nie gotowy (wgrywanie, skan) **wolno** oznaczyć jako opublikowany – pojawi się
    u widzów sam, gdy skaner go przepuści. Odrzuconego nie: nie ma już czego pokazać.
    """
    if material.status == MaterialStatus.REJECTED and published:
        return False
    if material.is_published == published:
        return False
    material.is_published = published
    material.save(update_fields=["is_published", "updated_at"])
    return True


def move(material: WorkshopMaterial, direction: str) -> bool:
    """Przesuwa materiał o jedno miejsce **w obrębie swojego warsztatu**. ``False`` na brzegu.

    Numeracja jest przepisywana od zera, a nie zamieniana parami – ta sama reguła i z tego samego
    powodu, co przy plakatach (dziury i duplikaty w ``position`` po skasowaniach).
    """
    from apps.web.page_cache import invalidate_competition

    ordered = list(
        WorkshopMaterial.objects.for_competition(material.competition)
        .filter(workshop_key=material.workshop_key)
        .order_by("position", "id")
    )
    index = next((i for i, item in enumerate(ordered) if item.pk == material.pk), None)
    if index is None or direction not in (MOVE_UP, MOVE_DOWN):
        return False
    target = index - 1 if direction == MOVE_UP else index + 1
    if not 0 <= target < len(ordered):
        return False
    ordered[index], ordered[target] = ordered[target], ordered[index]
    for position, item in enumerate(ordered):
        item.position = position
    WorkshopMaterial.objects.bulk_update(ordered, ["position"])
    invalidate_competition(material.competition_id)
    return True


def remove(material: WorkshopMaterial) -> None:
    """Kasuje materiał razem z obiektem w magazynie (i porzuca wgrywanie, jeśli trwało).

    Wiersz znika **przed** obiektem: gdyby było odwrotnie i skasowanie wiersza się nie udało,
    zostałby materiał wskazujący na plik, którego nie ma. Obiekt, którego nie da się skasować,
    zostaje w logu (``delete_quietly``).
    """
    storage = get_material_storage()
    key, upload_id = material.object_key, material.upload_id
    material.delete()
    abort_quietly(storage, key, upload_id)
    delete_quietly(storage, key)


def apply_scan_verdict(material: WorkshopMaterial, *, clean: bool, note: str = "") -> None:
    """Werdykt ClamAV-a: czysty → ``READY``; zagrożenie albo plik nie do sprawdzenia → ``REJECTED``.

    Przy odrzuceniu obiekt jest kasowany od razu – zainfekowany plik nie ma prawa leżeć w buckecie
    dłużej, niż trwa decyzja. Wiersz zostaje z adnotacją, żeby koordynator wiedział, co się stało.
    """
    if clean:
        material.status = MaterialStatus.READY
        material.ready_at = timezone.now()
        material.status_note = ""
        material.save(update_fields=["status", "ready_at", "status_note", "updated_at"])
        return
    delete_quietly(get_material_storage(), material.object_key)
    material.status = MaterialStatus.REJECTED
    material.is_published = False
    material.object_key = ""
    material.status_note = note[:300]
    material.save(update_fields=["status", "is_published", "object_key", "status_note", "updated_at"])


# --- listy do wyświetlenia ------------------------------------------------------------------------


@dataclass
class WorkshopGroup:
    """Jeden warsztat z jego materiałami – wiersz harmonogramu albo migawka „osieroconego” klucza."""

    key: str
    topic: str
    date_value: date | None
    date_text: str = ""
    time: str = ""
    lecturer: str = ""
    in_schedule: bool = True
    materials: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return row_label(self.topic, self.date_value)


def _group(row: dict) -> WorkshopGroup:
    return WorkshopGroup(
        key=row["key"],
        topic=row["topic"],
        date_value=row["date_value"],
        date_text=row.get("date", ""),
        time=row.get("time", ""),
        lecturer=row.get("lecturer", ""),
    )


def grouped(
    competition, materials, *, include_empty: bool
) -> tuple[list[WorkshopGroup], list[WorkshopGroup]]:
    """``(warsztaty z harmonogramu, warsztaty osierocone)`` z materiałami w kolejności listy.

    ``include_empty``: koordynator widzi każdy wiersz harmonogramu (żeby mieć gdzie dodać materiał),
    widz – wyłącznie warsztaty, do których coś jest. Osierocone grupy powstają z migawki materiału,
    jedna na klucz, w kolejności daty.
    """
    rows = schedule(competition)
    groups = {row["key"]: _group(row) for row in rows}
    orphans: dict[str, WorkshopGroup] = {}
    for material in materials:
        group = groups.get(material.workshop_key)
        if group is None:
            group = orphans.setdefault(
                material.workshop_key,
                WorkshopGroup(
                    key=material.workshop_key,
                    topic=material.workshop_topic or material.workshop_key,
                    date_value=material.workshop_date,
                    in_schedule=False,
                ),
            )
        group.materials.append(material)
    scheduled = [group for group in groups.values() if include_empty or group.materials]
    orphaned = sorted(orphans.values(), key=lambda g: (g.date_value or date.max, g.topic))
    return scheduled, orphaned


def visible_materials(competition):
    """Materiały, które widzi zalogowany czytelnik: opublikowane i gotowe, w kolejności listy."""
    return (
        WorkshopMaterial.objects.for_competition(competition)
        .filter(is_published=True, status=MaterialStatus.READY)
        .order_by("workshop_key", "position", "id")
    )
