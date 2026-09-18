"""Stopka dyplomu nie może wchodzić pod kod QR.

W domyślnym układzie kod QR (bok 74 pt od ``y=455``) sięga 4 pt poniżej pierwszego wiersza stopki
(``y=525``) i zasłaniał końcówkę napisu „Data wystawienia”. Prawa kolumna stopki cofa się więc przed
kodem – ale tylko wtedy, gdy oba prostokąty naprawdę na siebie zachodzą, żeby szablon z przesuniętym
kodem zachował wyrównanie do marginesu.
"""

import io
from dataclasses import replace

from pypdf import PdfReader

from apps.results.certificate_layout import PAGE_HEIGHT, PAGE_WIDTH, default_certificate_layout
from apps.results.certificates import (
    QR_FOOTER_GAP,
    _footer_right_edge,
    compose_pdf,
    sample_content,
)
from apps.results.models import CertificateKind

FOOTER_TOP = PAGE_HEIGHT - 525
FOOTER_SIZE = 9
MARGIN_EDGE = PAGE_WIDTH - 60


def _edge(content, layout) -> float:
    return _footer_right_edge(
        content, layout=layout, width=PAGE_WIDTH, height=PAGE_HEIGHT, top=FOOTER_TOP, size=FOOTER_SIZE
    )


def test_footer_steps_aside_for_the_default_qr_code():
    content = sample_content(CertificateKind.LAUREAT)
    assert content.verification_url

    layout = default_certificate_layout()

    assert _edge(content, layout) == layout["qr"]["x"] - QR_FOOTER_GAP


def test_footer_keeps_the_margin_without_a_qr_code():
    content = replace(sample_content(CertificateKind.LAUREAT), verification_url="")

    assert _edge(content, default_certificate_layout()) == MARGIN_EDGE


def test_footer_keeps_the_margin_when_the_template_hides_the_code():
    layout = default_certificate_layout()
    layout["qr"]["show"] = False

    assert _edge(sample_content(CertificateKind.LAUREAT), layout) == MARGIN_EDGE


def test_footer_keeps_the_margin_when_the_code_stands_clear_of_it():
    layout = default_certificate_layout()
    layout["qr"]["y"] = 300

    assert _edge(sample_content(CertificateKind.LAUREAT), layout) == MARGIN_EDGE


def test_the_composed_document_still_carries_the_whole_footer():
    text = PdfReader(io.BytesIO(compose_pdf(sample_content(CertificateKind.LAUREAT)))).pages[0].extract_text()

    assert "Data wystawienia:" in text
    assert "Weryfikacja: /dyplomy/<kod>/ w serwisie olimpiady" in text
