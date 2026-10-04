"""Listy zapraszające do wizy (VISA-01): wnioski opiekuna, decyzje oficera, powiadomienia, język listu,
kod weryfikacyjny i strona weryfikacji, unieważnienie, CSV, retencja i RODO.

Świat testów jest światem LOG-01 (``conftest.py``): delegacja Niemiec z opiekunem i dwoma uczniami,
delegacja Francji z opiekunem, oficer logistyki, finał za 60 dni.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserPreference
from apps.accounts.tests.factories import CoordinatorFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.delegation_logistics import letter_requests, letters, privacy, services, verification
from apps.delegation_logistics.letter_texts import LETTER_TEXTS, letter_languages
from apps.delegation_logistics.models import (
    InvitationLetter,
    LetterRequest,
    LetterRequestStatus,
    MemberKind,
)

from .conftest import years_ago

pytestmark = pytest.mark.django_db

PASSPORT = {
    "passport_name": "ANNA ADULT",
    "nationality": "de",
    "date_of_birth": years_ago(19),
    "passport_number": "C01X00T47",
    "passport_expiry": timezone.localdate() + timedelta(days=900),
}


def member_of(leader, participant=None, kind=MemberKind.STUDENT):
    members = services.members_of(leader.delegation)
    if participant is not None:
        return next(m for m in members if m.participant_id == participant.pk)
    return next(m for m in members if m.kind == kind)


def with_passport(leader, participant, **extra):
    member = member_of(leader, participant)
    services.save_member(member, {**PASSPORT, **extra}, actor=leader.user)
    return services.member_for_leader(leader, member.pk)


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)


# --- wniosek opiekuna ------------------------------------------------------------------------------------


def test_leader_requests_letter_and_officer_approves_it(iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])

    result = letter_requests.request_letters(leader, [member.pk], language="en")
    assert len(result["created"]) == 1
    row = result["created"][0]
    assert row.status == LetterRequestStatus.PENDING

    outcome = letter_requests.approve(iqo, [row], actor=officer)
    assert outcome["failed"] == []
    row.refresh_from_db()
    letter = row.letter
    assert row.status == LetterRequestStatus.APPROVED and row.decided_by == officer
    assert letter.member_id == member.pk and letter.language == "en"
    assert len(letter.verification_code) == 12 and letter.verification_code not in letter.number
    assert letter.event_name == "IQO 2027" and letter.event_starts_on == event.starts_on
    assert AuditLog.objects.filter(action="logistics.letter_request_approved").exists()


def test_only_one_pending_request_per_person(leader, students, event):
    member = with_passport(leader, students[0])
    letter_requests.request_letters(leader, [member.pk], language="en")
    again = letter_requests.request_letters(leader, [member.pk], language="en")
    assert again["created"] == [] and [m.pk for m in again["skipped"]] == [member.pk]
    assert LetterRequest.objects.filter(member=member).count() == 1


def test_request_needs_a_complete_travel_document(leader, students, event):
    member = member_of(leader, students[1])
    with pytest.raises(DomainError) as error:
        letter_requests.request_letters(leader, [member.pk], language="en")
    assert error.value.machine_code == "IDENTITY_INCOMPLETE"


def test_unsupported_letter_language_is_refused(leader, students, event):
    member = with_passport(leader, students[0])
    with pytest.raises(DomainError):
        letter_requests.request_letters(leader, [member.pk], language="zh-hans")


def test_other_country_leader_cannot_request_or_withdraw(leader, other_leader, students, event):
    from django.http import Http404

    member = with_passport(leader, students[0])
    with pytest.raises(Http404):
        letter_requests.request_letters(other_leader, [member.pk], language="en")
    row = letter_requests.request_letters(leader, [member.pk], language="en")["created"][0]
    with pytest.raises(Http404):
        letter_requests.request_for_leader(other_leader, row.pk)
    with pytest.raises(Http404):
        letter_requests.withdraw(other_leader, row)


def test_leader_withdraws_pending_request(leader, students, event):
    member = with_passport(leader, students[0])
    row = letter_requests.request_letters(leader, [member.pk], language="en")["created"][0]
    letter_requests.withdraw(leader, row)
    row.refresh_from_db()
    assert row.status == LetterRequestStatus.WITHDRAWN
    with pytest.raises(DomainError):
        letter_requests.withdraw(leader, row)


# --- decyzje oficera -------------------------------------------------------------------------------------


def test_coordinator_without_officer_grant_cannot_decide(iqo, leader, students, event):
    member = with_passport(leader, students[0])
    row = letter_requests.request_letters(leader, [member.pk], language="en")["created"][0]
    plain = CoordinatorFactory()
    with pytest.raises(PermissionDenied):
        letter_requests.approve(iqo, [row], actor=plain)
    with pytest.raises(PermissionDenied):
        letter_requests.reject(iqo, [row], reason="x", actor=plain)


def test_reject_requires_reason_and_notifies_leader_in_his_language(
    iqo, leader, students, officer, event, mailoutbox, django_capture_on_commit_callbacks
):
    iqo.interface_languages = ["pl", "en"]
    iqo.save(update_fields=["interface_languages"])
    UserPreference.objects.create(user=leader.user, language="en")
    member = with_passport(leader, students[0])
    row = letter_requests.request_letters(leader, [member.pk], language="en")["created"][0]

    with pytest.raises(DomainError):
        letter_requests.reject(iqo, [row], reason="  ", actor=officer)
    with django_capture_on_commit_callbacks(execute=True):
        rejected = letter_requests.reject(iqo, [row], reason="Passport expires too soon", actor=officer)

    assert [r.pk for r in rejected] == [row.pk]
    row.refresh_from_db()
    assert row.status == LetterRequestStatus.REJECTED and row.reject_reason == "Passport expires too soon"
    message = mailoutbox[-1]
    assert message.to == ["lead-de@example.test"]
    assert "Decision on invitation letters" in message.subject
    assert "Passport expires too soon" in message.body and "Rejected:" in message.body
    assert "C01X00T47" not in message.body
    # Powód nie trafia do dziennika zdarzeń (wolny tekst bez retencji).
    entry = AuditLog.objects.get(action="logistics.letter_request_rejected")
    assert "Passport" not in str(entry.diff)


def test_bulk_approve_sends_one_mail_per_leader_and_keeps_failures_pending(
    iqo, leader, students, officer, event, mailoutbox, django_capture_on_commit_callbacks
):
    adult, minor = students
    first = with_passport(leader, adult)
    second = with_passport(leader, minor, passport_name="MAX MINOR", passport_number="D99")
    rows = letter_requests.request_letters(leader, [first.pk, second.pk], language="en")["created"]
    # Opiekun czyści numer paszportu drugiej osoby po złożeniu wniosku – zatwierdzenie nie może przejść.
    services.save_member(second, {"passport_number": ""}, actor=leader.user)

    with django_capture_on_commit_callbacks(execute=True):
        outcome = letter_requests.approve(iqo, rows, actor=officer)

    assert [r.member_id for r in outcome["approved"]] == [first.pk]
    assert [r.member_id for r, _reason in outcome["failed"]] == [second.pk]
    assert LetterRequest.objects.get(member=second).status == LetterRequestStatus.PENDING
    assert len(mailoutbox) == 1
    assert outcome["approved"][0].letter.number in mailoutbox[0].body


def test_approval_supersedes_previous_valid_personal_letter(iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])
    old = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    row = letter_requests.request_letters(leader, [member.pk], language="en")["created"][0]
    letter_requests.approve(iqo, [row], actor=officer)

    old.refresh_from_db()
    row.refresh_from_db()
    assert old.revoked_at is not None and row.letter.number in old.revoke_reason
    assert row.letter.revoked_at is None


def test_requests_csv_has_no_passport_data(client_for, iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])
    letter_requests.request_letters(leader, [member.pk], language="en")
    client = logged_in(client_for, iqo, officer)

    response = client.get(reverse("web:coordinator-onsite-letter-requests-export") + "?state=PENDING")
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    assert response.status_code == 200
    assert "Anna Adult" in body and "C01X00T47" not in body


# --- PDF i język ------------------------------------------------------------------------------------------


def test_pdf_contains_number_name_and_verification_code(iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)

    text = pdf_text(letters.letter_pdf(letter))
    assert letter.number in text
    assert "ANNA ADULT" in text
    assert letter.display_code in text
    assert "/visa/verify/" in text
    assert "Letter of invitation" in text


def test_letter_language_changes_text_and_role(iqo, leader, students, officer, event):
    iqo.interface_languages = ["pl", "en", "fr", "zh-hans"]
    iqo.save(update_fields=["interface_languages"])
    # Chiński odpada (krój dokumentów nie ma jego znaków), angielski zawsze pierwszy.
    assert [code for code, _name in letter_languages(iqo)] == ["en", "pl", "fr"]
    member = with_passport(leader, students[0])

    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer, language="fr")
    text = pdf_text(letters.letter_pdf(letter))
    assert LETTER_TEXTS["fr"]["title"] in text
    assert "Lettre n°" in text
    assert letters.people_of(letter)[0]["role"] != "Uczeń"


def test_revoked_letter_cannot_be_downloaded(iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    with pytest.raises(DomainError):
        letter_requests.revoke(iqo, letter, reason="", actor=officer)
    letter_requests.revoke(iqo, letter, reason="Passport replaced", actor=officer)
    letter.refresh_from_db()
    with pytest.raises(DomainError) as error:
        letters.letter_pdf(letter)
    assert error.value.machine_code == "LETTER_REVOKED"
    entry = AuditLog.objects.get(action="logistics.letter_revoked")
    assert entry.diff == {"number": letter.number}


# --- strona weryfikacji ------------------------------------------------------------------------------------


def test_verify_page_shows_minimal_data_without_passport(client_for, iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    client = client_for(iqo)

    response = client.get(reverse("web:visa-verify-code", args=[letter.display_code.lower()]))
    page = response.content.decode()
    assert response.status_code == 200
    assert letter.number in page and "ANNA ADULT" in page and "Germany" in page and "IQO 2027" in page
    assert "C01X00T47" not in page
    assert PASSPORT["date_of_birth"].isoformat() not in page
    assert response["Cache-Control"] == "private, no-store"
    assert "noindex" in response["X-Robots-Tag"]


def test_verify_page_shows_revocation(client_for, iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    letter_requests.revoke(iqo, letter, reason="Visa refused – secret reason", actor=officer)

    result = verification.verify(iqo, letter.verification_code)
    assert result["valid"] is False and result["revoked_on"] is not None
    page = (
        client_for(iqo).get(reverse("web:visa-verify-code", args=[letter.verification_code])).content.decode()
    )
    assert "secret reason" not in page


def test_verify_unknown_code_and_form_redirect(client_for, iqo, event):
    client = client_for(iqo)
    response = client.get(reverse("web:visa-verify-code", args=["ZZZZZZZZZZZZ"]))
    assert response.status_code == 200 and verification.verify(iqo, "ZZZZZZZZZZZZ") is None

    redirect = client.get(reverse("web:visa-verify") + "?code=abcd-efgh-jkmn")
    assert redirect.status_code == 302 and redirect["Location"].endswith("/visa/verify/ABCDEFGHJKMN/")


def test_verify_is_scoped_to_the_competition_of_the_request(iqo, leader, students, officer, event):
    from apps.tenancy.models import Competition

    member = with_passport(leader, students[0])
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    other = Competition.objects.exclude(pk=iqo.pk).first()
    if other is not None:
        assert verification.verify(other, letter.verification_code) is None
    assert verification.verify(iqo, letter.verification_code) is not None


def test_verify_page_is_404_without_the_feature(client_for, competition):
    from apps.competitions.tests.factories import CurrentEditionFactory

    CurrentEditionFactory(competition=competition)
    response = client_for(competition).get(reverse("web:visa-verify-code", args=["ABCDEFGHJKMN"]))
    assert response.status_code == 404
    assert client_for(competition).get(reverse("web:visa-verify")).status_code == 404


def _rates(**rates) -> dict:
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


def test_verify_page_is_rate_limited(client_for, iqo, event):
    from django.core.cache import cache

    cache.clear()
    client = client_for(iqo)
    url = reverse("web:visa-verify-code", args=["ABCDEFGHJKMN"])
    with override_settings(REST_FRAMEWORK=_rates(visa_verify="2/hour")):
        assert client.get(url).status_code == 200
        assert client.get(url).status_code == 200
        assert client.get(url).status_code == 429


# --- ekrany ------------------------------------------------------------------------------------------------


def test_leader_screen_requests_and_officer_screen_approves(
    client_for, iqo, leader, students, officer, event
):
    member = with_passport(leader, students[0])
    leader_client = logged_in(client_for, iqo, leader.user)
    page = leader_client.get(reverse("web:delegation-logistics-letters"))
    assert page.status_code == 200 and page["Cache-Control"] == "private, no-store"

    response = leader_client.post(
        reverse("web:delegation-logistics-letters"), {"member": [str(member.pk)], "language": "en"}
    )
    assert response.status_code == 302
    row = LetterRequest.objects.get(member=member)

    officer_client = logged_in(client_for, iqo, officer)
    listing = officer_client.get(
        reverse("web:coordinator-onsite-letter-requests") + "?country=de&state=PENDING"
    )
    assert listing.status_code == 200 and "Anna Adult" in listing.content.decode()
    response = officer_client.post(
        reverse("web:coordinator-onsite-letter-requests-approve"), {"request": [str(row.pk)]}
    )
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.status == LetterRequestStatus.APPROVED

    download = leader_client.get(reverse("web:delegation-logistics-letter", args=[row.letter_id]))
    assert download.status_code == 200 and download["Content-Type"] == "application/pdf"


def test_coordinator_without_grant_gets_403_on_request_screens(client_for, iqo, leader, event):
    client = logged_in(client_for, iqo, CoordinatorFactory())
    assert client.get(reverse("web:coordinator-onsite-letter-requests")).status_code == 403
    assert client.post(reverse("web:coordinator-onsite-letter-requests-approve"), {}).status_code == 403


def test_leader_screens_are_404_without_the_feature(client_for, competition):
    from apps.accounts.tests.test_delegations import leader_for_country, make_delegations_competition

    iqo = make_delegations_competition(competition)  # tryb delegacji, ale bez flagi logistyki
    leader = leader_for_country(iqo, CoordinatorFactory(), "lead-x@example.test", country="de")
    client = logged_in(client_for, iqo, leader.user)
    assert client.get(reverse("web:delegation-logistics-letters")).status_code == 404


# --- RODO ------------------------------------------------------------------------------------------------


def test_retention_removes_requests_and_verify_says_data_removed(iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])
    row = letter_requests.request_letters(leader, [member.pk], language="en")["created"][0]
    letter_requests.approve(iqo, [row], actor=officer)
    row.refresh_from_db()

    event.starts_on = timezone.localdate() - timedelta(days=50)
    event.ends_on = timezone.localdate() - timedelta(days=40)
    event.save()
    privacy.purge_expired()

    assert not LetterRequest.objects.filter(pk=row.pk).exists()
    result = verification.verify(iqo, row.letter.verification_code)
    assert result["purged"] is True and result["people"] == [] and result["number"] == row.letter.number


def test_export_and_erasure_cover_requests(iqo, leader, students, officer, event):
    from apps.accounts.profile import _erase_account

    adult = students[0]
    member = with_passport(leader, adult)
    row = letter_requests.request_letters(leader, [member.pk], language="en")["created"][0]
    letter_requests.reject(iqo, [row], reason="Wrong passport", actor=officer)

    section = privacy.export_section(adult.user)[0]["wnioski_o_list_zapraszajacy"]
    assert section[0]["stan"] == "REJECTED" and section[0]["powod_odrzucenia"] == "Wrong passport"

    _erase_account(adult.user, actor=officer)
    assert not LetterRequest.objects.filter(pk=row.pk).exists()


def test_letters_issued_before_visa_01_get_codes_in_migration(iqo, leader, students, officer, event):
    """Migracja nadaje kod i migawkę wydarzenia wierszom rejestru sprzed VISA-01."""
    from importlib import import_module

    from django.apps import apps as django_apps

    member = with_passport(leader, students[0])
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    InvitationLetter.objects.filter(pk=letter.pk).update(verification_code=None, event_name="")

    module = import_module(f"apps.delegation_logistics.migrations.{VISA_MIGRATION}")
    module._codes(django_apps, None)
    letter.refresh_from_db()
    assert len(letter.verification_code) == 12 and letter.event_name == "IQO 2027"


#: Migracja VISA-01 w aplikacji ``delegation_logistics`` (po migracjach LOG-01).
VISA_MIGRATION = "0002_visa_letter_workflow"
