"""T-03, kryteria 5-6: serwis `create_stage` i rejestracja do etapu."""

from datetime import UTC, date, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from freezegun import freeze_time

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import (
    Edition,
    QualificationMode,
    QualificationRule,
    ScoringScale,
    Stage,
    StageEntry,
    StageEntryStatus,
    StageKind,
)
from apps.competitions.services import create_stage, current_stage, register_for_stage, update_stage
from apps.core.api import DomainError
from apps.core.models import AuditLog

from .factories import CurrentEditionFactory, EditionFactory, StageFactory

OPENS_AT = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
DEADLINE_AT = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def _elim_stage(**kwargs):
    # Rejestracja wymaga bieżącej edycji – reużywamy istniejącą, żeby nie złamać "jedna bieżąca".
    kwargs.setdefault("edition", Edition.objects.filter(is_current=True).first() or CurrentEditionFactory())
    return StageFactory(kind=StageKind.ELIM, opens_at=OPENS_AT, deadline_at=DEADLINE_AT, **kwargs)


@pytest.mark.django_db
def test_kryterium_5_create_stage_tworzy_domyslna_skale_i_regule():
    """5. `create_stage` tworzy domyślną skalę 0/2/5/6 i regułę MIN_POINTS(min_points=0)."""
    edition = EditionFactory()

    stage = create_stage(
        edition=edition,
        kind=StageKind.ELIM,
        opens_at=OPENS_AT,
        deadline_at=DEADLINE_AT,
        review_deadline_at=DEADLINE_AT + timedelta(days=14),
        appeal_window_opens_at=DEADLINE_AT + timedelta(days=16),
        appeal_window_closes_at=DEADLINE_AT + timedelta(days=23),
    )

    assert ScoringScale.objects.filter(stage=stage).count() == 1
    assert QualificationRule.objects.filter(stage=stage).count() == 1
    assert stage.scoring_scale.allowed_values() == {0, 2, 5, 6}
    assert stage.scoring_scale.max_value == 6
    assert stage.qualification_rule.mode == QualificationMode.MIN_POINTS
    assert stage.qualification_rule.min_points == 0
    assert stage.qualification_rule.top_n is None


@pytest.mark.django_db
def test_kryterium_5_create_stage_przyjmuje_wlasna_skale_i_prog():
    """5. Parametry etapu (skala, próg) są konfigurowalne – domyślne wartości to tylko default."""
    stage = create_stage(
        edition=EditionFactory(),
        kind=StageKind.FINAL,
        opens_at=OPENS_AT,
        deadline_at=DEADLINE_AT,
        review_deadline_at=DEADLINE_AT + timedelta(days=14),
        appeal_window_opens_at=DEADLINE_AT + timedelta(days=16),
        appeal_window_closes_at=DEADLINE_AT + timedelta(days=23),
        scoring_values=[{"value": 0, "label": "brak"}, {"value": 3, "label": "pełne"}],
        max_value=3,
        qualification_mode=QualificationMode.TOP_N,
        min_points=None,
        top_n=40,
    )

    assert stage.scoring_scale.allowed_values() == {0, 3}
    assert stage.qualification_rule.top_n == 40


@pytest.mark.django_db
def test_kryterium_5_create_stage_z_bledna_osia_czasu_nic_nie_zapisuje():
    """5. Serwis waliduje przez `full_clean()` i jest atomowy – nie zostaje osierocona skala."""
    with pytest.raises(ValidationError):
        create_stage(
            edition=EditionFactory(),
            kind=StageKind.ELIM,
            opens_at=DEADLINE_AT,
            deadline_at=OPENS_AT,
            review_deadline_at=DEADLINE_AT + timedelta(days=14),
            appeal_window_opens_at=DEADLINE_AT + timedelta(days=16),
            appeal_window_closes_at=DEADLINE_AT + timedelta(days=23),
        )

    assert ScoringScale.objects.count() == 0
    assert QualificationRule.objects.count() == 0


@pytest.mark.django_db
@freeze_time("2026-09-30 09:00:00")
def test_kryterium_6_rejestracja_przed_otwarciem_daje_403_registration_closed():
    """6. `register_for_stage` przed `opens_at` → 403 REGISTRATION_CLOSED."""
    stage = _elim_stage()

    with pytest.raises(DomainError) as exc:
        register_for_stage(ParticipantFactory(), stage)

    assert exc.value.machine_code == "REGISTRATION_CLOSED"
    assert exc.value.status_code == 403
    assert StageEntry.objects.count() == 0


@pytest.mark.django_db
@freeze_time("2026-10-16 09:00:00")
def test_kryterium_6_rejestracja_po_deadline_daje_403():
    """6. `register_for_stage` po `deadline_at + grace` → 403 REGISTRATION_CLOSED."""
    stage = _elim_stage(grace_seconds=600)

    with pytest.raises(DomainError) as exc:
        register_for_stage(ParticipantFactory(), stage)

    assert exc.value.machine_code == "REGISTRATION_CLOSED"
    assert exc.value.status_code == 403


@pytest.mark.django_db
@freeze_time("2026-10-05 09:00:00")
def test_kryterium_6_rejestracja_w_oknie_tworzy_wpis_registered():
    """6. W oknie zgłoszeń powstaje `StageEntry` ze statusem REGISTERED."""
    stage = _elim_stage()
    participant = ParticipantFactory()

    entry = register_for_stage(participant, stage)

    assert entry.status == StageEntryStatus.REGISTERED
    assert entry.participant_id == participant.id
    assert entry.stage_id == stage.id
    assert entry.total_points is None


@pytest.mark.django_db
@freeze_time("2026-10-15 10:00:05")
def test_kryterium_6_grace_seconds_przedluza_okno_rejestracji():
    """6. Deadline jest liczony jako `deadline_at + grace_seconds`, po stronie serwera."""
    # Jeden etap ELIM w bieżącej edycji: najpierw bez tolerancji (zamknięty), potem z tolerancją (otwarty).
    stage = _elim_stage(grace_seconds=0)

    with pytest.raises(DomainError) as exc:
        register_for_stage(ParticipantFactory(), stage)
    assert exc.value.machine_code == "REGISTRATION_CLOSED"

    Stage.objects.filter(pk=stage.pk).update(grace_seconds=600)
    stage.refresh_from_db()
    assert register_for_stage(ParticipantFactory(), stage).pk is not None


@pytest.mark.django_db
@freeze_time("2026-10-05 09:00:00")
def test_kryterium_6_etap_okregowy_nie_przyjmuje_recznej_rejestracji():
    """6. Dla DISTRICT/FINAL wpisy tworzy kwalifikacja → STAGE_NOT_OPEN_FOR_REGISTRATION."""
    for kind in (StageKind.DISTRICT, StageKind.FINAL):
        stage = StageFactory(kind=kind, opens_at=OPENS_AT, deadline_at=DEADLINE_AT)

        with pytest.raises(DomainError) as exc:
            register_for_stage(ParticipantFactory(), stage)

        assert exc.value.machine_code == "STAGE_NOT_OPEN_FOR_REGISTRATION"
        assert exc.value.status_code == 403
    assert StageEntry.objects.count() == 0


@pytest.mark.django_db
@freeze_time("2026-10-05 09:00:00")
def test_kryterium_6_powtorna_rejestracja_serwisu_daje_already_registered():
    """6/7. Druga rejestracja tego samego uczestnika → ALREADY_REGISTERED (409)."""
    stage = _elim_stage()
    participant = ParticipantFactory()
    register_for_stage(participant, stage)

    with pytest.raises(DomainError) as exc:
        register_for_stage(participant, stage)

    assert exc.value.machine_code == "ALREADY_REGISTERED"
    assert exc.value.status_code == 409
    assert StageEntry.objects.filter(participant=participant, stage=stage).count() == 1


@pytest.mark.django_db
@freeze_time("2026-10-05 09:00:00")
def test_current_stage_wybiera_etap_otwarty_a_potem_najblizszy_przyszly():
    """Etap „bieżący” wynika z terminów, nie z rodzaju etapu."""
    edition = CurrentEditionFactory()
    elim = StageFactory(edition=edition, kind=StageKind.ELIM, opens_at=OPENS_AT, deadline_at=DEADLINE_AT)
    district = StageFactory(
        edition=edition,
        kind=StageKind.DISTRICT,
        opens_at=OPENS_AT + timedelta(days=60),
        deadline_at=DEADLINE_AT + timedelta(days=60),
    )

    assert current_stage(edition) == elim
    with freeze_time("2026-11-01 09:00:00"):
        assert current_stage(edition) == district
    with freeze_time("2030-01-01 09:00:00"):
        assert current_stage(edition) == district


# --- dni wydarzenia: create_stage i update_stage ------------------------------------------------


@pytest.mark.django_db
def test_create_stage_zapisuje_dni_wydarzenia_obok_okna_oddawania_prac():
    """Etap stacjonarny powstaje z dwiema parami dat naraz – zjazd i sesja egzaminacyjna."""
    stage = create_stage(
        edition=EditionFactory(),
        kind=StageKind.FINAL,
        location="Kraków",
        opens_at=datetime(2027, 6, 5, 7, 0, tzinfo=UTC),
        deadline_at=datetime(2027, 6, 5, 12, 0, tzinfo=UTC),
        event_starts_on=date(2027, 6, 4),
        event_ends_on=date(2027, 6, 7),
        review_deadline_at=datetime(2027, 6, 20, 12, 0, tzinfo=UTC),
        appeal_window_opens_at=datetime(2027, 6, 22, 12, 0, tzinfo=UTC),
        appeal_window_closes_at=datetime(2027, 6, 29, 12, 0, tzinfo=UTC),
    )

    stage.refresh_from_db()
    assert stage.event_range == (date(2027, 6, 4), date(2027, 6, 7))
    # Okno uploadu zostaje takie, jakie podał koordynator – dni zjazdu go nie rozciągają.
    assert stage.deadline_at == datetime(2027, 6, 5, 12, 0, tzinfo=UTC)


@pytest.mark.django_db
def test_update_stage_zapisuje_dni_wydarzenia_i_wpisuje_je_do_audytu():
    """Zmiana terminu zjazdu zostawia ślad w audycie – z datami w ISO, jak reszta pól.

    ``diff`` jest JSON-em, więc same obiekty ``date`` wysadziłyby zapis wpisu. Test pilnuje
    zarazem zapisu i tego, że różnica jest czytelna dla człowieka czytającego audyt.
    """
    stage = StageFactory(kind=StageKind.FINAL, location="Kraków")

    update_stage(stage, None, event_starts_on=date(2027, 6, 4), event_ends_on=date(2027, 6, 7))

    stage.refresh_from_db()
    assert stage.event_range == (date(2027, 6, 4), date(2027, 6, 7))
    entry = AuditLog.objects.filter(action="stage.updated", target_id=str(stage.pk)).latest("at")
    assert entry.diff["event_starts_on"] == {"from": None, "to": "2027-06-04"}
    assert entry.diff["event_ends_on"] == {"from": None, "to": "2027-06-07"}


@pytest.mark.django_db
def test_update_stage_odrzuca_polowe_terminu_wydarzenia():
    """Sam początek bez końca nie przechodzi także przez serwis – to ``full_clean()`` modelu."""
    stage = StageFactory(kind=StageKind.FINAL)

    with pytest.raises(ValidationError) as exc:
        update_stage(stage, None, event_starts_on=date(2027, 6, 4))

    assert "event_ends_on" in exc.value.message_dict
    stage.refresh_from_db()
    assert stage.event_range is None
