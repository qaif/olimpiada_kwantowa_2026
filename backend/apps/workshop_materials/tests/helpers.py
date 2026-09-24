"""Magazyn w pamięci, prawdziwe nagłówki plików i strona „Warsztaty” – wspólne dla testów materiałów.

``FakeMaterialStorage`` odtwarza **kontrakt** ``MaterialStorage`` tak, jak zachowuje się MinIO:
części wgrywania leżą osobno, dopóki ``complete_upload`` ich nie złoży, porzucenie je kasuje, a
podpisany adres niesie w sobie czas życia i nagłówki odpowiedzi. Stan jest na poziomie klasy
(fabryka ``get_material_storage`` tworzy nową instancję przy każdym wywołaniu – jak prawdziwa)
i czyści go fikstura ``storage`` przed każdym testem.

Nagłówki plików są **prawdziwe** w tym sensie, na którym stoi walidacja: MP4 zaczyna się pudełkiem
``ftyp`` z marką ``isom``, WebM nagłówkiem EBML z ``DocType webm``, PDF – ``%PDF-``.
"""

from __future__ import annotations

import itertools
from datetime import date
from io import BytesIO
from urllib.parse import quote

from apps.cms.models import ContentPage
from apps.cms.site_tree import home_page
from apps.cms.workshops import WORKSHOPS_SLUG, workshop_key
from apps.workshop_materials.models import MaterialKind, MaterialStatus, WorkshopMaterial
from apps.workshop_materials.storage import PART_SIZE, MaterialStorage

MP4_HEADER = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 32
MOV_HEADER = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + b"\x00" * 32
WEBM_HEADER = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\x82\x84webm" + b"\x00" * 32
MKV_HEADER = b"\x1a\x45\xdf\xa3\xa3\x42\x86\x81\x01\x42\x82\x88matroska" + b"\x00" * 32
PDF_BYTES = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
ZIP_BYTES = b"PK\x03\x04" + b"\x00" * 60
HTML_BYTES = b"<!doctype html><script>alert(1)</script>"


class FakeMaterialStorage(MaterialStorage):
    objects: dict[str, bytes] = {}
    uploads: dict[str, dict] = {}
    deleted: list[str] = []
    aborted: list[str] = []
    fail_create = False
    _ids = itertools.count(1)

    @classmethod
    def reset(cls) -> None:
        cls.objects = {}
        cls.uploads = {}
        cls.deleted = []
        cls.aborted = []
        cls.fail_create = False

    # --- kontrakt --------------------------------------------------------------------------------

    def create_upload(self, key, content_type):
        if self.fail_create:
            raise ConnectionError("MinIO nie odpowiada")
        upload_id = f"upload-{next(self._ids)}"
        self.uploads[upload_id] = {"key": key, "content_type": content_type, "parts": {}}
        return upload_id

    def presign_part(self, key, upload_id, part_number):
        return f"https://s3.test/submissions/{key}?uploadId={upload_id}&partNumber={part_number}&X-Amz-Signature=x"

    def list_parts(self, key, upload_id):
        parts = self.uploads[upload_id]["parts"]
        return [
            {"PartNumber": number, "ETag": f'"etag-{number}"', "Size": len(data)}
            for number, data in sorted(parts.items())
        ]

    def complete_upload(self, key, upload_id, parts):
        upload = self.uploads.pop(upload_id)
        self.objects[key] = b"".join(upload["parts"][p["PartNumber"]] for p in parts)

    def abort_upload(self, key, upload_id):
        self.aborted.append(key)
        if upload_id not in self.uploads:
            raise KeyError("NoSuchUpload")
        del self.uploads[upload_id]

    def size(self, key):
        data = self.objects.get(key)
        return None if data is None else len(data)

    def read_head(self, key, length):
        return self.objects[key][:length]

    def open(self, key):
        return BytesIO(self.objects[key])

    def presigned_get(self, key, *, ttl, content_type, content_disposition):
        return (
            f"https://s3.test/submissions/{key}?X-Amz-Expires={ttl}"
            f"&response-content-type={quote(content_type)}"
            f"&response-content-disposition={quote(content_disposition)}&X-Amz-Signature=fake"
        )

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    # --- pomocnicze: to, co w życiu robi przeglądarka ------------------------------------------

    @classmethod
    def put_part(cls, upload_id: str, number: int, data: bytes) -> None:
        cls.uploads[upload_id]["parts"][number] = data

    @classmethod
    def put_file(cls, upload_id: str, content: bytes) -> None:
        """Cały plik pocięty na części ``PART_SIZE`` – tak, jak tnie go skrypt przeglądarki."""
        for index in range(0, max(1, len(content)), PART_SIZE):
            cls.put_part(upload_id, index // PART_SIZE + 1, content[index : index + PART_SIZE])


STORAGE_PATH = "apps.workshop_materials.tests.helpers.FakeMaterialStorage"

ROWS = [
    ("Kubity i bramki kwantowe", date(2026, 11, 12), "dr Anna Kowalska"),
    ("Splątanie i nierówności Bella", date(2026, 12, 10), "prof. Jan Nowak"),
]


def schedule_block(rows=ROWS) -> tuple:
    return (
        "schedule",
        {
            "caption": "",
            "topic_label": "Temat",
            "date_label": "Termin",
            "time_label": "",
            "lecturer_label": "Prowadzący",
            "rows": [
                {
                    "topic": topic,
                    "date": when.strftime("%d.%m.%Y"),
                    "date_value": when,
                    "time": "",
                    "lecturer": who,
                }
                for topic, when, who in rows
            ],
        },
    )


def workshops_page_for(competition, rows=ROWS) -> ContentPage:
    """Strona „Warsztaty” **tego** konkursu z jednym blokiem ``schedule``."""
    home = home_page(competition.site)
    page = ContentPage(title="Warsztaty", slug=WORKSHOPS_SLUG, live=True, body=[schedule_block(rows)])
    home.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


def rewrite_rows(page: ContentPage, rows) -> None:
    page.body = [schedule_block(rows)]
    page.save()
    page.save_revision().publish()


def key_of(index: int = 0, rows=ROWS) -> str:
    topic, when, _ = rows[index]
    return workshop_key(topic, when)


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "workshop_materials": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def make_material(
    competition,
    *,
    kind: str = MaterialKind.VIDEO,
    content: bytes | None = None,
    published: bool = True,
    status: str = MaterialStatus.READY,
    index: int = 0,
    title: str = "Nagranie zajęć",
    **kwargs,
) -> WorkshopMaterial:
    """Materiał zapisany wprost (bez wgrywania) – do testów list, odtwarzacza i pobrania."""
    topic, when, _ = ROWS[index]
    fmt = {"video": "mp4", "file": "pdf", "link": ""}[kind]
    if content is None:
        content = {"video": MP4_HEADER, "file": PDF_BYTES, "link": b""}[kind]
    material = WorkshopMaterial.objects.create(
        competition=competition,
        workshop_key=kwargs.pop("workshop_key", workshop_key(topic, when)),
        workshop_topic=kwargs.pop("workshop_topic", topic),
        workshop_date=kwargs.pop("workshop_date", when),
        kind=kind,
        title=title,
        file_format=fmt,
        size_bytes=len(content),
        status=status,
        is_published=published,
        object_key=f"workshop-materials/{competition.pk}/{title.replace(' ', '-')}.{fmt}" if fmt else "",
        url="https://example.com/nagranie" if kind == MaterialKind.LINK else "",
        **kwargs,
    )
    if material.object_key:
        FakeMaterialStorage.objects[material.object_key] = content
    return material
