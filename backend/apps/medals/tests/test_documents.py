"""Dyplomy medalowe i zaświadczenia w języku ucznia (``apps.medals.documents``) – MED-01 § 2.

Dokument przechodzi przez **tę samą** drogę, co każdy dyplom (``results.certificates.render_pdf``):
rejestr, pieczęć, nazwa pliku. Sprawdzamy, że skład medali przejmuje wyłącznie swoje dokumenty,
że treść jest w języku ucznia (11 języków), że pismo bez kształtowania spada na angielski i że
Olimpiada Kwantowa składa zaświadczenie o udziale dokładnie jak przed MED-01.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.conf import settings
from pypdf import PdfReader

from apps.medals import documents, typesetting
from apps.medals.models import CertificateLanguage
from apps.results.certificates import issue_certificate, render_pdf, verify
from apps.results.models import CertificateKind
from apps.results.tests.conftest import graded_entry, make_stage

from .conftest import contestant, enable_medals, final_stage

pytestmark = pytest.mark.django_db

LANGUAGE_CODES = [code for code, _label in settings.LANGUAGES]


def medal_certificate(stage, kind=CertificateKind.MEDAL_GOLD, language=None):
    entry = contestant(stage, (6, 6), code="de")
    user = entry.participant.user
    user.first_name, user.last_name = "Łucja", "Śniadecka"
    user.save(update_fields=["first_name", "last_name"])
    certificate, _created = issue_certificate(edition=stage.edition, kind=kind, entry=entry)
    if language:
        CertificateLanguage.objects.create(certificate=certificate, language=language)
    return certificate


def pdf_text(data: bytes) -> str:
    return PdfReader(BytesIO(data)).pages[0].extract_text()


@pytest.mark.parametrize("language", LANGUAGE_CODES)
def test_medal_certificate_renders_in_every_interface_language(iqo, language):
    certificate = medal_certificate(final_stage(iqo), language=language)

    content = documents.localized_content(certificate)
    data = render_pdf(certificate)

    assert content.language == language
    assert content.rtl is (language == "ar")
    assert data.startswith(b"%PDF")
    assert content.recipient == "Łucja Śniadecka"
    assert "Germany" in content.school
    reader = PdfReader(BytesIO(data))
    assert reader.metadata.title.endswith(certificate.number)


def test_texts_follow_the_language_of_the_document(iqo):
    stage = final_stage(iqo)
    english = documents.localized_content(medal_certificate(stage, language="en"))
    spanish = documents.localized_content(
        medal_certificate(stage, kind=CertificateKind.HON_MENTION, language="es")
    )

    assert english.title == "Gold Medal"
    assert english.statement.startswith("for outstanding performance at the ")
    assert english.number_line.startswith("Document number: ")
    assert spanish.title == "Mención honorífica"
    assert "Código de verificación" in spanish.code_line


def test_latin_documents_have_extractable_text(iqo):
    data = render_pdf(medal_certificate(final_stage(iqo), language="en"))

    text = pdf_text(data)
    assert "Gold Medal" in text
    assert "Śniadecka" in text


def test_document_without_a_frozen_language_takes_the_students_current_one(iqo):
    from apps.accounts.models import UserPreference

    iqo.interface_languages = ["en", "ru"]
    iqo.default_language = "en"
    iqo.save(update_fields=["interface_languages", "default_language"])
    certificate = medal_certificate(final_stage(iqo))
    UserPreference.objects.create(user=certificate.entry.participant.user, language="ru")
    certificate.edition.competition.refresh_from_db()

    assert documents.certificate_language(certificate) == "ru"


def test_language_outside_the_competition_offer_falls_back_to_its_default(iqo):
    from apps.accounts.models import UserPreference

    iqo.interface_languages = ["en"]
    iqo.default_language = "en"
    iqo.save(update_fields=["interface_languages", "default_language"])
    certificate = medal_certificate(final_stage(iqo))
    UserPreference.objects.create(user=certificate.entry.participant.user, language="ar")
    certificate.edition.competition.refresh_from_db()

    assert documents.certificate_language(certificate) == "en"


def test_without_shaping_an_arabic_document_falls_back_to_english(iqo, monkeypatch):
    monkeypatch.setattr(typesetting, "shaping_available", lambda: False)
    certificate = medal_certificate(final_stage(iqo), language="ar")

    content = documents.localized_content(certificate)

    assert content.language == "en"
    assert content.title == "Gold Medal"
    assert render_pdf(certificate).startswith(b"%PDF")


def test_participation_certificate_is_localised_only_with_the_medals_flag(iqo):
    stage = final_stage(iqo)
    certificate = medal_certificate(stage, kind=CertificateKind.UCZESTNIK, language="fr")

    assert documents.handles(certificate) is True
    assert documents.localized_content(certificate).title == "Attestation de participation"


def test_kwantowa_participation_certificate_keeps_the_old_composer(competition):
    """Olimpiada Kwantowa: ten sam skład, ten sam tytuł i zdanie, co przed MED-01."""
    stage = make_stage()
    entry = graded_entry(stage, (5, 5))
    certificate, _created = issue_certificate(
        edition=stage.edition, kind=CertificateKind.UCZESTNIK, entry=entry
    )

    assert documents.handles(certificate) is False
    text = pdf_text(render_pdf(certificate))
    assert "Zaświadczenie o udziale" in text
    assert "OLIMPIADA KWANTOWA" in text


def test_medal_kinds_are_always_composed_by_the_medals_app(competition):
    enable_medals(competition)
    stage = make_stage()
    entry = graded_entry(stage, (6, 6))
    certificate, _created = issue_certificate(
        edition=stage.edition, kind=CertificateKind.MEDAL_BRONZE, entry=entry
    )

    assert documents.handles(certificate) is True


def test_verification_page_names_the_medal(iqo):
    certificate = medal_certificate(final_stage(iqo))

    result = verify(certificate.code)

    assert str(result["title"]) == "Złoty medal"
    assert result["recipient"] is None  # bez zgody na publikację nazwiska


def test_organiser_text_from_a_graphic_template_wins_over_the_competition_name(iqo):
    from apps.results.tests.factories import CertificateTemplateFactory

    stage = final_stage(iqo)
    layout = {"organiser": {"y": 90, "size": 22, "bold": True, "text": "IQO 2026 – WARSAW"}}
    CertificateTemplateFactory(competition=iqo, kind=CertificateKind.MEDAL_GOLD, layout=layout)
    certificate = medal_certificate(stage, language="en")

    assert "IQO 2026 – WARSAW" in pdf_text(render_pdf(certificate))


def test_the_default_layout_header_is_replaced_by_the_competition_name(iqo):
    iqo.short_name = "International Quantum Olympiad"
    iqo.save(update_fields=["short_name"])
    certificate = medal_certificate(final_stage(iqo), language="en")
    certificate.edition.competition.refresh_from_db()

    text = pdf_text(render_pdf(certificate))

    assert "INTERNATIONAL QUANTUM OLYMPIAD" in text
    assert "OLIMPIADA KWANTOWA" not in text
