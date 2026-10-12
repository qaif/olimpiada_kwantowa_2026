"""Skan ClamAV mediów redakcyjnych w ``/cms/`` (audyt 10.10.2026, niskie; ``apps/cms/media_scan.py``).

Skaner jest tu podmieniany: testy sprawdzają **reakcję formularza** na werdykt, a nie clamd.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image as PILImage
from wagtail.documents import get_document_model
from wagtail.documents.forms import get_document_form
from wagtail.images import get_image_model
from wagtail.images.forms import get_image_form
from wagtail.models import Collection

from apps.cms import media_scan
from apps.submissions import antivirus

pytestmark = pytest.mark.django_db


def _scanner(monkeypatch, result=None, error=None):
    calls = []

    def fake_scan(fileobj, **kwargs):
        calls.append(fileobj.read())
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(antivirus, "scan_stream", fake_scan)
    return calls


def _document_form(name="ulotka.pdf", body=b"%PDF-1.4 ulotka"):
    Form = get_document_form(get_document_model())
    return Form(
        data={"title": "Ulotka", "collection": Collection.get_first_root_node().pk},
        files={"file": SimpleUploadedFile(name, body, content_type="application/pdf")},
    )


def _png() -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_the_wagtail_forms_are_built_on_the_scanning_bases():
    assert issubclass(get_document_form(get_document_model()), media_scan.ScannedDocumentForm)
    assert issubclass(get_image_form(get_image_model()), media_scan.ScannedImageForm)


def test_an_infected_document_is_rejected_with_a_readable_error(monkeypatch):
    _scanner(monkeypatch, result=(antivirus.VERDICT_INFECTED, "Eicar-Test-Signature"))

    form = _document_form()

    assert not form.is_valid()
    assert "Eicar-Test-Signature" in form.errors["file"][0]
    assert "antywirus" in form.errors["file"][0]


def test_a_clean_document_passes_and_its_bytes_are_intact(monkeypatch):
    calls = _scanner(monkeypatch, result=(antivirus.VERDICT_CLEAN, ""))

    form = _document_form()

    assert form.is_valid(), form.errors
    assert calls == [b"%PDF-1.4 ulotka"]
    document = form.save(commit=False)
    document.file.seek(0)
    assert document.file.read() == b"%PDF-1.4 ulotka"


def test_an_unavailable_scanner_does_not_block_the_editor(monkeypatch):
    _scanner(monkeypatch, error=antivirus.ClamAVUnavailable("brak clamd"))

    assert _document_form().is_valid()


def test_a_file_above_the_scanner_limit_is_rejected(monkeypatch):
    _scanner(monkeypatch, error=antivirus.ClamAVStreamTooLarge("za duży"))

    form = _document_form()

    assert not form.is_valid()
    assert "za duży" in form.errors["file"][0]


def test_an_infected_image_is_rejected(monkeypatch):
    _scanner(monkeypatch, result=(antivirus.VERDICT_INFECTED, "Win.Test.EICAR_HDB-1"))
    Form = get_image_form(get_image_model())

    form = Form(
        data={"title": "Plakat", "collection": Collection.get_first_root_node().pk},
        files={"file": SimpleUploadedFile("plakat.png", _png(), content_type="image/png")},
    )

    assert not form.is_valid()
    assert "Win.Test.EICAR_HDB-1" in form.errors["file"][0]


def test_the_scan_can_be_switched_off(monkeypatch, settings):
    settings.CMS_MEDIA_AV_SCAN = False
    calls = _scanner(monkeypatch, result=(antivirus.VERDICT_INFECTED, "x"))

    assert _document_form().is_valid()
    assert calls == []
