"""Reguła „zaświadczenie od N warsztatów”: próg, idempotencja, brak odbierania i izolacja konkursów.

Testy wołają ``issue_due`` wprost (tak, jak robi to zadanie Celery) – drogę od zapisu obecności do
zadania sprawdza ``test_triggers.py``.
"""

from __future__ import annotations

import pytest

from apps.cms.models import WorkshopAttendance
from apps.core.models import AuditLog
from apps.results.certificates import certificate_content, issue_workshop_certificates, render_pdf
from apps.results.models import Certificate, CertificateKind, CertificateTemplate
from apps.workshop_certificates.models import AutoIssuedCertificate, AutoIssueTrigger
from apps.workshop_certificates.rules import evaluate, issue_due

from .helpers import attend, current_stage, enable, keys, participant_in, workshops_page_for

pytestmark = pytest.mark.django_db


def run(edition, **kwargs):
    return issue_due(edition, trigger=AutoIssueTrigger.ATTENDANCE, **kwargs)


# --- próg ----------------------------------------------------------------------------------------


def test_only_participants_at_or_above_the_threshold_get_a_certificate(stage):
    enable(stage.edition, threshold=3)
    below = participant_in(stage, "Jan", "Dwa")
    exact = participant_in(stage, "Ola", "Trzy")
    above = participant_in(stage, "Ewa", "Cztery")
    attend(below, 2)
    attend(exact, 3)
    attend(above, 4)

    result = run(stage.edition)

    owners = {certificate.entry.participant_id for certificate in result.issued}
    assert owners == {exact.pk, above.pk}
    assert all(certificate.kind == CertificateKind.WARSZTATY for certificate in result.issued)
    assert AutoIssuedCertificate.objects.filter(edition=stage.edition).count() == 2


def test_attendance_outside_the_schedule_does_not_count(stage):
    """Obecność przy kluczu spoza harmonogramu nie stoi na papierze – nie liczy się też do progu."""
    enable(stage.edition, threshold=3)
    participant = participant_in(stage)
    attend(participant, 2)
    WorkshopAttendance.objects.create(participant=participant, workshop_key="2026-01-01-zajecia-odwolane")

    assert run(stage.edition).issued == []
    evaluation = evaluate(stage.edition, 3)
    assert [(entry.participant_id, attended) for entry, attended in evaluation.below] == [(participant.pk, 2)]


def test_disabled_rule_issues_nothing(stage):
    policy = enable(stage.edition, threshold=1)
    policy.enabled = False
    policy.save()
    attend(participant_in(stage), 4)

    assert run(stage.edition).issued == []
    assert not Certificate.objects.exists()


def test_deleted_account_gets_no_new_certificate(stage):
    from apps.accounts.profile import anonymise_account

    enable(stage.edition, threshold=1)
    deleted = participant_in(stage, "Jan", "Usunięty")
    attend(deleted, 4)
    anonymise_account(deleted.user)

    assert run(stage.edition).issued == []


# --- idempotencja i brak odbierania ---------------------------------------------------------------


def test_second_run_issues_nothing_new(stage):
    enable(stage.edition, threshold=2)
    attend(participant_in(stage), 3)

    first = run(stage.edition)
    second = run(stage.edition)

    assert len(first.issued) == 1
    assert second.issued == []
    assert Certificate.objects.count() == 1


def test_certificate_issued_earlier_by_hand_keeps_its_number(stage):
    """Ręczne „Wystaw (ZIP)” i reguła dają ten sam dokument – reguła go nie dubluje ani nie przejmuje."""
    participant = participant_in(stage)
    attend(participant, 4)
    [manual] = issue_workshop_certificates(stage.edition)
    enable(stage.edition, threshold=1)

    assert run(stage.edition).issued == []
    assert list(Certificate.objects.values_list("number", flat=True)) == [manual.number]
    assert not AutoIssuedCertificate.objects.exists()


def test_removing_attendance_below_the_threshold_keeps_the_certificate(stage):
    enable(stage.edition, threshold=3)
    participant = participant_in(stage)
    attend(participant, 3)
    [certificate] = run(stage.edition).issued

    WorkshopAttendance.objects.filter(participant=participant, workshop_key__in=keys()[:2]).delete()
    run(stage.edition)

    assert Certificate.objects.filter(pk=certificate.pk).exists()
    # Treść jest – jak dotąd – liczona przy pobraniu, więc poprawka obecności poprawia listę tematów.
    assert len(certificate_content(Certificate.objects.get(pk=certificate.pk)).workshops) == 1


# --- audyt ---------------------------------------------------------------------------------------


def test_run_is_audited_with_counts_and_no_personal_data(stage):
    enable(stage.edition, threshold=1)
    participant = participant_in(stage, "Łucja", "Śniadecka")
    attend(participant, 1)

    run(stage.edition)

    entry = AuditLog.objects.get(action="workshop_certificates.auto_issued")
    assert entry.target_id == str(stage.edition.pk)
    assert entry.diff == {"issued": 1, "notified": 0, "threshold": 1, "trigger": "attendance"}
    serialized = str(entry.diff)
    assert "Śniadecka" not in serialized and participant.user.email not in serialized


# --- izolacja konkursów --------------------------------------------------------------------------


def test_attendance_in_another_competition_does_not_count(stage, other_competition):
    """Obecność profilu olimpiady B nie wchodzi do liczenia w A – nawet przy tym samym kluczu warsztatu."""
    from apps.accounts.tests.factories import ParticipantFactory

    enable(stage.edition, threshold=1)
    workshops_page_for(other_competition)
    other_stage = current_stage(other_competition)
    enable(other_stage.edition, threshold=1)
    stranger = ParticipantFactory(competition=other_competition)
    for key in keys():
        WorkshopAttendance.objects.create(participant=stranger, workshop_key=key)

    assert run(stage.edition).issued == []
    assert [c.entry.participant_id for c in run(other_stage.edition).issued] == []  # brak wpisu w B
    participant_b = participant_in(other_stage)
    attend(participant_b, 1)
    assert [c.edition_id for c in run(other_stage.edition).issued] == [other_stage.edition.pk]
    assert not Certificate.objects.filter(edition=stage.edition).exists()


# --- szablon z reguły ----------------------------------------------------------------------------


def test_template_chosen_in_the_rule_composes_the_document(stage, competition):
    from apps.workshop_certificates.documents import _chosen_template, handles

    template = CertificateTemplate.objects.create(competition=competition, name="Warsztaty 2026", kind="")
    enable(stage.edition, threshold=1, template=template)
    attend(participant_in(stage), 1)
    [issued] = run(stage.edition).issued
    certificate = Certificate.objects.get(pk=issued.pk)

    assert handles(certificate) is True
    assert _chosen_template(certificate) == template
    assert render_pdf(certificate).startswith(b"%PDF-")


def test_inactive_or_foreign_template_is_ignored(stage, competition, other_competition):
    from apps.workshop_certificates.documents import handles

    template = CertificateTemplate.objects.create(
        competition=competition, name="Stary", kind="", is_active=False
    )
    enable(stage.edition, threshold=1, template=template)
    attend(participant_in(stage), 1)
    [certificate] = run(stage.edition).issued
    assert handles(Certificate.objects.get(pk=certificate.pk)) is False

    foreign = CertificateTemplate.objects.create(competition=other_competition, name="Cudzy", kind="")
    enable(stage.edition, threshold=1, template=foreign)
    assert handles(Certificate.objects.get(pk=certificate.pk)) is False
