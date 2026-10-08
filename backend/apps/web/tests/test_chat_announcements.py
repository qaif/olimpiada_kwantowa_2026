"""Ogłoszenia organizatora w Wiadomościach (CZ-ANN-01) – ekrany uczestnika i panel koordynatora.

Reguły serwisu sprawdza ``apps/chat/tests/test_announcements.py``. Tutaj: gdzie uczestnik widzi
ogłoszenie (skrzynka ``/me/messages/`` i pulpit ``/me/``), co trafia do HTML-a (escape, odnośnik),
i bramki ekranu ``/coordinator/inbox-announcements/``.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import UserFactory
from apps.chat import announcements
from apps.chat.models import OrganizerAnnouncement
from apps.chat.tests.helpers import configure, coordinator_of, participant_of
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

INBOX = "/me/messages/"
DASHBOARD = "/me/"
PANEL = "/coordinator/inbox-announcements/"
MEET = "https://meet.google.com/abc-defg-hij"
HEADING = "Ogłoszenia organizatora"


def edit_url(item) -> str:
    return f"{PANEL}{item.pk}/"


def action_url(item, action: str) -> str:
    return f"{PANEL}{item.pk}/{action}/"


def publish(competition, title="Warsztaty online", body=f"Link do warsztatów: {MEET}"):
    return announcements.publish_announcement(competition=competition, title=title, body=body, actor=None)


def page(client, participant, url) -> str:
    client.force_login(participant.user)
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


# --- uczestnik -----------------------------------------------------------------------------------------


def test_participant_sees_announcement_in_inbox_and_dashboard(web_client, competition):
    publish(competition)
    ala = participant_of(competition)

    for url in (INBOX, DASHBOARD):
        content = page(web_client, ala, url)
        assert HEADING in content
        assert "Warsztaty online" in content


def test_account_registered_after_publication_sees_it(web_client, competition):
    """Wymaganie organizatora: „także tych, co dopiero się zarejestrują” – odczyt w chwili wyświetlenia."""
    publish(competition)
    newcomer = participant_of(competition, "Nowa", "Osoba")

    assert "Warsztaty online" in page(web_client, newcomer, INBOX)


def test_no_block_without_announcements_and_after_unpublishing(web_client, competition):
    ala = participant_of(competition)
    assert HEADING not in page(web_client, ala, INBOX)

    item = publish(competition)
    announcements.set_published(announcement=item, actor=None, published=False)

    assert HEADING not in page(web_client, ala, INBOX)
    assert "Warsztaty online" not in page(web_client, ala, DASHBOARD)


def test_announcement_of_another_competition_is_not_shown(web_client, competition, other_competition):
    publish(other_competition, title="Obcy konkurs")
    ala = participant_of(competition)

    assert "Obcy konkurs" not in page(web_client, ala, INBOX)
    assert "Obcy konkurs" not in page(web_client, ala, DASHBOARD)


def test_dashboard_shows_announcements_when_messages_are_disabled(web_client, competition):
    """Skrzynka za wyłączonym modułem oddaje 404 – pulpit nadal pokazuje ogłoszenie."""
    configure(competition, enabled=False)
    publish(competition)
    ala = participant_of(competition)
    web_client.force_login(ala.user)

    assert web_client.get(INBOX).status_code == 404
    assert "Warsztaty online" in page(web_client, ala, DASHBOARD)


def test_body_is_escaped_and_urls_become_safe_links(web_client, competition):
    publish(competition, title="<b>Tytuł</b>", body=f"<script>alert(1)</script>\nSpotkanie: {MEET}")
    content = page(web_client, participant_of(competition), INBOX)

    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
    assert "&lt;b&gt;Tytuł&lt;/b&gt;" in content
    assert f'<a href="{MEET}" rel="nofollow noopener noreferrer" target="_blank">{MEET}</a>' in content


def test_open_thread_does_not_repeat_the_block(web_client, competition):
    from apps.chat import services

    publish(competition)
    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation

    assert HEADING not in page(web_client, ala, f"/me/messages/{conversation.pk}/")


# --- panel koordynatora --------------------------------------------------------------------------------


def as_coordinator(client, competition):
    user = coordinator_of(competition)
    client.force_login(user)
    return user


def test_anonymous_is_sent_to_login(web_client, competition):
    response = web_client.get(PANEL)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


@pytest.mark.parametrize(
    "role", [CompetitionRole.PARTICIPANT, CompetitionRole.REVIEWER, CompetitionRole.SUPERVISOR]
)
def test_other_roles_get_403(web_client, competition, role):
    item = publish(competition)
    user = UserFactory(groups=[role])
    grant_membership(user, competition, role)
    web_client.force_login(user)

    assert web_client.get(PANEL).status_code == 403
    assert web_client.post(PANEL, {"title": "x", "body": "y", "publish": "1"}).status_code == 403
    assert web_client.post(action_url(item, "unpublish")).status_code == 403
    assert OrganizerAnnouncement.objects.filter(is_published=True).count() == 1


def test_coordinator_creates_and_publishes(web_client, competition):
    user = as_coordinator(web_client, competition)
    assert web_client.get(PANEL).status_code == 200

    response = web_client.post(PANEL, {"title": "Warsztaty", "body": f"Link: {MEET}", "publish": "1"})

    assert response.status_code == 302
    item = OrganizerAnnouncement.objects.get()
    assert item.is_published and item.created_by == user and item.competition == competition
    assert list(
        AuditLog.objects.filter(target_id=str(item.pk)).order_by("id").values_list("action", flat=True)
    ) == [
        "chat.announcement.created",
        "chat.announcement.published",
    ]
    assert "Warsztaty" in web_client.get(PANEL).content.decode()


def test_coordinator_saves_a_draft_and_publishes_it_later(web_client, competition):
    as_coordinator(web_client, competition)
    web_client.post(PANEL, {"title": "Szkic", "body": "Treść", "publish": "0"})
    item = OrganizerAnnouncement.objects.get()
    assert not item.is_published

    assert web_client.get(action_url(item, "publish")).status_code == 405
    assert web_client.post(action_url(item, "publish")).status_code == 302
    item.refresh_from_db()
    assert item.is_published

    assert web_client.post(action_url(item, "unpublish")).status_code == 302
    item.refresh_from_db()
    assert not item.is_published
    assert AuditLog.objects.filter(action="chat.announcement.unpublished", target_id=str(item.pk)).exists()


def test_coordinator_edits_and_deletes(web_client, competition):
    as_coordinator(web_client, competition)
    item = publish(competition)

    assert web_client.get(edit_url(item)).status_code == 200
    response = web_client.post(edit_url(item), {"title": "Nowy tytuł", "body": "Nowa treść"})
    assert response.status_code == 302
    item.refresh_from_db()
    assert (item.title, item.body, item.is_published) == ("Nowy tytuł", "Nowa treść", True)

    assert web_client.post(action_url(item, "delete")).status_code == 302
    assert not OrganizerAnnouncement.objects.exists()
    assert set(AuditLog.objects.filter(target_id=str(item.pk)).values_list("action", flat=True)) >= {
        "chat.announcement.updated",
        "chat.announcement.deleted",
    }


def test_invalid_window_is_a_form_error(web_client, competition):
    as_coordinator(web_client, competition)

    response = web_client.post(
        PANEL,
        {
            "title": "x",
            "body": "y",
            "published_from": "2026-10-10T12:00",
            "published_until": "2026-10-10T11:00",
            "publish": "1",
        },
    )

    assert response.status_code == 400
    assert "Koniec widoczności" in response.content.decode()
    assert not OrganizerAnnouncement.objects.exists()


def test_announcement_of_another_competition_is_404(web_client, competition, other_competition):
    foreign = publish(other_competition, title="Obce")
    as_coordinator(web_client, competition)

    assert web_client.get(edit_url(foreign)).status_code == 404
    for action in ("publish", "unpublish", "delete"):
        assert web_client.post(action_url(foreign, action)).status_code == 404
    assert "Obce" not in web_client.get(PANEL).content.decode()
    foreign.refresh_from_db()
    assert foreign.is_published


def test_panel_works_with_messages_disabled_and_is_in_the_menu(web_client, competition):
    configure(competition, enabled=False)
    as_coordinator(web_client, competition)

    response = web_client.get(PANEL)

    assert response.status_code == 200
    assert f'href="{PANEL}"' in web_client.get("/coordinator/").content.decode()
