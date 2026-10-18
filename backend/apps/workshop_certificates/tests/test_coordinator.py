"""Ekran ``/coordinator/workshops/auto-certificates/``: reguła, liczby, „Przelicz teraz”, poniżej progu."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.tests.factories import ParticipantFactory
from apps.core.models import AuditLog
from apps.results.models import Certificate, CertificateKind, CertificateTemplate
from apps.workshop_certificates.models import AutoIssuedCertificate, AutoIssuePolicy

from .helpers import attend, enable, participant_in

pytestmark = pytest.mark.django_db

URL = "/coordinator/workshops/auto-certificates/"


@pytest.fixture(autouse=True)
def _clear_cooldown():
    cache.clear()
    yield
    cache.clear()


def test_url_name_resolves():
    assert reverse("web:coordinator-workshop-auto-certificates") == URL


def test_participant_cannot_open_the_screen(client_for, competition, stage):
    participant = ParticipantFactory(competition=competition)
    client = client_for(competition)
    client.force_login(participant.user)

    assert client.get(URL).status_code in (302, 403, 404)
    assert client.post(URL, {"action": "recalculate"}).status_code in (302, 403, 404)
    assert not AutoIssuePolicy.objects.exists()


def test_saving_the_rule_is_audited_without_personal_data(coordinator_client, stage):
    response = coordinator_client.post(
        URL, {"action": "save", "enabled": "on", "min_workshops": "3", "notify_participants": "on"}
    )

    assert response.status_code == 302
    policy = AutoIssuePolicy.objects.get(edition=stage.edition)
    assert (policy.enabled, policy.min_workshops, policy.notify_participants) == (True, 3, True)
    entry = AuditLog.objects.get(action="workshop_certificates.policy_updated")
    assert entry.diff == {"enabled": True, "min_workshops": 3, "notify_participants": True}


def test_saving_does_not_issue_by_itself(coordinator_client, stage):
    """Zapis reguły nie wysyła nagle listów do wszystkich – od tego jest „Przelicz teraz”."""
    attend(participant_in(stage), 4)

    coordinator_client.post(URL, {"action": "save", "enabled": "on", "min_workshops": "1"})

    assert not Certificate.objects.exists()


def test_threshold_must_be_positive(coordinator_client, stage):
    response = coordinator_client.post(URL, {"action": "save", "enabled": "on", "min_workshops": "0"})

    assert response.status_code == 400
    assert not AutoIssuePolicy.objects.exists()


def test_template_of_another_competition_is_rejected(coordinator_client, stage, other_competition):
    foreign = CertificateTemplate.objects.create(competition=other_competition, name="Cudza winieta", kind="")

    response = coordinator_client.post(
        URL, {"action": "save", "enabled": "on", "min_workshops": "1", "template": str(foreign.pk)}
    )

    assert response.status_code == 400
    assert not AutoIssuePolicy.objects.exists()
    assert "Cudza winieta" not in response.content.decode()


def test_own_workshop_template_can_be_chosen(coordinator_client, stage, competition):
    template = CertificateTemplate.objects.create(
        competition=competition, name="Winieta warsztatowa", kind=CertificateKind.WARSZTATY
    )
    CertificateTemplate.objects.create(
        competition=competition, name="Winieta laureatów", kind=CertificateKind.LAUREAT
    )

    page = coordinator_client.get(URL).content.decode()
    assert "Winieta warsztatowa" in page and "Winieta laureatów" not in page
    coordinator_client.post(URL, {"action": "save", "min_workshops": "1", "template": str(template.pk)})

    assert AutoIssuePolicy.objects.get().template == template


def test_recalculate_issues_to_everyone_at_the_threshold(
    coordinator_client, stage, django_capture_on_commit_callbacks
):
    enable(stage.edition, threshold=2)
    above = participant_in(stage, "Ola", "Dwa")
    below = participant_in(stage, "Jan", "Jeden")
    attend(above, 2)
    attend(below, 1)

    with django_capture_on_commit_callbacks(execute=True):
        response = coordinator_client.post(URL, {"action": "recalculate"})

    assert response.status_code == 302
    assert list(Certificate.objects.values_list("entry__participant_id", flat=True)) == [above.pk]
    assert AuditLog.objects.filter(action="workshop_certificates.recalculate_requested").count() == 1
    page = coordinator_client.get(URL)
    assert page.context["auto_issued"] == 1
    assert page.context["due_count"] == 0
    assert [entry.participant_id for entry, _ in page.context["below"]] == [below.pk]
    assert "1 z 2" in page.content.decode()


def test_recalculate_twice_in_a_row_is_queued_once(
    coordinator_client, stage, django_capture_on_commit_callbacks
):
    enable(stage.edition, threshold=1)
    attend(participant_in(stage), 1)

    with django_capture_on_commit_callbacks(execute=True):
        coordinator_client.post(URL, {"action": "recalculate"})
        coordinator_client.post(URL, {"action": "recalculate"})

    assert AuditLog.objects.filter(action="workshop_certificates.recalculate_requested").count() == 1
    assert AutoIssuedCertificate.objects.count() == 1


def test_recalculate_with_disabled_rule_is_refused(
    coordinator_client, stage, django_capture_on_commit_callbacks
):
    attend(participant_in(stage), 4)

    with django_capture_on_commit_callbacks(execute=True):
        coordinator_client.post(URL, {"action": "recalculate"})

    assert not Certificate.objects.exists()
    page = coordinator_client.get(URL)
    # Liczba „spełnia próg, czeka” jest informacją także przy wyłączonej regule – po to, żeby było
    # widać, ilu osobom wyjdą dokumenty po jej włączeniu.
    assert page.context["due_count"] == 1
    assert 'disabled aria-describedby="recalculate-hint"' in page.content.decode()


def test_screen_shows_only_this_competition(coordinator_client, stage, other_competition):
    from .helpers import current_stage, workshops_page_for

    workshops_page_for(other_competition)
    other_stage = current_stage(other_competition)
    stranger = participant_in(other_stage, "Obcy", "Zinnegokonkursu")
    attend(stranger, 1)
    enable(stage.edition, threshold=3)

    page = coordinator_client.get(URL)

    assert "Zinnegokonkursu" not in page.content.decode()
    assert page.context["edition"] == stage.edition
