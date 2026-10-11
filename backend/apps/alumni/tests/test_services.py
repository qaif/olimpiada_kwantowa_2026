"""Sieć absolwentów w serwisie: kwalifikowalność, zgoda, profil, podpis, katalog, ściana, izolacja."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.alumni import services
from apps.alumni.achievements import Achievement, achievements_for, register_source
from apps.alumni.models import (
    ALUMNI_CONSENT_VERSION,
    AlumniConsentEvent,
    AlumniProfile,
    ConsentEventKind,
    Level,
)
from apps.alumni.tests.helpers import alumnus, coordinator_of, enable, joined, participant_of, past_final
from apps.alumni.validators import clean_github, clean_linkedin
from apps.competitions.models import StageEntryStatus
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.results.models import Certificate, CertificateKind

pytestmark = pytest.mark.django_db


# --- osiągnięcia i kwalifikowalność ---------------------------------------------------------------------


def test_laureate_of_published_final_in_past_edition_is_eligible(competition):
    enable(competition)
    person = alumnus(competition)

    status = services.eligibility(person)

    assert status.eligible
    assert [item.level for item in status.achievements] == [Level.LAUREATE]
    assert status.achievements[0].finished


def test_unpublished_results_give_no_achievement(competition):
    enable(competition)
    person = alumnus(competition, stage=past_final(competition, published=False))

    status = services.eligibility(person)

    assert not status.eligible
    assert status.reason == services.NO_ACHIEVEMENT
    assert status.achievements == []


def test_threshold_is_configurable(competition):
    enable(competition, eligibility=Level.LAUREATE)
    finalist = alumnus(competition, "Fin", laureate=False)
    laureate = alumnus(competition, "Lau")

    assert not services.eligibility(finalist).eligible
    assert services.eligibility(laureate).eligible

    enable(competition, eligibility=Level.FINALIST)
    assert services.eligibility(finalist).eligible


def test_disqualified_and_training_entries_do_not_count(competition):
    enable(competition, eligibility=Level.ANY)
    person = participant_of(competition, "Dyskwalifikowany", birth_year=1998)
    StageEntryFactory(
        competition=competition,
        participant=person,
        stage=past_final(competition),
        status=StageEntryStatus.DISQUALIFIED,
    )

    assert achievements_for([person]) == {}
    assert not services.eligibility(person).eligible


def test_certificate_counts_even_when_table_was_published_by_code(competition):
    enable(competition, eligibility=Level.LAUREATE)
    person = alumnus(competition, laureate=False)
    entry = person.stage_entries.get()
    Certificate.objects.create(
        edition=entry.stage.edition, entry=entry, kind=CertificateKind.LAUREAT, number="OK/2025/9999"
    )

    assert services.eligibility(person).eligible


def test_minor_cannot_join_even_with_achievement(competition):
    enable(competition)
    young = alumnus(competition, "Młoda", birth_year=2012)

    status = services.eligibility(young)

    assert not status.eligible
    assert status.reason == services.MINOR
    with pytest.raises(DomainError) as exc:
        services.join(user=young.user, competition=competition, consent=True)
    assert exc.value.status_code == 403


def test_current_edition_counts_once_all_its_stages_are_published(competition):
    from apps.competitions.models import Edition

    enable(competition)
    stage = past_final(competition)
    Edition.objects.filter(pk=stage.edition_id).update(is_current=True)
    person = alumnus(competition, stage=stage)

    assert services.eligibility(person).eligible


def test_external_achievement_source_is_merged(competition, monkeypatch):
    """Rejestr źródeł (medale w przyszłości) – równy poziom z własnym podpisem wygrywa z gołym."""
    from apps.alumni import achievements

    monkeypatch.setattr(achievements, "_SOURCES", [])
    person = alumnus(competition)
    edition = person.stage_entries.get().stage.edition
    register_source(
        lambda people: [
            Achievement(p.pk, edition.pk, edition.year_label, Level.LAUREATE, True, "złoty medal")
            for p in people
        ]
    )

    [item] = achievements_for([person])[person.pk]
    assert item.title == "złoty medal"


# --- zgoda i wycofanie ---------------------------------------------------------------------------------


def test_join_requires_explicit_consent(competition):
    enable(competition)
    person = alumnus(competition)

    with pytest.raises(DomainError):
        services.join(user=person.user, competition=competition, consent=False)
    assert not AlumniProfile.objects.exists()


def test_join_records_consent_proof_and_audit(competition):
    enable(competition)
    person = alumnus(competition)

    profile = joined(person)

    assert profile.consent_version == ALUMNI_CONSENT_VERSION
    assert profile.public is False
    assert profile.listed is True
    [event] = AlumniConsentEvent.objects.filter(participant=person)
    assert event.kind == ConsentEventKind.GRANTED
    assert AuditLog.objects.filter(action=services.AUDIT_JOINED).exists()


def test_withdraw_deletes_profile_and_keeps_proof(competition):
    enable(competition)
    person = alumnus(competition)
    joined(person, bio="Coś o mnie", university="UW")

    services.withdraw(user=person.user, competition=competition)

    assert not AlumniProfile.objects.filter(participant=person).exists()
    kinds = list(
        AlumniConsentEvent.objects.filter(participant=person)
        .order_by("created_at")
        .values_list("kind", flat=True)
    )
    assert kinds == [ConsentEventKind.GRANTED, ConsentEventKind.WITHDRAWN]


def test_flag_off_is_404(competition):
    person = alumnus(competition)

    with pytest.raises(DomainError) as exc:
        services.join(user=person.user, competition=competition, consent=True)
    assert exc.value.status_code == 404


def test_reviewer_is_forbidden(competition):
    enable(competition)
    reviewer = ActiveReviewerFactory(competition=competition)

    with pytest.raises(DomainError) as exc:
        services.join(user=reviewer.user, competition=competition, consent=True)
    assert exc.value.status_code == 403


# --- profil i podpis ---------------------------------------------------------------------------------


def test_full_name_only_with_choice_and_publication_consent(competition):
    enable(competition)
    person = alumnus(competition, "Ola", "Kwantowa", publish_full_name=False)
    profile = joined(person, show_full_name=True)

    assert services.display_name(profile) == "Ola K."

    person.publish_full_name = True
    person.save(update_fields=["publish_full_name"])
    profile.refresh_from_db()
    assert services.display_name(profile) == "Ola Kwantowa"

    profile.show_full_name = False
    assert services.display_name(profile) == "Ola K."


def test_update_profile_filters_interests_and_clamps_capacity(competition):
    enable(competition)
    person = alumnus(competition)
    joined(person)

    profile = services.update_profile(
        user=person.user,
        competition=competition,
        data={"interests": ["physics", "hacking", "physics"], "mentor_capacity": 99, "linkedin_url": ""},
    )

    assert profile.interests == ["physics"]
    assert profile.mentor_capacity == 10


@pytest.mark.parametrize(
    "value",
    [
        "http://www.linkedin.com/in/ola",
        "https://evil.example/in/ola",
        "https://www.linkedin.com.evil.example/in/ola",
        "https://user:pass@www.linkedin.com/in/ola",
        "javascript:alert(1)",
        "https://www.linkedin.com/feed/",
    ],
)
def test_linkedin_validation_rejects(value):
    with pytest.raises(ValidationError):
        clean_linkedin(value)


def test_links_are_normalised_without_query():
    assert clean_linkedin("https://www.linkedin.com/in/ola?utm=x#y") == "https://www.linkedin.com/in/ola"
    assert clean_github(" https://github.com/ola ") == "https://github.com/ola"
    with pytest.raises(ValidationError):
        clean_github("https://gitlab.com/ola")


# --- katalog i ściana --------------------------------------------------------------------------------


def test_directory_lists_only_listed_visible_profiles_without_self(competition):
    enable(competition)
    viewer = joined(alumnus(competition, "Viewer"))
    shown = joined(alumnus(competition, "Shown"))
    joined(alumnus(competition, "Unlisted"), listed=False)
    hidden = joined(alumnus(competition, "Hidden"))
    coordinator = coordinator_of(competition)
    services.set_hidden(competition=competition, actor=coordinator, pk=hidden.pk, hidden=True)

    rows = list(services.directory(viewer.participant))

    assert rows == [shown]


def test_directory_is_scoped_to_competition(competition, other_competition):
    enable(competition)
    enable(other_competition)
    mine = joined(alumnus(competition, "Moja"))
    joined(alumnus(other_competition, "Obca"))
    viewer = participant_of(competition, "Widz")

    assert list(services.directory(viewer)) == [mine]


def test_wall_shows_only_public_profiles_and_only_when_enabled(competition):
    enable(competition, public_wall=False)
    public = joined(alumnus(competition, "Publiczna"), public=True)
    joined(alumnus(competition, "Prywatna"))

    assert list(services.wall(competition)) == []

    enable(competition, public_wall=True)
    assert list(services.wall(competition)) == [public]


def test_hide_is_coordinator_only_and_audited(competition):
    enable(competition)
    profile = joined(alumnus(competition))
    stranger = participant_of(competition, "Obcy")

    with pytest.raises(DomainError):
        services.set_hidden(competition=competition, actor=stranger.user, pk=profile.pk, hidden=True)

    coordinator = coordinator_of(competition)
    services.set_hidden(competition=competition, actor=coordinator, pk=profile.pk, hidden=True)
    assert AuditLog.objects.filter(action=services.AUDIT_HIDDEN).exists()


def test_eligible_not_joined_count(competition):
    enable(competition)
    joined(alumnus(competition, "Jest"))
    alumnus(competition, "Nie")
    alumnus(competition, "Młoda", birth_year=2012)

    assert services.eligible_not_joined_count(competition) == 1
