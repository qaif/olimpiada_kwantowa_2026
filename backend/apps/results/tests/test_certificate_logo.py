"""Logo olimpiady na dyplomie.

Znak staje w lewym górnym rogu każdego dokumentu (``DEFAULT_LAYOUT["logo"]``). Pierwszeństwo ma
logo szablonu graficznego; bez niego – znak olimpiady z treści dokumentu (``competition_logo``).
``show: false`` w układzie gasi oba, np. gdy znak jest już częścią tła.
"""

import io
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from apps.results.certificate_layout import default_certificate_layout
from apps.results.certificates import (
    _default_logo,
    competition_logo,
    compose_pdf,
    sample_content,
)
from apps.results.models import CertificateKind

from .factories import CertificateTemplateFactory


def images_on_first_page(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages[0].images)


def test_the_default_logo_is_the_olympiad_mark_from_the_repository():
    data = _default_logo()

    assert data is not None
    assert data.startswith(b"\x89PNG")


@pytest.mark.parametrize("kind", CertificateKind.values)
def test_every_kind_of_document_carries_the_logo(kind):
    content = sample_content(kind)

    assert content.logo == _default_logo()
    assert images_on_first_page(compose_pdf(content)) == 1


def test_a_document_without_a_logo_has_no_picture():
    content = replace(sample_content(CertificateKind.LAUREAT), logo=None)

    assert images_on_first_page(compose_pdf(content)) == 0


@pytest.mark.django_db
def test_the_layout_can_switch_the_logo_off():
    layout = default_certificate_layout()
    layout["logo"]["show"] = False
    template = CertificateTemplateFactory(layout=layout)

    pdf = compose_pdf(sample_content(CertificateKind.LAUREAT), template)

    assert images_on_first_page(pdf) == 0


@pytest.mark.django_db
def test_a_template_without_its_own_logo_shows_the_olympiad_mark():
    template = CertificateTemplateFactory(layout=default_certificate_layout())

    pdf = compose_pdf(sample_content(CertificateKind.LAUREAT), template)

    assert images_on_first_page(pdf) == 1


def test_a_competition_with_its_own_brand_does_not_borrow_the_olympiad_mark():
    """Konkurs z własną marką i bez logotypu dostaje dokument bez znaku, a nie z cudzym."""
    competition = SimpleNamespace(logo=None, has_feature=lambda name: name == "competition_branding_in_mail")

    assert competition_logo(competition) is None


def test_a_competition_without_its_own_brand_keeps_the_olympiad_mark():
    competition = SimpleNamespace(logo=None, has_feature=lambda name: False)

    assert competition_logo(competition) == _default_logo()
    assert competition_logo(None) == _default_logo()
