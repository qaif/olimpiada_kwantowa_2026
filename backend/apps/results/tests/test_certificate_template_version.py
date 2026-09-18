"""Wersja tekstu dokumentu zapamiętana przy wystawieniu (§ 1.1.3).

PDF-a nikt nie przechowuje – powstaje przy każdym pobraniu. Bez zapamiętanej wersji poprawka
zdania w panelu zmieniałaby treść dokumentu, który ktoś trzyma w ręku, i nie byłoby tego widać
nigdzie: numer, kod i pieczęć zostałyby te same. Dlatego ``Certificate.template_version`` jest
kopią napisu, a nie kluczem obcym, i dlatego składa się nim dokument, a nie wersją obowiązującą.

Testy idą dwoma torami, bo taka jest reguła etapu 2 § 0.1: **bez flagi** ``document_templates``
wszystko ma wyglądać dokładnie jak przed wydaniem (napisy ze stałych, pusta wersja, zero zapytań
do tabeli szablonów), **z flagą** – tekstem z konfiguracji konkursu.
"""

from io import BytesIO

import pytest
from pypdf import PdfReader

from apps.results.certificates import (
    DOCUMENT_STATEMENTS,
    DOCUMENT_TITLES,
    PDF_AUTHOR,
    SIGNATURE_LINE,
    certificate_content,
    compose_pdf,
    issue_certificate,
)
from apps.results.models import CertificateKind
from apps.tenancy.documents import DocumentKind, set_current_template

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db


@pytest.fixture
def stage():
    return make_stage(problems=2)


def enable_templates(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "document_templates": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def laureate(stage, **kwargs):
    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.LAUREAT, entry=graded_entry(stage, [6, 6], **kwargs)
    )
    return certificate


# --- bez flagi: dokument Olimpiady Kwantowej bez zmiany -------------------------------------------


def test_bez_flagi_wersja_jest_pusta_a_napisy_sa_dzisiejsze(competition, stage):
    """Pusta wersja znaczy „układ wbudowany”, czyli dokładnie stałe ``certificates``."""
    assert stage.edition.competition_id == competition.pk
    certificate = laureate(stage)

    assert certificate.template_version == ""

    content = certificate_content(certificate)
    assert content.title == DOCUMENT_TITLES[CertificateKind.LAUREAT]
    assert content.statement == DOCUMENT_STATEMENTS[CertificateKind.LAUREAT]
    assert content.signature_line == SIGNATURE_LINE
    assert content.author == PDF_AUTHOR
    assert content.template_version == ""


def test_bez_flagi_pdf_ma_dzisiejszego_autora_i_linie_podpisu(competition, stage):
    """Sprawdzamy plik, a nie stałą, z której powstał – to plik krąży poza systemem."""
    content = certificate_content(laureate(stage))

    reader = PdfReader(BytesIO(compose_pdf(content)))

    assert reader.metadata.author == PDF_AUTHOR
    assert SIGNATURE_LINE in reader.pages[0].extract_text()


def test_bez_flagi_wystawienie_nie_pyta_o_szablony(competition, stage, django_assert_num_queries):
    """Liczba zapytań przy „Wystaw” nie rośnie o tabelę, której nikt jeszcze nie włączył."""
    entry = graded_entry(stage, [6, 6])
    with django_assert_num_queries(0):
        from apps.tenancy.documents import current_version

        assert current_version(stage.edition.competition, CertificateKind.LAUREAT) == ""

    certificate, created = issue_certificate(edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry)
    assert created is True
    assert certificate.template_version == ""


# --- z flagą: tekst z konfiguracji konkursu -------------------------------------------------------


def test_z_flaga_wersja_trafia_do_rejestru_a_tekst_na_papier(competition, stage):
    """Wystawienie kopiuje wersję obowiązującą, a dokument składa się jej tekstem."""
    enable_templates(competition)
    set_current_template(
        competition,
        DocumentKind.LAUREAT,
        version="2.0",
        title="Dyplom laureata {competition}",
        statement="{recipient} został(a) laureatem edycji {edition}",
        signature_line="Przewodnicząca komitetu",
    )

    certificate = laureate(stage)

    assert certificate.template_version == "2.0"
    content = certificate_content(certificate)
    assert content.title == f"Dyplom laureata {competition.short_name or competition.name}"
    assert content.statement == f"{content.recipient} został(a) laureatem edycji {content.edition}"
    assert content.signature_line == "Przewodnicząca komitetu"


def test_dokument_wystawiony_wczoraj_nie_zmienia_tresci_po_poprawce(competition, stage):
    """Najważniejszy test tego pola: poprawka tekstu **nie dotyczy** dokumentów już wydanych."""
    enable_templates(competition)
    set_current_template(
        competition,
        DocumentKind.LAUREAT,
        version="2.0",
        title="Tytuł z wersji 2.0",
        statement="zdanie z wersji 2.0",
    )
    certificate = laureate(stage)
    assert certificate.template_version == "2.0"

    set_current_template(
        competition,
        DocumentKind.LAUREAT,
        version="3.0",
        title="Tytuł z wersji 3.0",
        statement="zdanie z wersji 3.0",
    )

    content = certificate_content(certificate)
    assert content.title == "Tytuł z wersji 2.0"
    assert content.statement == "zdanie z wersji 2.0"
    assert content.template_version == "2.0"


def test_dokument_sprzed_wlaczenia_flagi_zostaje_przy_stalych(competition, stage):
    """Pusta wersja zostaje pusta także po włączeniu flagi – dyplom z zeszłego roku się nie zmienia."""
    certificate = laureate(stage)
    assert certificate.template_version == ""

    enable_templates(competition)
    set_current_template(
        competition,
        DocumentKind.LAUREAT,
        version="2.0",
        title="Tytuł z wersji 2.0",
        statement="zdanie z wersji 2.0",
    )

    content = certificate_content(certificate)
    assert content.title == DOCUMENT_TITLES[CertificateKind.LAUREAT]
    assert content.statement == DOCUMENT_STATEMENTS[CertificateKind.LAUREAT]


def test_zaswiadczenie_opiekuna_nie_ma_kodu_uczestnika(competition, stage):
    """Znacznik bez wartości w tym dokumencie zostawia puste miejsce, a nie klamrę na papierze."""
    from apps.accounts.models import SchoolSupervisor
    from apps.accounts.tests.factories import UserFactory

    enable_templates(competition)
    set_current_template(
        competition,
        DocumentKind.OPIEKUN,
        version="2.0",
        title="Zaświadczenie",
        statement="opiekun ucznia {participant_code} w edycji {edition}",
    )
    supervisor = SchoolSupervisor.objects.create(
        user=UserFactory(first_name="Jan", last_name="Nowak"),
        school="I LO",
        competition=stage.edition.competition,
    )
    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.OPIEKUN, supervisor=supervisor
    )

    content = certificate_content(certificate)

    assert content.statement == f"opiekun ucznia  w edycji {content.edition}"
