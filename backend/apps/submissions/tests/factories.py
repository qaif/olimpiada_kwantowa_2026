"""Fabryki i budowniczowie plików testowych dla rozwiązań. Używane wyłącznie w testach."""

import json

import factory
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.submissions.models import AvStatus, Submission, SubmissionFile, SubmissionStatus

# Minimalny, ale prawdziwy PDF: liczy się nagłówek %PDF- (walidator patrzy na treść, nie na nazwę).
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
ZIP_BYTES = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\xff\xfe\x00\x01" * 64
# Minimalny, ale prawdziwy JPEG: SOI + segment APP0/JFIF + EOI. Walidator sprawdza sygnaturę
# ``FF D8 FF`` i nie dekoduje obrazu, więc kilkadziesiąt bajtów wystarcza do odróżnienia zdjęcia
# od pliku, który tylko nazywa się ``.jpg``.
JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 32 + b"\xff\xd9"
)


def notebook_bytes(output_text: str = "wynik") -> bytes:
    """Poprawny notatnik nbformat 4 z jedną komórką kodu i jednym outputem tekstowym."""
    document = {
        "cells": [
            {
                "cell_type": "code",
                "id": "a1b2c3d4",
                "execution_count": 1,
                "metadata": {},
                "source": ["print('hej')\n"],
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": [output_text]},
                ],
            }
        ],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.12.0"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(document).encode("utf-8")


def legacy_notebook_bytes(output_text: str = "wynik") -> bytes:
    """Notatnik nbformat 3: komórki siedzą w ``worksheets[].cells[]``, nie w ``cells``.

    Taki układ omijał licznik outputów czytający surowe ``cells`` – stąd regresja na limit 2 MB.
    """
    document = {
        "metadata": {"name": "stary"},
        "nbformat": 3,
        "nbformat_minor": 0,
        "worksheets": [
            {
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "collapsed": False,
                        "input": ["print('hej')\n"],
                        "language": "python",
                        "metadata": {},
                        "prompt_number": 1,
                        "outputs": [
                            {"output_type": "stream", "stream": "stdout", "text": [output_text]},
                        ],
                    }
                ],
            }
        ],
    }
    return json.dumps(document).encode("utf-8")


def upload(name: str, content: bytes, content_type: str = "application/octet-stream") -> SimpleUploadedFile:
    """Plik w żądaniu. ``content_type`` celowo bywa kłamliwy – walidator ma go ignorować."""
    return SimpleUploadedFile(name, content, content_type=content_type)


def pdf_upload(name: str = "rozwiazanie.pdf") -> SimpleUploadedFile:
    return upload(name, PDF_BYTES, "application/pdf")


def jpeg_upload(name: str = "zdjecie.jpg") -> SimpleUploadedFile:
    return upload(name, JPEG_BYTES, "image/jpeg")


class SubmissionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Submission

    entry = factory.SubFactory(StageEntryFactory)
    problem = factory.SubFactory(ProblemFactory)
    version = 1
    submitted_at = factory.LazyFunction(timezone.now)
    is_late = False
    status = SubmissionStatus.SUBMITTED


class SubmissionFileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SubmissionFile

    submission = factory.SubFactory(SubmissionFactory)
    object_key = factory.Sequence(lambda n: f"1/1/OLM-TEST/{n:032d}/{'a' * 64}.pdf")
    sha256 = "a" * 64
    original_name = "rozwiazanie.pdf"
    mime = "application/pdf"
    size_bytes = len(PDF_BYTES)
    av_status = AvStatus.PENDING
