"""Pieczęć elektroniczna dokumentów: podpis PAdES, brak konfiguracji i awaria podpisu.

Najważniejsza reguła tego modułu nie dotyczy kryptografii, tylko **dostępności dokumentu**: żadna
awaria podpisu nie może zabrać uczestnikowi dyplomu. Bez klucza, z kluczem uszkodzonym i ze złym
hasłem dokument ma wyjść tak samo – tyle że bez pieczęci i z wpisem w logu.

Certyfikat robimy w teście, samopodpisany i jednorazowy (``cryptography`` jest w zależnościach,
bo ciągnie ją Django/allauth). Klucz pieczęci organizacji nie ma prawa istnieć w repozytorium
ani w środowisku testowym, a to, co sprawdzamy, nie zależy od tego, kto certyfikat wystawił:
że w pliku PDF pojawia się podpis, że czytnik go znajduje i że z podmiotu certyfikatu bierze się
nazwa pieczętującego.
"""

from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest

from apps.results.certificates import compose_pdf, issue_certificate, render_pdf, sample_content
from apps.results.models import CertificateKind
from apps.results.signing import sign_document, sign_pdf

from .conftest import make_stage
from .test_certificates import entry_with_name

#: Bez ``pyhanko`` cały moduł nie ma czego sprawdzać. Pomijamy go, zamiast go wywracać: obraz
#: budowany bez tej zależności ma nadal składać dyplomy, a to sprawdzają pozostałe testy.
pyhanko = pytest.importorskip("pyhanko", reason="pyhanko nie jest zainstalowane w tym obrazie")

ORGANISATION = "Olimpiada Kwantowa"
PASSWORD = "haslo-do-pieczeci"


def make_p12(path, *, password: str = PASSWORD) -> bytes:
    """Jednorazowy, samopodpisany PKCS#12 – imitacja pliku pieczęci od dostawcy zaufania."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Pieczec testowa"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANISATION),
        ]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    blob = pkcs12.serialize_key_and_certificates(
        b"pieczec", key, certificate, None, BestAvailableEncryption(password.encode())
    )
    path.write_bytes(blob)
    return blob


@pytest.fixture
def configured_seal(settings, tmp_path):
    """Ustawienia z działającą pieczęcią. Plik żyje tyle, co katalog tymczasowy testu."""
    path = tmp_path / "pieczec.p12"
    make_p12(path)
    settings.CERT_SIGN_P12_PATH = str(path)
    settings.CERT_SIGN_P12_PASSWORD = PASSWORD
    settings.CERT_SIGN_TSA_URL = ""
    return path


def sample_pdf() -> bytes:
    """Dokument do podpisania. Ten sam skład, co dyplom – bez bazy, bo podpis jej nie potrzebuje."""
    return compose_pdf(sample_content(CertificateKind.LAUREAT))


def embedded_signatures(data: bytes) -> list:
    """Podpisy, które w tym pliku widzi **czytnik** ``pyhanko`` – nie nasz własny kod."""
    from pyhanko.pdf_utils.reader import PdfFileReader

    return list(PdfFileReader(BytesIO(data)).embedded_signatures)


def test_bez_konfiguracji_dokument_wychodzi_bez_zmian(settings):
    """Stan domyślny serwisu: klucza nie ma, dokument jest, pieczęci nie ma."""
    settings.CERT_SIGN_P12_PATH = ""
    raw = sample_pdf()

    result = sign_document(raw)

    assert result.signed is False
    assert result.data == raw
    assert sign_pdf(raw) == raw


def test_podpisany_dokument_niesie_podpis_widoczny_dla_czytnika(configured_seal):
    """To jest cała obietnica pieczęci: czytnik PDF-a ma znaleźć w pliku podpis."""
    signed = sign_document(sample_pdf())

    assert signed.signed is True
    assert signed.data.startswith(b"%PDF-")
    assert len(embedded_signatures(signed.data)) == 1


def test_nazwa_pieczetujacego_bierze_sie_z_podmiotu_certyfikatu(configured_seal):
    """Pieczęć należy do podmiotu, nie do osoby – na stronie weryfikacji stoi nazwa organizacji."""
    assert sign_document(sample_pdf()).signer_name == ORGANISATION


def test_zle_haslo_do_klucza_nie_zabiera_dokumentu(configured_seal, settings):
    """Pomyłka w konfiguracji nie może zatrzymać wydawania dyplomów w dniu gali."""
    settings.CERT_SIGN_P12_PASSWORD = "nie-to-haslo"
    raw = sample_pdf()

    result = sign_document(raw)

    assert result.signed is False
    assert result.data == raw


def test_brak_pliku_klucza_nie_zabiera_dokumentu(settings, tmp_path):
    """Wolumen bez zamontowanego pliku to ta sama sytuacja, co brak konfiguracji – tyle że w logu."""
    settings.CERT_SIGN_P12_PATH = str(tmp_path / "nie-ma-takiego-pliku.p12")
    settings.CERT_SIGN_P12_PASSWORD = PASSWORD
    raw = sample_pdf()

    assert sign_document(raw).signed is False


@pytest.mark.django_db
def test_stan_pieczeci_zapisuje_sie_przy_dokumencie(configured_seal):
    """Strona weryfikacji ma mówić prawdę o tym, co odbiorca trzyma w ręku."""
    stage = make_stage(problems=1)
    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry_with_name(stage)
    )

    assert certificate.signed is False
    render_pdf(certificate)

    certificate.refresh_from_db()
    assert certificate.signed is True
    assert certificate.signer_name == ORGANISATION
    assert certificate.signed_at is not None


@pytest.mark.django_db
def test_wylaczenie_pieczeci_zdejmuje_flage_z_dokumentu(configured_seal, settings):
    """Wygasły certyfikat albo zabrany klucz znaczy „od teraz bez pieczęci”, a nie „kiedyś była”."""
    stage = make_stage(problems=1)
    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry_with_name(stage)
    )
    render_pdf(certificate)

    settings.CERT_SIGN_P12_PATH = ""
    render_pdf(certificate)

    certificate.refresh_from_db()
    assert certificate.signed is False
    assert certificate.signed_at is None
