"""Przeglądarka audytu: filtry, stronicowanie i dostęp.

Testy chodzą po samym filtrze (``apps.core.audit_browser``) i po ekranie. Przedmiotem jest to,
czego nie widać po kodzie: że przedział dat obejmuje **cały** dzień z pola „do”, że listy wyboru
biorą wartości z danych, a nie ze spisu w kodzie, i że filtr nie gubi się przy stronicowaniu.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core import audit_browser
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def _entry(actor=None, *, action="stage.closed", target="competitions.stage", at=None, diff=None):
    """Wpis audytowy wprost w bazie – testy filtra nie potrzebują wywoływać operacji domenowej."""
    log = AuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=target,
        target_id="1",
        diff=diff or {},
    )
    if at is not None:
        AuditLog.objects.filter(pk=log.pk).update(at=at)
        log.refresh_from_db()
    return log


def test_actor_filter_matches_a_fragment_of_the_address(coordinator):
    _entry(coordinator)
    _entry(None, action="results.published")

    found = audit_browser.entries({"actor": coordinator.email[:5]})

    assert [log.action for log in found] == ["stage.closed"]


def test_action_filter_is_exact():
    _entry(action="stage.closed")
    _entry(action="stage.rule_updated")

    assert [log.action for log in audit_browser.entries({"action": "stage.closed"})] == ["stage.closed"]


def test_date_range_includes_the_whole_closing_day():
    day = timezone.localtime(timezone.now()).date()
    late = timezone.make_aware(
        timezone.datetime.combine(day, timezone.datetime.min.time()) + timedelta(hours=23, minutes=30),
        timezone.get_current_timezone(),
    )
    _entry(at=late)

    found = audit_browser.entries({"from": day.isoformat(), "to": day.isoformat()})

    assert found.count() == 1


def test_unparsable_date_is_treated_as_no_filter():
    _entry()

    assert audit_browser.entries({"from": "wczoraj"}).count() == 1


def test_known_actions_come_from_the_data():
    _entry(action="stage.closed")
    _entry(action="broadcast.sent")

    assert audit_browser.known_actions() == ["broadcast.sent", "stage.closed"]


def test_page_shows_entries_and_the_collapsed_diff(web_client, coordinator):
    _entry(coordinator, diff={"locked": 3})
    web_client.force_login(coordinator)

    response = web_client.get("/coordinator/audit/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "stage.closed" in content
    assert "<details" in content


def test_filter_survives_pagination(web_client, coordinator):
    for _ in range(audit_browser.AUDIT_PAGE_SIZE + 1):
        _entry(coordinator, action="stage.closed")
    web_client.force_login(coordinator)

    response = web_client.get("/coordinator/audit/", {"action": "stage.closed"})
    content = response.content.decode()

    assert response.context["paginator"].num_pages == 2
    assert "action=stage.closed&amp;page=2" in content


def test_page_is_forbidden_for_a_participant(web_client, participant):
    web_client.force_login(participant.user)

    assert web_client.get("/coordinator/audit/").status_code == 403
