"""Publiczne API ``/api/v1/``: zakresy, zawężenie do edycji, dane osobowe i kształt odpowiedzi."""

import pytest
from django.urls import reverse

from apps.competitions.models import EditionEvent, StageEntryStatus
from apps.competitions.tests.factories import EditionFactory, StageFactory
from apps.integrations.models import (
    SCOPE_READ_PARTICIPANTS,
    SCOPE_READ_PARTICIPANTS_PII,
    SCOPE_READ_RESULTS,
    SCOPE_READ_STATS,
    SCOPE_READ_SUBMISSIONS_META,
    SCOPE_WRITE_EVENTS,
)
from apps.results.models import Anonymization, ResultsPublication
from apps.results.statistics import invalidate
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db

LAST_NAME = "Śniadecka"
EMAIL = "lucja.sniadecka@example.test"


def participants_url(stage) -> str:
    return reverse("integrations:stage-participants", args=[stage.pk])


def results_url(stage) -> str:
    return reverse("integrations:stage-results", args=[stage.pk])


def publish(stage, rows=None) -> ResultsPublication:
    """Ogłoszona tabela wprost ze snapshotu – API czyta wyłącznie jego, więc tyle wystarczy."""
    snapshot = rows or [
        {
            "rank": 1,
            "display": "OLM-0001",
            "points": {"1": 6, "2": 5},
            "total": 11,
            "qualified": True,
            "manual": False,
            "district": "mazowieckie",
        }
    ]
    return ResultsPublication.objects.create(stage=stage, anonymization=Anonymization.CODE, snapshot=snapshot)


# --- katalog ------------------------------------------------------------------------------------


def test_editions_are_listed_paginated(authed, edition):
    client, _ = authed()
    response = client.get(reverse("integrations:editions"))

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["year_label"] == edition.year_label


def test_key_bound_to_edition_sees_only_that_edition(authed, edition):
    """Zawężenie klucza jest zawężeniem **danych**, a nie tylko etykietą na liście."""
    other = EditionFactory()
    other_stage = StageFactory(edition=other)
    client, _ = authed(edition=edition)

    listing = client.get(reverse("integrations:editions"))
    assert [row["id"] for row in listing.data["results"]] == [edition.pk]

    # Cudzy etap ma być **nieodnajdywalny**, a nie zakazany: 403 zdradzałby, że istnieje.
    denied = client.get(participants_url(other_stage))
    assert denied.status_code == 404
    assert denied.data["code"] == "NOT_FOUND"


def test_stage_listing_carries_calendar(authed, stage):
    client, _ = authed()
    response = client.get(reverse("integrations:edition-stages", args=[stage.edition_id]))

    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["id"] == stage.pk
    assert row["problem_count"] == 2
    assert row["deadline_at"] is not None


# --- uczestnicy ---------------------------------------------------------------------------------


def test_participants_hide_personal_data_by_default(authed, stage, entry):
    client, _ = authed(scopes=[SCOPE_READ_PARTICIPANTS], pii_allowed=False)
    response = client.get(participants_url(stage))

    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["public_code"] == entry.participant.public_code
    assert row["voivodeship"] == "mazowieckie"
    assert set(row) == {
        "public_code",
        "school",
        "school_city",
        "voivodeship",
        "grade",
        "status",
        "registered_at",
    }
    assert LAST_NAME not in response.content.decode()
    assert EMAIL not in response.content.decode()


def test_participants_show_personal_data_with_both_consents(authed, stage, entry):
    client, _ = authed(scopes=[SCOPE_READ_PARTICIPANTS, SCOPE_READ_PARTICIPANTS_PII], pii_allowed=True)
    response = client.get(participants_url(stage))

    row = response.data["results"][0]
    assert row["last_name"] == LAST_NAME
    assert row["email"] == EMAIL


def test_pii_scope_without_flag_falls_back_to_anonymous_shape(authed, stage, entry):
    """Klucz z zakresem PII, ale bez zgody koordynatora, widzi tyle, co każdy inny."""
    client, key = authed(scopes=[SCOPE_READ_PARTICIPANTS, SCOPE_READ_PARTICIPANTS_PII], pii_allowed=True)
    key.pii_allowed = False
    key.save(update_fields=["pii_allowed"])

    response = client.get(participants_url(stage))
    assert "last_name" not in response.data["results"][0]


def test_participants_can_be_filtered(authed, stage, entry):
    client, _ = authed(scopes=[SCOPE_READ_PARTICIPANTS], pii_allowed=False)

    assert client.get(participants_url(stage), {"voivodeship": "mazowieckie"}).data["count"] == 1
    assert client.get(participants_url(stage), {"voivodeship": "pomorskie"}).data["count"] == 0
    assert client.get(participants_url(stage), {"grade": 3}).data["count"] == 1
    assert client.get(participants_url(stage), {"school": "XIV"}).data["count"] == 1
    assert client.get(participants_url(stage), {"status": StageEntryStatus.DISQUALIFIED}).data["count"] == 0


# --- wyniki -------------------------------------------------------------------------------------


def test_results_require_publication(authed, stage):
    client, _ = authed(scopes=[SCOPE_READ_RESULTS], pii_allowed=False)
    response = client.get(results_url(stage))

    assert response.status_code == 404
    assert response.data["code"] == "NOT_FOUND"


def test_published_results_come_from_snapshot(authed, stage):
    publication = publish(stage)
    client, _ = authed(scopes=[SCOPE_READ_RESULTS], pii_allowed=False)
    response = client.get(results_url(stage))

    assert response.status_code == 200
    assert response.data["stage"]["anonymization"] == publication.anonymization
    assert response.data["stage"]["count"] == 1
    row = response.data["results"][0]
    assert row["display"] == "OLM-0001"
    assert row["total"] == 11
    # Województwo wychodzi pod angielską nazwą pola, bo cała odpowiedź jest angielska.
    assert row["voivodeship"] == "mazowieckie"
    assert "district" not in row


def test_results_are_paginated(authed, stage):
    rows = [
        {
            "rank": i,
            "display": f"OLM-{i:04d}",
            "points": {"1": 1},
            "total": 1,
            "qualified": False,
            "manual": False,
        }
        for i in range(1, 6)
    ]
    publish(stage, rows)
    client, _ = authed(scopes=[SCOPE_READ_RESULTS], pii_allowed=False)
    response = client.get(results_url(stage), {"page_size": 2})

    assert len(response.data["results"]) == 2
    assert response.data["count"] == 5
    # Nagłówek publikacji jest na **każdej** stronie, nie tylko na pierwszej.
    assert client.get(results_url(stage), {"page_size": 2, "page": 3}).data["stage"]["count"] == 5


# --- zgłoszenia i statystyki --------------------------------------------------------------------


def test_submissions_expose_metadata_only(authed, stage, entry):
    problem = stage.problems.order_by("number").first()
    submission = SubmissionFactory(entry=entry, problem=problem, version=2, status=SubmissionStatus.LOCKED)
    client, _ = authed(scopes=[SCOPE_READ_SUBMISSIONS_META], pii_allowed=False)
    response = client.get(reverse("integrations:stage-submissions", args=[stage.pk]))

    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["id"] == submission.pk
    assert row["participant_code"] == entry.participant.public_code
    assert row["problem_number"] == problem.number
    assert row["version"] == 2
    assert set(row) == {
        "id",
        "participant_code",
        "problem_number",
        "version",
        "status",
        "is_late",
        "submitted_at",
    }


def test_stats_come_from_published_stages(authed, stage):
    publish(stage)
    invalidate()
    client, _ = authed(scopes=[SCOPE_READ_STATS], pii_allowed=False)
    response = client.get(reverse("integrations:stats"))

    assert response.status_code == 200
    assert response.data["results"][0]["stage_id"] == stage.pk
    assert response.data["results"][0]["participants"] == 1


# --- zapis wydarzenia ----------------------------------------------------------------------------


def test_event_can_be_created_with_write_scope(authed, edition):
    client, key = authed(scopes=[SCOPE_WRITE_EVENTS], pii_allowed=False)
    response = client.post(
        reverse("integrations:events"),
        {"title": "Gala finałowa", "starts_on": "2027-05-20", "url": "/gala/"},
        format="json",
    )

    assert response.status_code == 201
    event = EditionEvent.objects.get(pk=response.data["id"])
    assert event.edition_id == edition.pk
    assert event.title == "Gala finałowa"
    # Klucz nie jest kontem, więc autorstwo zapisuje osobny wpis audytowy z przedrostkiem klucza.
    from apps.core.models import AuditLog

    entry = AuditLog.objects.get(action="apikey.event_created", target_id=str(event.pk))
    assert entry.diff["key"] == key.prefix


def test_event_without_scope_is_forbidden(authed, edition):
    client, _ = authed(scopes=[SCOPE_READ_RESULTS], pii_allowed=False)
    response = client.post(
        reverse("integrations:events"), {"title": "Gala", "starts_on": "2027-05-20"}, format="json"
    )

    assert response.status_code == 403
    assert response.data["code"] == "MISSING_SCOPE"


def test_event_respects_domain_rules(authed, edition):
    """Regułę rozstrzyga serwis wydarzeń – API nie ma własnej, drugiej kopii."""
    client, _ = authed(scopes=[SCOPE_WRITE_EVENTS], pii_allowed=False)
    response = client.post(
        reverse("integrations:events"),
        {"title": "Gala", "starts_on": "2027-05-20", "url": "javascript:alert(1)"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "EVENT_URL_INVALID"


def test_event_cannot_be_written_to_another_edition(authed, edition):
    other = EditionFactory()
    client, _ = authed(scopes=[SCOPE_WRITE_EVENTS], pii_allowed=False, edition=edition)
    response = client.post(
        reverse("integrations:events"),
        {"edition_id": other.pk, "title": "Gala", "starts_on": "2027-05-20"},
        format="json",
    )

    assert response.status_code == 404
    assert not EditionEvent.objects.filter(edition=other).exists()
