"""Dyplomy i zaświadczenia: numeracja, idempotencja, skład PDF-a i zakres publicznej weryfikacji.

Dwie rzeczy są tu ważniejsze od reszty. Po pierwsze **jeden numer na dokument**: uczestnik z dwoma
numerami na ten sam tytuł ma problem przy pierwszej rekrutacji, w której trzeba ten numer podać.
Po drugie **strona weryfikacji nie jest wyszukiwarką danych osobowych**: kod z papieru sprawdza
każdy, kto go przepisze, więc bez zgody na publikację nazwiska strona nie wydaje nazwiska.
"""

import zipfile

import pytest

from apps.accounts.consents import ConsentKind, ConsentSource
from apps.accounts.models import ConsentRecord, SchoolSupervisor
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.core.models import AuditLog
from apps.results.certificates import (
    build_certificates_zip,
    certificate_content,
    issue_certificate,
    pdf_filename,
    render_pdf,
    verify,
)
from apps.results.models import Certificate, CertificateKind

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db


@pytest.fixture
def stage():
    return make_stage(problems=2)


def entry_with_name(stage, first_name="Łucja", last_name="Śniadecka", **kwargs):
    """Wpis uczestnika z nazwiskiem wymagającym polskich znaków – to jest sedno doboru kroju."""
    participant = ParticipantFactory(user=UserFactory(first_name=first_name, last_name=last_name), **kwargs)
    return graded_entry(stage, [6, 6], participant=participant)


def test_numer_ma_ustalony_ksztalt_i_rosnie(stage):
    """``OK/<rok>/<kolejny>`` – tak numeruje się dokumenty w sekretariacie i tak są cytowane."""
    first, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry_with_name(stage)
    )
    second, _ = issue_certificate(
        edition=stage.edition,
        kind=CertificateKind.LAUREAT,
        entry=entry_with_name(stage, first_name="Jan", last_name="Kowalski"),
    )

    prefix, year, number = first.number.split("/")
    assert prefix == "OK"
    assert year.isdigit()
    assert int(second.number.rsplit("/", 1)[-1]) == int(number) + 1


def test_powtorne_wystawienie_oddaje_ten_sam_numer(stage):
    """Drugi dyplom na ten sam tytuł byłby kłopotem uczestnika, a nie udogodnieniem."""
    entry = entry_with_name(stage)

    first, created_first = issue_certificate(
        edition=stage.edition, kind=CertificateKind.FINALISTA, entry=entry
    )
    second, created_second = issue_certificate(
        edition=stage.edition, kind=CertificateKind.FINALISTA, entry=entry
    )

    assert created_first is True
    assert created_second is False
    assert first.pk == second.pk
    assert Certificate.objects.count() == 1


def test_dokument_musi_miec_dokladnie_jednego_odbiorce(stage):
    """Dokument bez adresata niczego nie poświadcza, a z dwoma – nie wiadomo, czyj jest."""
    from apps.core.api import DomainError

    with pytest.raises(DomainError) as exc:
        issue_certificate(edition=stage.edition, kind=CertificateKind.UCZESTNIK)

    assert exc.value.machine_code == "INVALID_RECIPIENT"


def test_audyt_nie_zawiera_nazwiska(stage):
    """Wpis audytowy niesie identyfikator odbiorcy i numer – nigdy danych osobowych."""
    entry = entry_with_name(stage)
    coordinator = CoordinatorFactory()

    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry, actor=coordinator
    )

    log = AuditLog.objects.get(action="certificate.issued")
    assert log.diff["entry_id"] == entry.pk
    assert log.diff["number"] == certificate.number
    assert "Śniadecka" not in str(log.diff)


def test_pdf_powstaje_i_niesie_tresc_dokumentu(stage):
    """Sprawdzamy nagłówek pliku i treść, którą składamy – renderowania kroju nie testujemy."""
    entry = entry_with_name(stage)
    certificate, _ = issue_certificate(edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry)

    content = certificate_content(certificate)
    raw = render_pdf(certificate)

    assert raw.startswith(b"%PDF-")
    assert len(raw) > 1000
    assert content.recipient == "Łucja Śniadecka"
    assert content.title == "Dyplom laureata"
    assert content.number == certificate.number
    assert pdf_filename(certificate) == f"dyplom-{certificate.number.replace('/', '-')}.pdf"


def test_paczka_zip_ma_po_jednym_pliku_na_dokument(stage):
    """„Wystaw wszystkim” oddaje komplet – nazwa pliku jest numerem, nigdy nazwiskiem."""
    certificates = [
        issue_certificate(
            edition=stage.edition,
            kind=CertificateKind.UCZESTNIK,
            entry=entry_with_name(stage, first_name=name, last_name="Testowy"),
        )[0]
        for name in ("Ala", "Ola")
    ]

    archive = build_certificates_zip(certificates)

    assert archive.count == 2
    with zipfile.ZipFile(archive.stream) as package:
        names = sorted(package.namelist())
        assert names == sorted(pdf_filename(item) for item in certificates)
        assert all("Testowy" not in name for name in names)


def test_weryfikacja_bez_zgody_nie_wydaje_nazwiska(stage):
    """Kod z papieru sprawdza każdy, kto go przepisze – nazwisko wymaga zgody na publikację."""
    entry = entry_with_name(stage)
    certificate, _ = issue_certificate(edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry)

    result = verify(certificate.code)

    assert result["valid"] is True
    assert result["number"] == certificate.number
    assert result["edition"] == stage.edition.year_label
    assert result["recipient"] is None


def test_weryfikacja_ze_zgoda_pokazuje_nazwisko(stage):
    """Zgoda jest tu podstawą – sprawdzamy dowód (``ConsentRecord``), nie samo pole profilu."""
    entry = entry_with_name(stage)
    participant = entry.participant
    participant.publish_full_name = True
    participant.save(update_fields=["publish_full_name"])
    ConsentRecord.objects.create(
        participant=participant, kind=ConsentKind.PUBLISH_NAME, source=ConsentSource.PANEL
    )
    certificate, _ = issue_certificate(edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry)

    assert verify(certificate.code)["recipient"] == "Łucja Śniadecka"


def test_wycofana_zgoda_znowu_chowa_nazwisko(stage):
    """Projekcja w profilu bywa nieaktualna; rozstrzyga **aktywny** dowód zgody."""
    from django.utils import timezone

    entry = entry_with_name(stage)
    participant = entry.participant
    participant.publish_full_name = True
    participant.save(update_fields=["publish_full_name"])
    ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentKind.PUBLISH_NAME,
        source=ConsentSource.PANEL,
        withdrawn_at=timezone.now(),
    )
    certificate, _ = issue_certificate(edition=stage.edition, kind=CertificateKind.LAUREAT, entry=entry)

    assert verify(certificate.code)["recipient"] is None


def test_nieznany_kod_nie_wywraca_strony():
    """Nieznany kod jest normalnym stanem strony publicznej, a nie awarią."""
    assert verify("NIEISTNIEJACY") is None
    assert verify("") is None


def test_zaswiadczenie_opiekuna_nigdy_nie_pokazuje_nazwiska(stage):
    """Opiekun nie przechodzi przez blok zgód uczestnika, a milczenie nie jest zgodą."""
    supervisor = SchoolSupervisor.objects.create(
        user=UserFactory(first_name="Anna", last_name="Nauczycielska"),
        school="XIV LO",
        competition=stage.edition.competition,
    )

    certificate, _ = issue_certificate(
        edition=stage.edition, kind=CertificateKind.OPIEKUN, supervisor=supervisor
    )

    assert certificate.is_for_supervisor
    assert verify(certificate.code)["recipient"] is None
    assert certificate_content(certificate).recipient == "Anna Nauczycielska"
