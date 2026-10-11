"""Poprawki po przeglądzie krytyka ALUM-01 (04.10.2026) – po jednym bloku na identyfikator uwagi."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.alumni import mentoring, safety, services
from apps.alumni.achievements import achievements_for
from apps.alumni.models import (
    AlumniConsentEvent,
    AlumniProfile,
    Channel,
    EndReason,
    Mentorship,
    MentorshipFlag,
    MentorshipStatus,
    NoteStatus,
)
from apps.alumni.tests.helpers import (
    alumnus,
    chat,
    coordinator_of,
    current_stage,
    enable,
    joined,
    mentee,
    mentor,
    participant_of,
    past_final,
)
from apps.alumni.validators import clean_event_url, clean_github, clean_linkedin
from apps.chat import services as chat_services
from apps.chat.models import AgePolicy, MessageStatus, PeerMode
from apps.competitions.models import StageEntryStatus
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.results.models import Certificate, CertificateKind

pytestmark = pytest.mark.django_db


def ask(person, profile, **kwargs):
    return mentoring.request_mentor(
        user=person.user, competition=person.competition, token=profile.token, **kwargs
    )


def accept(profile, row):
    return mentoring.accept(user=profile.participant.user, competition=row.competition, pk=row.pk)


def logged(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


# --- H1: notatka małoletniego, opis mentora, wzorce kontaktowe ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ("zadzwoń 600 123 456", "telefon"),
        ("+48 600-123-456", "telefon"),
        ("pisz na ala@example.com", "e-mail"),
        ("ala (at) example (dot) com", "e-mail"),
        ("mój nick @ala_kwant", "@nazwa"),
        ("napisz na Discordzie albo discord", "komunikator"),
        ("WhatsApp proszę", "komunikator"),
        ("t.me/ala", "komunikator"),
    ],
)
def test_contact_patterns(text, hit):
    assert hit in safety.contact_hits(text)


def test_plain_note_has_no_contact_hits():
    assert safety.contact_hits("Pomóż mi z zadaniem 3 z fizyki kwantowej, edycja 2026.") == []


def test_minor_note_waits_for_coordinator_and_is_never_mailed(
    client_for, competition, django_capture_on_commit_callbacks
):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition, minor=True)

    with django_capture_on_commit_callbacks(execute=True):
        row = ask(student, teacher, note="Nie rozumiem splątania")

    assert row.note_status == NoteStatus.PENDING
    assert "splątania" not in mail.outbox[0].body
    mentor_client = logged(client_for, competition, teacher.participant.user)
    page = mentor_client.get(reverse("web:alumni")).content.decode()
    assert "splątania" not in page
    assert "czeka na akceptację organizatora" in page

    coordinator = coordinator_of(competition)
    assert [item.pk for item in mentoring.pending_notes(competition)] == [row.pk]
    coordinator_page = logged(client_for, competition, coordinator).get(
        reverse("web:coordinator-alumni-mentoring")
    )
    assert "splątania" in coordinator_page.content.decode()
    logged(client_for, competition, coordinator).post(
        reverse("web:coordinator-alumni-note", args=[row.pk, "approve"])
    )

    assert "splątania" in mentor_client.get(reverse("web:alumni")).content.decode()


def test_rejected_minor_note_stays_hidden(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    row = ask(mentee(competition, minor=True), teacher, note="Tekst")

    mentoring.decide_note(
        competition=competition, actor=coordinator_of(competition), pk=row.pk, approve=False
    )

    row.refresh_from_db()
    assert row.note_status == NoteStatus.REJECTED
    assert not mentoring.note_visible_to_mentor(row)


def test_adult_note_is_visible_without_approval(competition):
    enable(competition)
    chat(competition)
    row = ask(mentee(competition, minor=False), mentor(competition), note="Hej")

    assert row.note_status == NoteStatus.NONE
    assert mentoring.note_visible_to_mentor(row)


def test_contact_like_note_raises_automatic_flag(competition, django_capture_on_commit_callbacks):
    enable(competition)
    chat(competition)
    coordinator = coordinator_of(competition)
    teacher = mentor(competition)

    with django_capture_on_commit_callbacks(execute=True):
        row = ask(mentee(competition, minor=False), teacher, note="mój discord: kwant#1234, tel 600123456")

    flag = MentorshipFlag.objects.get(mentorship=row)
    assert flag.automatic and flag.reporter_id is None
    assert "telefon" in flag.reason and "komunikator" in flag.reason
    assert any(coordinator.email in message.to for message in mail.outbox)


def test_mentor_bio_hidden_from_minors_until_approved(client_for, competition):
    enable(competition)
    teacher = mentor(
        competition,
        bio="Kwantofil zaprasza, pisz na instagram",
        linkedin_url="https://www.linkedin.com/in/ola",
    )
    plain = joined(alumnus(competition, "Bez"), bio="Zwykły absolwent")  # nie mentor – nic do akceptacji
    minor = mentee(competition, minor=True)
    adult = mentee(competition, "Dorosły", minor=False)

    minor_html = (
        logged(client_for, competition, minor.user).get(reverse("web:alumni-directory")).content.decode()
    )
    adult_html = (
        logged(client_for, competition, adult.user).get(reverse("web:alumni-directory")).content.decode()
    )

    assert "Kwantofil" not in minor_html
    assert "linkedin.com/in/ola" not in minor_html
    assert "Zwykły absolwent" in minor_html
    assert "Kwantofil" in adult_html

    [queued] = services.content_review_queue(competition)
    assert queued.pk == teacher.pk and queued.contact_hits == ["komunikator"]
    with pytest.raises(DomainError):
        services.approve_content(competition=competition, actor=minor.user, pk=teacher.pk)
    services.approve_content(competition=competition, actor=coordinator_of(competition), pk=teacher.pk)
    minor_html = (
        logged(client_for, competition, minor.user).get(reverse("web:alumni-directory")).content.decode()
    )
    assert "Kwantofil" in minor_html

    # Zmiana treści unieważnia akceptację.
    services.update_profile(
        user=teacher.participant.user, competition=competition, data={"bio": "Nowy opis z numerem 600700800"}
    )
    teacher.refresh_from_db()
    assert safety.needs_review(teacher)
    assert plain.pk not in [row.pk for row in services.content_review_queue(competition)]


# --- M1: ukrycie mentora i warunki akceptacji ---------------------------------------------------------------


def test_hiding_mentor_ends_relations_and_declines_requests(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition, mentor_capacity=3)
    active = accept(teacher, ask(mentee(competition, "A"), teacher))
    pending = ask(mentee(competition, "B"), teacher)

    services.set_hidden(
        competition=competition, actor=coordinator_of(competition), pk=teacher.pk, hidden=True
    )

    active.refresh_from_db()
    pending.refresh_from_db()
    assert active.status == MentorshipStatus.ENDED and active.end_reason == EndReason.HIDDEN
    assert pending.status == MentorshipStatus.DECLINED and pending.end_reason == EndReason.HIDDEN
    with pytest.raises(DomainError):
        chat_services.send_participant_message(
            user=teacher.participant.user, competition=competition, conversation=active.conversation, body="x"
        )


def test_accept_requires_available_mentor_and_current_mentee(competition):
    from apps.accounts.models import Membership

    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    row = ask(student, teacher)

    AlumniProfile.objects.filter(pk=teacher.pk).update(mentor_available=False)
    with pytest.raises(DomainError) as exc:
        accept(teacher, row)
    assert exc.value.machine_code == "ALUMNI_NOT_AVAILABLE"

    AlumniProfile.objects.filter(pk=teacher.pk).update(mentor_available=True)
    Membership.objects.filter(user=student.user, competition=competition).delete()
    student.user.groups.clear()
    with pytest.raises(DomainError) as exc:
        accept(teacher, row)
    assert exc.value.machine_code == "ALUMNI_MENTEE_GONE"


# --- M2: data urodzenia zapisana przy akceptacji i potwierdzona pełnoletność ----------------------------


def test_stored_birth_date_keeps_supervision_after_edit(competition, django_capture_on_commit_callbacks):
    enable(competition)
    chat(competition, peer_mode=PeerMode.NONE, age_policy=AgePolicy.SAME_GROUP)
    coordinator = coordinator_of(competition)
    teacher = mentor(competition)
    student = mentee(competition, minor=True)
    row = accept(teacher, ask(student, teacher))
    assert row.mentee_birth_year == student.birth_year

    student.birth_date = student.birth_date.replace(year=1990)
    with django_capture_on_commit_callbacks(execute=True):
        student.save()

    row.refresh_from_db()
    assert mentoring.channel_for(row) == Channel.SUPERVISED
    message = chat_services.send_participant_message(
        user=teacher.participant.user, competition=competition, conversation=row.conversation, body="Hej"
    )
    assert message.status == MessageStatus.PENDING
    assert AuditLog.objects.filter(action=mentoring.AUDIT_BIRTH_DATE_CHANGED).exists()
    assert MentorshipFlag.objects.filter(mentorship=row, automatic=True).exists()
    assert any(coordinator.email in message.to for message in mail.outbox)


def test_mentor_confirmed_adult_stays_adult_for_channel(competition):
    enable(competition)
    chat(competition, peer_mode=PeerMode.NONE, age_policy=AgePolicy.SAME_GROUP)
    teacher = mentor(competition)
    student = mentee(competition, minor=True)
    row = accept(teacher, ask(student, teacher))
    assert teacher.adult_confirmed_at is not None

    person = teacher.participant
    person.birth_date = person.birth_date.replace(year=2011)
    person.save()

    row = Mentorship.objects.select_related("mentor", "mentee").get(pk=row.pk)
    assert mentoring.mixed_ages(row)
    assert mentoring.channel_for(row) == Channel.SUPERVISED


def test_profile_save_without_birth_change_costs_no_extra_queries(competition, django_assert_num_queries):
    enable(competition)
    person = participant_of(competition)
    person.phone = "+48600000001"
    with django_assert_num_queries(1):
        person.save(update_fields=["phone"])


# --- M3: tylko ogłoszone wyniki, bez dyskwalifikacji, ściana i katalog z zakończonych edycji ------------


def test_certificates_need_published_non_disqualified_entries(competition):
    enable(competition)
    unpublished = participant_of(competition, "Nieogł", birth_year=1998)
    entry = StageEntryFactory(
        competition=competition, participant=unpublished, stage=past_final(competition, published=False)
    )
    Certificate.objects.create(
        edition=entry.stage.edition, entry=entry, kind=CertificateKind.LAUREAT, number="A/1"
    )
    disqualified = participant_of(competition, "Dysk", birth_year=1998)
    entry = StageEntryFactory(
        competition=competition,
        participant=disqualified,
        stage=past_final(competition),
        status=StageEntryStatus.DISQUALIFIED,
    )
    Certificate.objects.create(
        edition=entry.stage.edition, entry=entry, kind=CertificateKind.LAUREAT, number="A/2"
    )

    assert achievements_for([unpublished, disqualified]) == {}


def test_directory_shows_only_finished_editions(client_for, competition):
    enable(competition)
    person = alumnus(competition, "Ola")
    stage = current_stage(competition)
    StageEntryFactory(
        competition=competition, participant=person, stage=stage, status=StageEntryStatus.QUALIFIED
    )
    type(stage).objects.filter(pk=stage.pk).update(results_published_at=timezone.now())
    # Drugi, nieogłoszony etap bieżącej edycji – edycja nie jest zakończona.
    from apps.competitions.models import StageKind

    StageFactory(competition=competition, edition=stage.edition, kind=StageKind.DISTRICT)
    joined(person)

    [card] = services.cards(services.directory(participant_of(competition, "Widz")))
    assert all(item.finished for item in card.achievements)
    assert stage.edition.year_label not in [item.edition_label for item in card.achievements]
    assert any(not item.finished for item in achievements_for([person])[person.pk])


# --- M4: wycofanie zgody przy wyłączonej sieci -------------------------------------------------------------


def test_withdrawal_reachable_when_flag_is_off(client_for, competition):
    enable(competition)
    person = alumnus(competition)
    joined(person)
    outsider = participant_of(competition, "Bez")
    competition.feature_flags = {**competition.feature_flags, "alumni": False}
    competition.save(update_fields=["feature_flags"])

    client = logged(client_for, competition, person.user)
    page = client.get(reverse("web:alumni"))
    assert page.status_code == 200
    assert reverse("web:alumni-withdraw") in page.content.decode()
    assert client.get(reverse("web:alumni-directory")).status_code == 404
    assert logged(client_for, competition, outsider.user).get(reverse("web:alumni")).status_code == 404

    assert client.post(reverse("web:alumni-withdraw")).status_code == 302
    assert not AlumniProfile.objects.filter(participant=person).exists()


# --- M5: minimalizacja zamiast pełnej anonimizacji ----------------------------------------------------------


def _expire(stage):
    edition = stage.edition
    edition.data_retention_months = 1
    edition.save(update_fields=["data_retention_months"])


def test_retention_minimises_held_alumni_and_anonymises_hidden(competition):
    from apps.accounts.anonymised import is_anonymised
    from apps.accounts.retention import anonymise_expired_editions

    enable(competition)
    kept = alumnus(
        competition, "Zostaje", phone="+48600111222", school="XIV LO", supervisor_email="n@example.com"
    )
    _expire(kept.stage_entries.get().stage)
    joined(kept)
    hidden = alumnus(competition, "Ukryta")
    _expire(hidden.stage_entries.get().stage)
    profile = joined(hidden)
    AlumniProfile.objects.filter(pk=profile.pk).update(hidden_at=timezone.now())

    anonymise_expired_editions(competition=competition)

    kept.refresh_from_db()
    kept.user.refresh_from_db()
    assert not is_anonymised(kept.user)
    assert kept.user.first_name == "Zostaje"
    assert (kept.phone, kept.school, kept.supervisor_email, kept.district) == ("", "", "", "")
    assert kept.birth_date is None and kept.birth_year == 1998
    assert kept.stage_entries.exists()
    assert services.eligibility(kept).achievements
    assert AuditLog.objects.filter(action="account.minimised_by_retention").exists()
    hidden.user.refresh_from_db()
    assert is_anonymised(hidden.user)


# --- L1: walidatory odporne na złą składnię -----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://[::1",
        "https://www.linkedin.com:abc/in/ola",
        "https://www.linkedin.com/in/../feed",
        "https://www.linkedin.com/in/ola%2F..%2Fx",
        "https://www.linkedin.com/in/%2e%2e/x",
    ],
)
def test_bad_urls_are_validation_errors(value):
    with pytest.raises(ValidationError):
        clean_linkedin(value)


def test_bad_event_url_is_validation_error():
    with pytest.raises(ValidationError):
        clean_event_url("https://[::1")
    with pytest.raises(ValidationError):
        clean_github("https://github.com:99999999/x")


# --- L2: rozmowa szyfrowana pod wymuszoną moderacją --------------------------------------------------------


def test_encrypted_conversation_blocks_acceptance_and_flags(competition, django_capture_on_commit_callbacks):
    enable(competition)
    chat(competition, peer_mode=PeerMode.NONE, age_policy=AgePolicy.ANY)
    coordinator = coordinator_of(competition)
    teacher = mentor(competition)
    student = mentee(competition, minor=True)
    chat_services._peer_conversation(teacher.participant, student, encrypted=True)
    row = ask(student, teacher)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True), pytest.raises(DomainError) as exc:
        accept(teacher, row)

    assert exc.value.machine_code == "ALUMNI_ENCRYPTED_CONVERSATION"
    row.refresh_from_db()
    assert row.status == MentorshipStatus.REQUESTED
    assert MentorshipFlag.objects.filter(
        mentorship=row, automatic=True, reason=mentoring.AUTO_ENCRYPTED
    ).exists()
    assert [message.to for message in mail.outbox] == [[coordinator.email]]
    assert student.user.email not in [address for message in mail.outbox for address in message.to]


# --- L3: łączenie polityk ----------------------------------------------------------------------------


def test_policies_combine_by_strictest():
    combined = chat_services.combine_policies(
        [
            chat_services.PeerPolicy(at_least=PeerMode.POST, notice="A"),
            chat_services.PeerPolicy(mode=PeerMode.PRE, skip_age_policy=True, notice="B"),
            None,
        ]
    )
    assert combined.mode == PeerMode.PRE
    assert combined.at_least == PeerMode.POST
    assert combined.skip_age_policy is False  # łagodzi tylko za zgodą wszystkich
    assert combined.notice == "A B"
    refused = chat_services.combine_policies(
        [chat_services.PeerPolicy(mode=PeerMode.PRE), chat_services.PeerPolicy(refusal="Stop")]
    )
    assert refused.refusal == "Stop"


# --- L4: zero zapytań bez flagi, odmowa po wyłączeniu ----------------------------------------------------


def test_policy_costs_no_queries_when_alumni_never_enabled(competition, django_assert_num_queries):
    chat(competition)
    a = participant_of(competition, "A")
    b = participant_of(competition, "B")
    conversation, _ = chat_services.ensure_peer_conversation(a, b)
    conversation.competition  # noqa: B018 - konkurs załadowany, jak w każdym wołającym czatu

    with django_assert_num_queries(0):
        assert mentoring.chat_policy(conversation) is None


def test_policy_pauses_conversations_after_flag_off(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    row = accept(teacher, ask(mentee(competition, minor=False), teacher))
    competition.feature_flags = {**competition.feature_flags, "alumni": False}
    competition.save(update_fields=["feature_flags"])

    conversation = type(row.conversation).objects.select_related("competition").get(pk=row.conversation_id)
    assert mentoring.chat_policy(conversation).refusal == str(mentoring.PAUSED_REFUSAL)


# --- L7: dowód zgody i odnowienie ---------------------------------------------------------------------------


def test_consent_event_stores_language_and_hash(competition):
    enable(competition)
    profile = joined(alumnus(competition))

    event = AlumniConsentEvent.objects.get(participant=profile.participant)
    assert event.language
    assert len(event.text_hash) == 64


def test_new_consent_version_hides_profile_until_renewed(competition, monkeypatch):
    enable(competition)
    person = alumnus(competition)
    joined(person)
    viewer = participant_of(competition, "Widz")
    monkeypatch.setattr(services, "ALUMNI_CONSENT_VERSION", "2099-01-01")

    assert services.needs_reconsent(services.profile_of(person))
    assert list(services.directory(viewer)) == []

    services.renew_consent(user=person.user, competition=competition, consent=True)

    assert not services.needs_reconsent(services.profile_of(person))
    assert len(list(services.directory(viewer))) == 1
    assert AlumniConsentEvent.objects.filter(participant=person, version="2099-01-01").exists()


# --- L11: pętla „poproś → wycofaj → poproś” -------------------------------------------------------------


def test_request_cancel_request_loop_is_limited(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)

    first = ask(student, teacher)
    mentoring.cancel(user=student.user, competition=competition, pk=first.pk)
    second = ask(student, teacher)
    mentoring.cancel(user=student.user, competition=competition, pk=second.pk)
    with pytest.raises(DomainError) as exc:
        ask(student, teacher)
    assert exc.value.machine_code == "ALUMNI_REQUEST_LIMIT"

    # Tydzień później znów wolno.
    Mentorship.objects.filter(mentee=student).update(created_at=timezone.now() - timedelta(days=8))
    ask(student, teacher)


def test_daily_request_limit_across_mentors(competition):
    enable(competition)
    chat(competition)
    student = mentee(competition)
    mentors = [mentor(competition, f"M{i}") for i in range(6)]

    for teacher in mentors[:5]:
        row = ask(student, teacher)
        mentoring.cancel(user=student.user, competition=competition, pk=row.pk)
    with pytest.raises(DomainError) as exc:
        ask(student, mentors[5])
    assert exc.value.machine_code == "ALUMNI_REQUEST_LIMIT"


# --- medale (MED-01) jako źródło osiągnięć ------------------------------------------------------------------


def test_frozen_medals_become_achievements(competition):
    from apps.medals.models import MedalScheme

    enable(competition)
    gold = alumnus(competition, "Złota", laureate=False)
    honourable = alumnus(competition, "Wyróż", laureate=False)
    stage_gold = gold.stage_entries.get().stage
    stage_hm = honourable.stage_entries.get().stage
    MedalScheme.objects.create(
        stage=stage_gold,
        frozen_at=timezone.now(),
        awards={str(gold.stage_entries.get().pk): {"award": "GOLD"}},
    )
    # Schemat niezamrożony (nieogłoszony) nie liczy się wcale.
    MedalScheme.objects.create(
        stage=stage_hm, awards={str(honourable.stage_entries.get().pk): {"award": "HM"}}
    )

    [item] = achievements_for([gold])[gold.pk]
    assert item.title == "złoty medal"
    assert item.rank == 4
    [plain] = achievements_for([honourable])[honourable.pk]
    assert plain.title == "finalista"

    MedalScheme.objects.filter(stage=stage_hm).update(frozen_at=timezone.now())
    [item] = achievements_for([honourable])[honourable.pk]
    assert item.title == "wyróżnienie"
