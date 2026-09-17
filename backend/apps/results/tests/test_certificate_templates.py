"""Szablony graficzne dokumentów: dopasowanie, scalanie układu i skład na cudzym tle.

Dwie rzeczy są tu ważniejsze od reszty. Po pierwsze **kolejność dopasowania**: szablon rodzaju
ma wygrywać z szablonem ogólnym, bo dyplom laureata ma prawo wyglądać inaczej niż zaświadczenie
o udziale. Po drugie **brak szablonu nie jest awarią**: bez ani jednego wiersza w tabeli dokumenty
składają się układem wbudowanym i tak ma zostać – to jest stan produkcji przez większość roku.
"""

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.competitions.tests.factories import EditionFactory
from apps.results.certificate_layout import DEFAULT_LAYOUT, block, default_certificate_layout
from apps.results.certificates import (
    compose_pdf,
    issue_certificate,
    make_default_template,
    render_pdf,
    resolve_template,
    sample_content,
)
from apps.results.models import CertificateKind, CertificateTemplate

from .conftest import make_stage
from .factories import CertificateTemplateFactory
from .test_certificates import entry_with_name

pytestmark = pytest.mark.django_db


def template(**kwargs) -> CertificateTemplate:
    """Szablon o podanym zakresie. Nazwa jest nieistotna dla dopasowania – liczy się para pól.

    Przez fabrykę, a nie ``objects.create``: od wydania D szablon ma wymaganą kolumnę konkursu,
    a fabryka bierze go z kontekstu testu (``backend/conftest.py``) – tak samo, jak każdy inny
    wiersz zakresowany.
    """
    kwargs.setdefault("name", "Szablon testowy")
    return CertificateTemplateFactory(**kwargs)


def png_bytes(color=(255, 255, 255)) -> bytes:
    """Najmniejszy sensowny obraz. Pillow jest w zależnościach (ciągnie go Wagtail)."""
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (40, 20), color).save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_background(width: float, height: float) -> bytes:
    """Jednostronicowy PDF o zadanym formacie – imitacja projektu z drukarni."""
    from reportlab.pdfgen import canvas as pdf_canvas

    buffer = BytesIO()
    canvas = pdf_canvas.Canvas(buffer, pagesize=(width, height))
    canvas.rect(10, 10, width - 20, height - 20)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


# --- dopasowanie ---------------------------------------------------------------------------------


def test_bez_szablonu_nie_ma_dopasowania_i_dokument_i_tak_powstaje():
    """Pusta tabela szablonów jest normalnym stanem serwisu, a nie brakiem konfiguracji."""
    stage = make_stage(problems=1)
    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry_with_name(stage)
    )

    assert resolve_template(certificate.kind, certificate.edition) is None
    assert render_pdf(certificate).startswith(b"%PDF-")


def test_szablon_rodzaju_wygrywa_z_ogolnym():
    """Dyplom laureata ma prawo wyglądać inaczej niż reszta dokumentów tej samej edycji."""
    edition = EditionFactory()
    general = template(kind="", edition=None)
    specific = template(kind=CertificateKind.LAUREAT, edition=None, name="Laureat")

    assert resolve_template(CertificateKind.LAUREAT, edition).pk == specific.pk
    assert resolve_template(CertificateKind.UCZESTNIK, edition).pk == general.pk


def test_szablon_edycji_wygrywa_z_szablonem_wszystkich_edycji():
    """Jubileuszowa winieta jednej edycji nie ma czekać na skasowanie szablonu ogólnego."""
    edition = EditionFactory()
    template(kind="", edition=None)
    jubilee = template(kind="", edition=edition, name="Jubileusz")

    assert resolve_template(CertificateKind.UCZESTNIK, edition).pk == jubilee.pk
    assert resolve_template(CertificateKind.UCZESTNIK, EditionFactory()).kind_label == "wszystkie rodzaje"


def test_wylaczony_szablon_nie_obowiazuje():
    """``is_active`` jest wyłącznikiem, a nie etykietą – wyłączony wiersz nie dotyka dokumentów."""
    edition = EditionFactory()
    template(kind="", edition=None, is_active=False)

    assert resolve_template(CertificateKind.UCZESTNIK, edition) is None


def test_ustaw_jako_domyslny_wylacza_pozostale_o_tym_samym_zakresie():
    """„Domyślny” nie jest osobnym polem: to czynność „włącz ten, wyłącz pasujące tak samo”."""
    edition = EditionFactory()
    old = template(kind=CertificateKind.LAUREAT, edition=edition, name="Stary")
    new = template(kind=CertificateKind.LAUREAT, edition=edition, name="Nowy", is_active=False)
    other = template(kind=CertificateKind.FINALISTA, edition=edition, name="Finalista")

    replaced = make_default_template(new)

    old.refresh_from_db()
    new.refresh_from_db()
    other.refresh_from_db()
    assert replaced == 1
    assert (new.is_active, old.is_active) == (True, False)
    # Inny rodzaj to inny zakres – jego szablon nie ma powodu zgasnąć.
    assert other.is_active is True
    assert resolve_template(CertificateKind.LAUREAT, edition).pk == new.pk


# --- układ ---------------------------------------------------------------------------------------


def test_uklad_szablonu_scala_sie_z_domyslnym():
    """Redaktor zapisuje to, co zmienia; reszta bloku bierze wartości domyślne."""
    merged = block({"recipient": {"y": 300}}, "recipient")

    assert merged["y"] == 300
    assert merged["size"] == DEFAULT_LAYOUT["recipient"]["size"]
    assert merged["bold"] is True


def test_nieznany_blok_ukladu_nie_psuje_znanych():
    """Szablon zapisany przy innej wersji układu ma nadal działać – i działa blok po bloku."""
    assert block({"cokolwiek": {"y": 1}}, "title")["y"] == DEFAULT_LAYOUT["title"]["y"]


def test_wartosc_domyslna_ukladu_jest_za_kazdym_razem_nowa():
    """Wspólny słownik znaczyłby, że przesunięcie napisu w jednym szablonie rusza wszystkie."""
    first = default_certificate_layout()
    first["title"]["y"] = 1

    assert default_certificate_layout()["title"]["y"] == DEFAULT_LAYOUT["title"]["y"]


# --- skład ---------------------------------------------------------------------------------------


def test_dokument_sklada_sie_na_tle_obrazkowym_z_logo_i_podpisami():
    """Tło, logo i trzy podpisy naraz – sprawdzamy, że skład nie wywraca się na żadnym z nich."""
    stage = make_stage(problems=1)
    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry_with_name(stage)
    )
    template(
        kind="",
        edition=None,
        background=SimpleUploadedFile("tlo.png", png_bytes(), content_type="image/png"),
        logo=SimpleUploadedFile("logo.png", png_bytes((0, 0, 0)), content_type="image/png"),
        signature_1_image=SimpleUploadedFile("podpis.png", png_bytes(), content_type="image/png"),
        signature_1_name="Jan Kowalski",
        signature_1_title="Przewodniczący",
        signature_2_name="Anna Nowak",
        signature_3_title="Partner",
    )

    raw = render_pdf(certificate)

    assert raw.startswith(b"%PDF-")
    assert len(raw) > 1000


def test_tlo_w_pdf_zachowuje_format_projektu():
    """Projekt ze spadami jest większy niż A4 – tekst ma trafić na **jego** stronę, nie obok."""
    stage = make_stage(problems=1)
    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.UCZESTNIK, entry=entry_with_name(stage)
    )
    template(
        kind="",
        edition=None,
        background=SimpleUploadedFile(
            "projekt.pdf", pdf_background(900, 640), content_type="application/pdf"
        ),
    )

    from pypdf import PdfReader

    page = PdfReader(BytesIO(render_pdf(certificate))).pages[0]

    assert round(float(page.mediabox.width)) == 900
    assert round(float(page.mediabox.height)) == 640


def test_brakujacy_plik_tla_nie_zabiera_dyplomu():
    """Plik zniknięty z bucketu daje dokument bez winiety, a nie błąd 500 w dniu gali."""
    stage = make_stage(problems=1)
    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.UCZESTNIK, entry=entry_with_name(stage)
    )
    saved = template(
        kind="",
        edition=None,
        background=SimpleUploadedFile("tlo.png", png_bytes(), content_type="image/png"),
    )
    saved.background.storage.delete(saved.background.name)

    assert render_pdf(certificate).startswith(b"%PDF-")


def test_podglad_nie_zuzywa_numeru_z_puli():
    """Podgląd bywa drukowany – nie może wyglądać na dokument, którego nikt nie wystawił."""
    edition = EditionFactory()
    saved = template(kind=CertificateKind.LAUREAT, edition=edition)

    content = sample_content(CertificateKind.LAUREAT, edition)
    raw = compose_pdf(content, saved)

    assert raw.startswith(b"%PDF-")
    assert content.number.endswith("/000")
    assert content.code == "PRZYKLADOWY1"
    from apps.results.models import Certificate

    assert Certificate.objects.count() == 0
