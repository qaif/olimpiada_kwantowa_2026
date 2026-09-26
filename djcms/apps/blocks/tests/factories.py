"""Budowanie stron z wtyczkami przez publiczne API django CMS – dla testów wtyczek i typów stron.

Ta sama droga, którą pójdzie importer (DJ-01g): ``cms.api.create_page`` → ``rescan_placeholders``
→ ``cms.api.add_plugin`` na wersji roboczej → ``Version.publish``. Żadnych zapisów wprost do tabel.
"""

from __future__ import annotations

import io

from cms.api import add_plugin
from cms.models import PageContent
from django.core.files.uploadedfile import SimpleUploadedFile
from djangocms_versioning.models import Version

LANGUAGE = "pl"


def draft(make_page, title: str, slug: str, template: str, **kwargs) -> tuple:
    """Strona z wersją roboczą (nieopublikowaną): ``(page, content, placeholders)``."""
    page = make_page(title, slug, template=template, publish=False, **kwargs)
    content = PageContent.admin_manager.get(page=page, language=LANGUAGE)
    return page, content, content.rescan_placeholders()


def plugin(placeholders: dict, slot: str, plugin_type: str, target=None, **data):
    return add_plugin(placeholders[slot], plugin_type, LANGUAGE, target=target, **data)


def publish(content, user):
    Version.objects.get_for_content(content).publish(user)
    return content


def png_bytes(size=(1200, 300), color="white") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def filer_image(name="logo.png", size=(1200, 300), alt="Opis obrazu"):
    from filer.models import Image

    upload = SimpleUploadedFile(name, png_bytes(size), content_type="image/png")
    return Image.objects.create(original_filename=name, file=upload, default_alt_text=alt, name=name)


def filer_file(name="regulamin.pdf", content=b"%PDF-1.4 test\n" * 100):
    from filer.models import File

    upload = SimpleUploadedFile(name, content, content_type="application/pdf")
    return File.objects.create(original_filename=name, file=upload, name="Regulamin (plik)")
