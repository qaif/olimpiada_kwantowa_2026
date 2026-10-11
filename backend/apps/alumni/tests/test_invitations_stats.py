"""Zaproszenia (filtry, język odbiorcy, wypis) i statystyki „gdzie są teraz” (k-anonimowość)."""

from __future__ import annotations

from collections import Counter

import pytest
from django.core import mail
from django.urls import reverse

from apps.alumni import invitations, stats
from apps.alumni.models import AlumniInvitation, AlumniProfile, InvitationKind, Level
from apps.alumni.tests.helpers import alumnus, coordinator_of, enable, joined, past_final
from apps.core.api import DomainError
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def send(competition, actor, **kwargs):
    defaults = {
        "kind": InvitationKind.WEBINAR,
        "title": "Webinar o kubitach",
        "body": "Zapraszamy!",
        "url": "",
    }
    return invitations.send_invitation(competition=competition, actor=actor, **{**defaults, **kwargs})


def test_audience_filters_by_edition_level_interest_and_opt_out(competition):
    enable(competition)
    stage_a = past_final(competition, year_label="2024/2025")
    stage_b = past_final(competition, year_label="2025/2026")
    laureate_a = joined(alumnus(competition, "A", stage=stage_a), interests=["physics"])
    finalist_b = joined(alumnus(competition, "B", stage=stage_b, laureate=False), interests=["mathematics"])
    joined(alumnus(competition, "C", stage=stage_a), invitations=False)

    assert set(invitations.audience(competition)) == {laureate_a, finalist_b}
    assert invitations.audience(competition, {"editions": [stage_b.edition_id]}) == [finalist_b]
    assert invitations.audience(competition, {"min_level": Level.LAUREATE}) == [laureate_a]
    assert invitations.audience(competition, {"interests": ["mathematics"]}) == [finalist_b]


def test_send_queues_mail_with_unsubscribe_headers_and_counts_only(
    competition, django_capture_on_commit_callbacks
):
    enable(competition)
    profile = joined(alumnus(competition))
    coordinator = coordinator_of(competition)

    with django_capture_on_commit_callbacks(execute=True):
        invitation = send(competition, coordinator, url="https://example.org/webinar")

    [message] = mail.outbox
    assert message.to == [profile.participant.user.email]
    assert "Webinar o kubitach" in message.subject
    assert "https://example.org/webinar" in message.body
    assert message.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert invitation.recipients == 1
    assert AuditLog.objects.filter(action=invitations.AUDIT_SENT).exists()


def test_invitation_frame_is_in_recipient_language(competition, django_capture_on_commit_callbacks):
    from apps.accounts.models import UserPreference

    competition.interface_languages = ["pl", "en"]
    competition.save(update_fields=["interface_languages"])
    enable(competition)
    profile = joined(alumnus(competition))
    UserPreference.objects.update_or_create(user=profile.participant.user, defaults={"language": "en"})

    with django_capture_on_commit_callbacks(execute=True):
        send(competition, coordinator_of(competition))

    body = mail.outbox[0].body
    assert "Zapraszamy!" in body  # treść koordynatora bez zmian
    assert "Dzień dobry" not in body  # ramka w języku odbiorcy


def test_send_requires_coordinator_and_valid_url(competition):
    enable(competition)
    joined(alumnus(competition))
    stranger = alumnus(competition, "Obcy")

    with pytest.raises(DomainError):
        send(competition, stranger.user)
    with pytest.raises(DomainError) as exc:
        send(competition, coordinator_of(competition), url="http://insecure.example")
    assert exc.value.machine_code == "ALUMNI_INVITATION_URL"
    assert not AlumniInvitation.objects.exists()


def test_no_recipients_is_refused(competition):
    enable(competition)
    with pytest.raises(DomainError) as exc:
        send(competition, coordinator_of(competition))
    assert exc.value.machine_code == "ALUMNI_NO_RECIPIENTS"


def test_unsubscribe_get_shows_button_post_unsubscribes(client_for, competition):
    enable(competition)
    profile = joined(alumnus(competition))
    token = invitations.unsubscribe_token(profile)
    client = client_for(competition, enforce_csrf_checks=True)
    url = reverse("web:alumni-unsubscribe", args=[token])

    assert client.get(url).status_code == 200
    assert AlumniProfile.objects.get(pk=profile.pk).invitations is True

    assert client.post(url, {"List-Unsubscribe": "One-Click"}).status_code == 200
    assert AlumniProfile.objects.get(pk=profile.pk).invitations is False

    assert client.get(reverse("web:alumni-unsubscribe", args=["zly-token"])).status_code == 400


def test_unsubscribe_token_does_not_survive_rejoining(competition):
    from apps.alumni import services

    enable(competition)
    person = alumnus(competition)
    old = invitations.unsubscribe_token(joined(person))
    services.withdraw(user=person.user, competition=competition)
    fresh = joined(person)

    assert invitations.apply_unsubscribe(competition, old) is False
    fresh.refresh_from_db()
    assert fresh.invitations is True


# --- statystyki --------------------------------------------------------------------------------------


def test_k_anonymity_merges_small_groups_without_complementary_cells():
    """L6: „inne” poniżej progu wciąga najmniejszą pokazaną grupę – żadna komórka nie jest < k."""
    rows = stats.k_anonymous(Counter({"uw": 9, "pw": 6, "agh": 3, "pk": 1}), {"uw": "UW", "pw": "PW"})
    assert [(row.label, row.shown) for row in rows] == [("UW", "9"), ("inne", "10")]
    assert all(row.count is not None and row.count >= 5 for row in rows)

    rows = stats.k_anonymous(Counter({"uw": 5, "agh": 3, "pw": 2}))
    assert [(row.label, row.count) for row in rows] == [("uw", 5), ("inne", 5)]

    # Nie ma czego dołożyć – zostaje „< 5”, ale wtedy jedyną komórką jest „inne”.
    rows = stats.k_anonymous(Counter({"agh": 3}))
    assert [(row.label, row.shown) for row in rows] == [("inne", "< 5")]


def test_small_network_has_no_distributions(competition):
    enable(competition)
    for index in range(4):
        joined(alumnus(competition, f"A{index}"), university="UW")

    result = stats.where_are_they_now(competition)

    assert result["total"] == 4
    assert result["too_small"] is True
    assert result["tables"] == []


def test_distributions_hide_groups_below_five(competition):
    enable(competition)
    stage = past_final(competition)
    for index in range(6):
        joined(
            alumnus(competition, f"U{index}", stage=stage),
            university=" Uniwersytet  Warszawski ",
            country="pl",
        )
    joined(alumnus(competition, "Sam", stage=stage), university="MIT", country="us")

    result = stats.where_are_they_now(competition)
    universities = next(table for table in result["tables"] if table.title == "Uczelnia")

    labels = {row.label: row.shown for row in universities.rows}
    # Sześć osób z UW i jedna z MIT: pokazanie „UW 6” przy znanej sumie odsłoniłoby tę jedną osobę,
    # więc całość ląduje w „inne” (L6), a suma na ekranie jest zaokrąglona.
    assert labels == {"inne": "7"}
    assert "MIT" not in str(result)
    assert result["total"] == "~5"
