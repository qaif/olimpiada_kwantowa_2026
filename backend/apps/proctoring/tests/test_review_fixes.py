"""Poprawki po przeglądzie PROC-01: wyproszenia z pokoi (M1, M3), pokoje komisji (M6), retencja zdjęć
i uwag (L2, L3), późny start i powód pracy bez nadzoru w siatce, raporcie i CSV (H1, H3), limity (L5),
okno TZ-01 (M4)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.proctoring import services, windows
from apps.proctoring.models import ProctoringRecording, ProctoringSession, ProctorKind

from .conftest import make_student, ready_session
from .fake_livekit import MemoryStorage
from .test_flow import media_session, published_long_ago

pytestmark = pytest.mark.django_db


def removed(fake) -> list[dict]:
    return [payload for name, payload in fake.calls if name == "RemoveParticipant"]


def test_withdrawn_consent_kicks_student_and_stops_recording(
    proctoring_on, fake_livekit, config, stage, student
):
    session = ready_session(stage, student)
    ProctoringRecording.objects.create(
        session=session, egress_id="EG_7", track_sid="TR", storage_key="proctoring/k/r/p/x.webm"
    )
    fake_livekit.publish(config.room_name("m"), session.identity, "CAMERA")
    services.withdraw_consent(session, user=student.user)
    assert {"room": config.room_name("m"), "identity": session.identity} in removed(fake_livekit)
    assert fake_livekit.payload("StopEgress") == {"egress_id": "EG_7"}


def test_anonymisation_kicks_student(proctoring_on, fake_livekit, config, stage, student):
    from apps.accounts.profile import anonymise_account

    session = ready_session(stage, student)
    fake_livekit.publish(config.room_name("m"), session.identity, "CAMERA")
    anonymise_account(student.user)
    assert {"room": config.room_name("m"), "identity": session.identity} in removed(fake_livekit)


def test_unassigning_proctor_kicks_them_and_moves_students(
    proctoring_on, fake_livekit, config, stage, student, coordinator, reviewer
):
    assignment = services.add_assignment(stage, coordinator, reviewer, ProctorKind.COMMITTEE)
    session = ready_session(stage, student)
    services.reassign(stage, session.pk, assignment.pk, coordinator)
    room = config.room_name(f"a{assignment.pk}")
    proctor = services.proctor_identity(stage, reviewer)
    fake_livekit.publish(room, proctor)
    ProctoringSession.objects.filter(pk=session.pk).update(connected=True)
    fake_livekit.publish(room, session.identity, "CAMERA")
    services.remove_assignment(stage, coordinator, assignment.pk)
    assert {"room": room, "identity": proctor} in removed(fake_livekit)
    assert {"room": room, "identity": session.identity} in removed(fake_livekit)
    session.refresh_from_db()
    assert session.group == "m"


def test_distribute_moves_connected_students_out_of_the_old_room(
    proctoring_on, fake_livekit, config, stage, student, coordinator, reviewer
):
    session = ready_session(stage, student)
    ProctoringSession.objects.filter(pk=session.pk).update(connected=True)
    fake_livekit.publish(config.room_name("m"), session.identity, "CAMERA")
    assignment = services.add_assignment(stage, coordinator, reviewer, ProctorKind.COMMITTEE)
    services.distribute(stage, coordinator)
    session.refresh_from_db()
    assert session.group == f"a{assignment.pk}"
    assert {"room": config.room_name("m"), "identity": session.identity} in removed(fake_livekit)


def test_committee_assigned_delegation_student_goes_to_assignment_room(
    proctoring_on, fake_livekit, config, stage, student, coordinator, reviewer, delegations_map
):
    delegations_map.students[student.pk] = 7
    session = services.session_for(stage, student)
    assert session.group == "d7"
    assignment = services.add_assignment(stage, coordinator, reviewer, ProctorKind.COMMITTEE)
    services.reassign(stage, session.pk, assignment.pk, coordinator)
    session.refresh_from_db()
    # Członek komisji nie dostaje tokenu do pokoju całej delegacji – uczeń przechodzi do pokoju przydziału.
    assert session.group == f"a{assignment.pk}"
    assert services.proctor_scope(reviewer, stage).allowed_groups() == [f"a{assignment.pk}"]


def test_main_group_warning_on_coordinator_screen(
    proctoring_on, config, stage, coordinator, logged, monkeypatch
):
    monkeypatch.setattr(services, "MAIN_GROUP_WARNING", 0)
    make_student(stage.edition.competition, stage)
    services.ensure_sessions(stage)
    assert services.stage_summary(stage, config)["main_group_warning"] is True
    page = logged(coordinator).get(f"/coordinator/proctoring/{stage.pk}/").content.decode()
    assert "Rozdziel uczniów" in page and "bez przydziału" in page


def test_id_photo_deleted_after_stage_end_unless_hold(proctoring_on, config, stage, student, competition):
    other = make_student(competition, stage, first="Ewa")
    first, second = ready_session(stage, student), ready_session(stage, other)
    for session, key in ((first, "proctoring/k/r/a/id.jpg"), (second, "proctoring/k/r/b/id.jpg")):
        session.id_photo_key, session.id_photo_at = key, timezone.now()
        session.save()
    services.set_hold(second, None, "sprawa w toku")
    assert services.purge_id_photos() == 0  # etap trwa
    stage.opens_at = timezone.now() - timedelta(days=3)
    stage.deadline_at = timezone.now() - timedelta(days=2)
    stage.save()
    assert services.purge_id_photos() == 1
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.id_photo_key == "" and second.id_photo_key
    assert MemoryStorage.deleted == ["proctoring/k/r/a/id.jpg"]


def test_purge_erases_alternative_note(proctoring_on, config, stage, student, coordinator):
    session = media_session(stage, student, coordinator)
    services.request_alternative(session, "other", "choroba w rodzinie", user=student.user)
    published_long_ago(stage, days=40)
    services.purge_expired()
    session.refresh_from_db()
    assert session.alternative_note == "" and session.alternative_reason == "other"


def test_anonymisation_erases_alternative_note(proctoring_on, config, stage, student):
    from apps.accounts.profile import anonymise_account

    session = services.session_for(stage, student)
    services.request_alternative(session, "other", "choroba w rodzinie", user=student.user)
    anonymise_account(student.user)
    session.refresh_from_db()
    assert session.alternative_note == ""


def test_late_start_marker_in_roster_report_and_csv(
    proctoring_on, config, stage, student, coordinator, logged, settings
):
    settings.PROCTORING_LATE_START_MINUTES = 15
    session = ready_session(stage, student)
    ProctoringSession.objects.filter(pk=session.pk).update(started_at=stage.opens_at + timedelta(minutes=40))
    roster = logged(coordinator).get(f"/proctoring/{stage.pk}/roster/?group=m").json()
    assert roster["items"][0]["late"] == 40
    report = logged(coordinator).get(f"/proctoring/{stage.pk}/s/{session.pk}/").content.decode()
    assert "40 min" in report
    body = logged(coordinator).get(f"/proctoring/{stage.pk}/export.csv").content.decode("utf-8-sig")
    assert student.public_code in body and ";40;" in body


def test_on_time_start_has_no_marker(proctoring_on, config, stage, student):
    session = ready_session(stage, student)
    ProctoringSession.objects.filter(pk=session.pk).update(started_at=stage.opens_at + timedelta(minutes=5))
    session.refresh_from_db()
    assert services.late_start_minutes(session) == 0


def test_unproctored_reason_in_roster_report_and_csv(
    proctoring_on, config, stage, student, coordinator, logged, settings
):
    config.on_unavailable = "allow"
    config.save()
    settings.LIVEKIT_URL = ""
    session = ready_session(stage, student, started=False)
    services.continue_unproctored(session, config, user=student.user)
    roster = logged(coordinator).get(f"/proctoring/{stage.pk}/roster/?group=m").json()
    assert roster["items"][0]["unproctored_reason"] == "not_configured"
    report = logged(coordinator).get(f"/proctoring/{stage.pk}/s/{session.pk}/").content.decode()
    assert "nieskonfigurowany" in report
    body = logged(coordinator).get(f"/proctoring/{stage.pk}/export.csv").content.decode("utf-8-sig")
    assert "nieskonfigurowany" in body


def test_banner_has_a_separate_unproctored_text(proctoring_on, config, stage, student, logged):
    page = logged(student.user).get("/me/").content.decode()
    assert "data-text-unproctored" in page


def test_coordinator_has_separate_token_bucket(
    proctoring_on, fake_livekit, config, stage, coordinator, logged, settings
):
    from django.core.cache import cache

    rates = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    rates["proctoring_token"] = "1/hour"
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": rates}
    cache.clear()
    client = logged(coordinator)
    for _attempt in range(3):
        assert client.post(f"/proctoring/{stage.pk}/token/", {"group": "m"}).status_code == 200


# --- M4: okna TZ-01 -------------------------------------------------------------------------------------


def test_time_windows_app_is_used_automatically(stage, student, monkeypatch):
    """Zainstalowane ``apps.time_windows`` – okno ucznia z ``EffectiveWindow`` (+ tolerancja etapu).

    Moduł dostępu podstawiony, żeby test nie zależał od planu okien TZ-01 w bazie."""
    import sys
    import types

    opens = timezone.now() - timedelta(hours=1)
    deadline = timezone.now() + timedelta(hours=2)
    module = types.ModuleType("apps.time_windows.access")
    module.effective_window = lambda stage_, participant, competition=None: SimpleNamespace(
        opens_at=opens, deadline_at=deadline, extra_minutes=0
    )
    module.plan_for = lambda stage_, competition=None: object()  # etap ma plan okien
    monkeypatch.setitem(sys.modules, "apps.time_windows.access", module)
    monkeypatch.setattr(windows.apps, "is_installed", lambda name: name == windows.TIME_WINDOWS_APP)
    stage.grace_seconds = 60
    assert windows.effective_window(stage, student) == (opens, deadline + timedelta(seconds=60))
    # Etap bez okien w TZ-01 (``None``) – okno globalne.
    module.effective_window = lambda stage_, participant, competition=None: None
    assert windows.effective_window(stage, student) == windows.global_window(stage)
    # Przy oknach per uczeń okno „dla kogokolwiek” jest poszerzone o rozjazd stref.
    assert windows.envelope(stage)[0] == stage.opens_at - windows.ZONE_SPREAD


def test_time_windows_installed_without_plan_keeps_global_envelope(stage, student):
    """Sama instalacja TZ-01 (konkurs bez flagi okien) – okno globalne, bez poszerzenia o strefy."""
    assert windows.effective_window(stage, student) == windows.global_window(stage)
    assert windows.envelope(stage) == windows.global_window(stage)
