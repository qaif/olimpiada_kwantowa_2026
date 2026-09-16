"""Kwalifikacja ręczna: uzasadnienie obowiązkowe, decyzja bije próg, ślad w audycie i w tabeli.

Najważniejsze w tych testach jest to, czego **nie** wolno: decyzji bez uzasadnienia, decyzji,
która nie jest widoczna w ogłoszonej tabeli, i decyzji, która przechodzi po cichu do wyników już
opublikowanych.
"""

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.models import ManualQualification, QualificationMode, StageEntryStatus
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.results.manual import set_manual_qualification
from apps.results.models import Anonymization
from apps.results.services import apply_qualification, publish_results
from apps.results.simulation import simulate

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db

REASON = "Awaria łącza w trakcie rozmowy kwalifikacyjnej – decyzja komitetu z 12.05."


def test_uzasadnienie_jest_obowiazkowe():
    """Decyzja bez uzasadnienia nie jest decyzją, tylko przestawionym polem."""
    stage = make_stage(min_points=10)
    entry = graded_entry(stage, [0, 0])

    with pytest.raises(DomainError) as exc:
        set_manual_qualification(entry, ManualQualification.QUALIFIED, "bo tak", actor=None)

    assert exc.value.machine_code == "REASON_REQUIRED"
    entry.refresh_from_db()
    assert entry.manual_qualification == ManualQualification.NONE


def test_zdjecie_decyzji_nie_wymaga_uzasadnienia():
    """Powrót do reguły punktowej jest stanem opisanym progiem etapu, a nie wyjątkiem od niego."""
    stage = make_stage(min_points=10)
    entry = graded_entry(stage, [0, 0])
    set_manual_qualification(entry, ManualQualification.QUALIFIED, REASON, actor=None)

    result = set_manual_qualification(entry, "", "", actor=None)

    assert result["entry"].manual_qualification == ManualQualification.NONE
    assert result["entry"].manual_qualified_at is None


def test_decyzja_bije_prog_w_obie_strony():
    """Komitet może dopuścić mimo zera punktów i odmówić mimo kompletu."""
    stage = make_stage(min_points=6)
    promoted = graded_entry(stage, [0, 0])
    demoted = graded_entry(stage, [6, 6])
    set_manual_qualification(promoted, ManualQualification.QUALIFIED, REASON, actor=None)
    set_manual_qualification(demoted, ManualQualification.NOT_QUALIFIED, REASON, actor=None)

    apply_qualification(stage)

    promoted.refresh_from_db()
    demoted.refresh_from_db()
    assert promoted.status == StageEntryStatus.QUALIFIED
    assert demoted.status == StageEntryStatus.NOT_QUALIFIED


def test_dyskwalifikacji_decyzja_nie_podnosi():
    """Dyskwalifikacja jest osobną decyzją – wymaga cofnięcia, a nie obejścia drugą decyzją."""
    stage = make_stage(min_points=0)
    entry = graded_entry(stage, [6, 6], status=StageEntryStatus.DISQUALIFIED)
    set_manual_qualification(entry, ManualQualification.QUALIFIED, REASON, actor=None)

    summary = apply_qualification(stage)

    row = next(item for item in summary["rows"] if item["entry_id"] == entry.pk)
    assert row["qualified"] is False
    entry.refresh_from_db()
    assert entry.status == StageEntryStatus.DISQUALIFIED


def test_symulacja_pokazuje_ten_sam_sklad_co_przeliczenie():
    """Ekran, na którym dobiera się próg, nie może pokazywać innego składu niż późniejsze wyniki."""
    stage = make_stage(min_points=6)
    promoted = graded_entry(stage, [0, 0])
    set_manual_qualification(promoted, ManualQualification.QUALIFIED, REASON, actor=None)

    result = simulate(stage, QualificationMode.MIN_POINTS, 6, None)

    row = next(item for item in result["rows"] if item["entry_id"] == promoted.pk)
    assert row["qualified"] is True
    assert result["qualified"] == 1


def test_ogloszona_tabela_niesie_odznake_decyzji():
    """Wiersz, w którym punkty nie zgadzają się z progiem, musi się z tego wytłumaczyć."""
    stage = make_stage(min_points=6)
    promoted = graded_entry(stage, [0, 0])
    graded_entry(stage, [6, 6])
    set_manual_qualification(promoted, ManualQualification.QUALIFIED, REASON, actor=None)

    publication = publish_results(stage, None, Anonymization.CODE)

    by_total = {row["total"]: row for row in publication.rows}
    assert by_total[0]["manual"] is True
    assert by_total[0]["qualified"] is True
    assert by_total[12]["manual"] is False


def test_audyt_nie_zawiera_tresci_uzasadnienia():
    """Uzasadnienie bywa opisem zdarzenia z życia ucznia; audyt czytają osoby bez dostępu do nich."""
    stage = make_stage(min_points=6)
    entry = graded_entry(stage, [0, 0])
    coordinator = CoordinatorFactory()

    set_manual_qualification(entry, ManualQualification.QUALIFIED, REASON, actor=coordinator)

    log = AuditLog.objects.get(action="entry.manual_qualification")
    assert log.diff["after"] == ManualQualification.QUALIFIED
    assert log.diff["reason_length"] == len(REASON)
    assert REASON not in str(log.diff)


def test_decyzja_po_publikacji_zglasza_nieaktualnosc_wynikow():
    """Ogłoszona tabela jest dokumentem z chwili publikacji – zmienia ją dopiero ponowne ogłoszenie."""
    stage = make_stage(min_points=0)
    entry = graded_entry(stage, [6, 6])
    publish_results(stage, None, Anonymization.CODE)

    result = set_manual_qualification(entry, ManualQualification.NOT_QUALIFIED, REASON, actor=None)

    assert result["results_stale"] is True
