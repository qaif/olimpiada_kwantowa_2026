"""Listy zapraszające do wizy (VISA-01): wnioski opiekuna, decyzje oficera, powiadomienia, język listu,
kod weryfikacyjny i strona weryfikacji, unieważnienie, CSV, retencja i RODO.

Świat testów jest światem LOG-01 (``conftest.py``): delegacja Niemiec z opiekunem i dwoma uczniami,
delegacja Francji z opiekunem, oficer logistyki, finał za 60 dni.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO, StringIO

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


def test_approval_keeps_previous_letter_when_material_data_unchanged(iqo, leader, students, officer, event):
    """M1: nowy list z tymi samymi danymi istotnymi nie unieważnia poprzedniego (leży w konsulacie)."""
    member = with_passport(leader, students[0])
    old = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    # Zmiana daty ważności paszportu nie jest zmianą istotną.
    services.save_member(
        member, {"passport_expiry": timezone.localdate() + timedelta(days=1200)}, actor=leader.user
    )
    row = letter_requests.request_letters(leader, [member.pk], language="en")["created"][0]
    assert letter_requests.would_revoke([row]) == {row.pk: []}
    letter_requests.approve(iqo, [row], actor=officer)

    old.refresh_from_db()
    row.refresh_from_db()
    assert old.revoked_at is None and row.letter.revoked_at is None


def test_approval_supersedes_letter_with_changed_passport(iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])
    old = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    services.save_member(member, {"passport_number": "NEW999"}, actor=leader.user)
    row = letter_requests.request_letters(leader, [member.pk], language="en")["created"][0]

    # Podgląd dla oficera i ostrzeżenie dla opiekuna – zanim ktokolwiek kliknie.
    assert letter_requests.would_revoke([row]) == {row.pk: [old.number]}
    rows = letter_requests.leader_rows(leader.delegation, services.members_of(leader.delegation))
    assert next(r for r in rows if r["member"].pk == member.pk)["would_revoke"] == [old.number]

    letter_requests.approve(iqo, [row], actor=officer)
    old.refresh_from_db()
    row.refresh_from_db()
    assert old.revoked_at is not None and row.letter.number in old.revoke_reason
    assert row.letter.revoked_at is None


def test_direct_issue_by_officer_follows_the_same_rule(iqo, leader, students, officer, event):
    """LOG-01: list imienny wystawiony z karty osoby – ta sama reguła zastępowania (M1)."""
    member = with_passport(leader, students[0])
    first = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    same = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    assert same.superseded == []
    services.save_member(member, {"passport_name": "ANNA NEW NAME"}, actor=leader.user)
    third = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    assert sorted(third.superseded) == sorted([first.number, same.number])


def test_delegation_letter_with_outdated_data_is_flagged(client_for, iqo, leader, students, officer, event):
    member = with_passport(leader, students[0])
    letter = letters.issue_letter(iqo, leader.delegation, actor=officer)  # list delegacji
    services.save_member(member, {"nationality": "fr"}, actor=leader.user)

    members = {m.pk: m for m in services.edition_members(leader.delegation.edition)}
    assert letters.outdated_names(letter, members) == ["ANNA ADULT"]
    page = logged_in(client_for, iqo, officer).get(reverse("web:coordinator-onsite-letters"))
    assert "nieaktualne dane: ANNA ADULT" in page.content.decode()
    letter.refresh_from_db()
    assert letter.revoked_at is None  # list delegacji nie jest unieważniany sam


def test_supersede_skips_letter_revoked_concurrently(iqo, leader, students, officer, event, monkeypatch):
    """L5: list unieważniony w międzyczasie (inny oficer) nie wywraca wystawienia nowego listu."""
    member = with_passport(leader, students[0])
    old = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    services.save_member(member, {"passport_number": "NEW999"}, actor=leader.user)
    fresh = services.member_for_leader(leader, member.pk)
    stale = letters.letters_to_supersede(fresh)
    assert [letter.pk for letter in stale] == [old.pk]
    letters.revoke_letter(old, reason="ręcznie", actor=officer)
    # Wyścig: lista „do unieważnienia” policzona przed cudzym unieważnieniem.
    monkeypatch.setattr(letters, "letters_to_supersede", lambda member, exclude_pk=None: stale)
    new = letters.issue_letter(iqo, leader.delegation, member=fresh, actor=officer)
    assert new.superseded == [] and new.revoked_at is None


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


@pytest.mark.parametrize("language", ["es", "pt", "ru", "id", "pl"])
def test_pdf_renders_in_every_letter_language(iqo, leader, students, officer, event, language):
    """L9: każda wersja językowa składa się (krój ma znaki, daty w formacie języka)."""
    iqo.interface_languages = ["en", language]
    iqo.save(update_fields=["interface_languages"])
    member = with_passport(leader, students[0])
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer, language=language)
    text = pdf_text(letters.letter_pdf(letter))
    assert LETTER_TEXTS[language]["title"] in text
    assert letter.number in text and "ANNA ADULT" in text
    # Organizator jest dopowiedzeniem, nie dopełnieniem – zdanie nie zależy od przypadka nazwy (L7).
    if language in ("pl", "ru"):
        assert "{organizer}" in LETTER_TEXTS[language]["statement"].split("–")[1]


def test_db_template_forces_english(iqo, leader, students, officer, event, monkeypatch):
    """L7: przy własnym szablonie listu wybór języka znika, a list wychodzi po angielsku."""
    iqo.interface_languages = ["en", "fr"]
    iqo.save(update_fields=["interface_languages"])
    monkeypatch.setattr(letters, "has_db_template", lambda competition: True)
    assert [code for code, _name in letter_languages(iqo)] == ["en"]
    member = with_passport(leader, students[0])
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer, language="fr")
    assert letter.language == "en"
    with pytest.raises(DomainError):
        letter_requests.request_letters(leader, [member.pk], language="fr")


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


def issued(iqo, leader, students, officer):
    member = with_passport(leader, students[0])
    return letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)


def test_verify_unknown_code_and_form_renders_result_directly(
    client_for, iqo, leader, students, officer, event
):
    letter = issued(iqo, leader, students, officer)
    client = client_for(iqo)
    response = client.get(reverse("web:visa-verify-code", args=["ZZZZZZZZZZZZ"]))
    assert response.status_code == 200 and verification.verify(iqo, "ZZZZZZZZZZZZ") is None

    # L2: formularz pokazuje wynik od razu – bez przekierowania (jedno miejsce w limicie żądań).
    direct = client.get(reverse("web:visa-verify") + f"?code={letter.display_code.lower()}")
    assert direct.status_code == 200 and letter.number in direct.content.decode()


def test_verify_is_scoped_to_the_competition_of_the_request(
    client_for, iqo, other_competition, leader, students, officer, event
):
    letter = issued(iqo, leader, students, officer)
    assert verification.verify(other_competition, letter.verification_code) is None
    assert verification.verify(iqo, letter.verification_code) is not None
    # Drugi konkurs nie wystawił listów – strony weryfikacji w nim nie ma (M3), także dla cudzego kodu.
    response = client_for(other_competition).get(
        reverse("web:visa-verify-code", args=[letter.verification_code])
    )
    assert response.status_code == 404


def test_verify_page_is_404_without_any_letters(client_for, competition):
    from apps.competitions.tests.factories import CurrentEditionFactory

    CurrentEditionFactory(competition=competition)
    response = client_for(competition).get(reverse("web:visa-verify-code", args=["ABCDEFGHJKMN"]))
    assert response.status_code == 404
    assert client_for(competition).get(reverse("web:visa-verify")).status_code == 404


def test_verify_works_after_logistics_flag_is_switched_off(client_for, iqo, leader, students, officer, event):
    """M3: list leży w konsulacie dłużej niż trwa logistyka finału – weryfikacja zostaje."""
    from apps.delegation_logistics.models import FLAG
    from apps.tenancy.models import RegistrationMode

    letter = issued(iqo, leader, students, officer)
    iqo.feature_flags = {**iqo.feature_flags, FLAG: False}
    iqo.registration_mode = RegistrationMode.OPEN
    iqo.save(update_fields=["feature_flags", "registration_mode"])

    response = client_for(iqo).get(reverse("web:visa-verify-code", args=[letter.verification_code]))
    assert response.status_code == 200 and letter.number in response.content.decode()


def _rates(**rates) -> dict:
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


def test_verify_page_is_rate_limited_for_get_and_head(client_for, iqo, leader, students, officer, event):
    from django.core.cache import cache

    issued(iqo, leader, students, officer)
    cache.clear()
    client = client_for(iqo)
    url = reverse("web:visa-verify-code", args=["ABCDEFGHJKMN"])
    with override_settings(REST_FRAMEWORK=_rates(visa_verify="2/hour")):
        assert client.get(url).status_code == 200
        assert client.head(url).status_code == 200
        assert client.head(url).status_code == 429  # L1: HEAD liczy się tak samo jak GET
        assert client.get(url).status_code == 429


def test_officer_is_not_rate_limited_on_verify_links(client_for, iqo, leader, students, officer, event):
    from django.core.cache import cache

    letter = issued(iqo, leader, students, officer)
    cache.clear()
    client = logged_in(client_for, iqo, officer)
    url = reverse("web:visa-verify-code", args=[letter.verification_code])
    with override_settings(REST_FRAMEWORK=_rates(visa_verify="1/hour")):
        assert [client.get(url).status_code for _ in range(3)] == [200, 200, 200]


# --- adres weryfikacji zapamiętany na liście (M4) ----------------------------------------------------


def test_qr_address_is_stored_at_issue_and_survives_domain_change(iqo, leader, students, officer, event):
    letter = issued(iqo, leader, students, officer)
    assert letter.verification_base_url == "https://kwantowa.invalid/visa/verify/"
    assert (
        letters.verification_url(letter)
        == f"https://kwantowa.invalid/visa/verify/{letter.verification_code}/"
    )

    iqo.primary_domain = "iqo-nowa.test"
    iqo.save(update_fields=["primary_domain"])
    letter = InvitationLetter.objects.get(pk=letter.pk)
    assert letters.verification_url(letter).startswith("https://kwantowa.invalid/visa/verify/")
    assert letters.current_verification_url(letter).startswith("https://iqo-nowa.test/visa/verify/")
    assert "kwantowa.invalid/visa/verify/" in pdf_text(letters.letter_pdf(letter))


def test_verification_base_for_path_routed_competition(other_competition):
    from apps.tenancy.models import RoutingMode

    other_competition.routing_mode = RoutingMode.PATH
    other_competition.path_prefix = "druga"
    other_competition.save(update_fields=["routing_mode", "path_prefix"])
    assert letters.verification_entry_url(other_competition).endswith("/druga/visa/verify/")


def test_moved_domain_redirects_to_the_current_address(
    client_for, iqo, other_competition, leader, students, officer, event
):
    letter = issued(iqo, leader, students, officer)
    # List wystawiony, gdy konkurs stał pod domeną, która dziś prowadzi do innego konkursu.
    InvitationLetter.objects.filter(pk=letter.pk).update(
        verification_base_url="https://inny.test/visa/verify/"
    )
    response = client_for(other_competition).get(
        reverse("web:visa-verify-code", args=[letter.verification_code])
    )
    assert response.status_code == 302
    assert response["Location"] == letters.current_verification_url(letter)


def test_redirect_command_covers_old_path_prefix(client_for, iqo, leader, students, officer, event):
    from django.core.management import call_command

    letter = issued(iqo, leader, students, officer)
    InvitationLetter.objects.filter(pk=letter.pk).update(
        verification_base_url="https://kwantowa.invalid/stary/visa/verify/"
    )
    call_command("visa_letter_redirects", iqo.slug, stdout=StringIO())
    response = client_for(iqo).get(f"/stary/visa/verify/{letter.verification_code}/")
    assert response.status_code == 302
    assert response["Location"] == letters.current_verification_url(letter)


# --- bez analityki na stronach weryfikacji (M5) -----------------------------------------------------


def test_verify_pages_do_not_load_analytics(client_for, iqo, leader, students, officer, event):
    from apps.cms.models import SiteSettings

    letter = issued(iqo, leader, students, officer)
    site_settings = SiteSettings.for_site(iqo.site)
    site_settings.ga_measurement_id = "G-TEST12345"
    site_settings.save()
    client = client_for(iqo)

    assert "googletagmanager" in client.get(reverse("web:login")).content.decode()
    visa = client.get(reverse("web:visa-verify-code", args=[letter.verification_code])).content.decode()
    diploma = client.get(reverse("web:certificate-verify", args=["ABCDEFGHJKMN"])).content.decode()
    assert "googletagmanager" not in visa and "G-TEST12345" not in visa
    assert "googletagmanager" not in diploma and "G-TEST12345" not in diploma


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


def test_guest_removal_revokes_personal_letters(iqo, leader, students, officer, event):
    """M2: osoba wypisana z delegacji nie jedzie – jej listy imienne przestają być ważne."""
    guest = services.add_guest(
        leader.delegation, first_name="Olga", last_name="Observer", role="OBSERVER", actor=leader.user
    )
    services.save_member(guest, {**PASSPORT, "passport_name": "OLGA OBSERVER"}, actor=leader.user)
    letter = letters.issue_letter(iqo, leader.delegation, member=guest, actor=officer)

    services.remove_guest(services.member_for_leader(leader, guest.pk), actor=leader.user)
    letter.refresh_from_db()
    assert letter.revoked_at is not None and letter.revoke_reason == services.REMOVED_FROM_DELEGATION
    result = verification.verify(iqo, letter.verification_code)
    assert result["valid"] is False and result["people"] == []


def test_unlinked_student_letter_is_revoked_at_sync(iqo, leader, students, officer, event):
    adult = students[0]
    member = with_passport(leader, adult)
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    type(adult).objects.filter(pk=adult.pk).update(delegation=None)  # wypisanie (DEL-01)

    services.members_of(leader.delegation)  # synchronizacja przy wejściu na ekran
    letter.refresh_from_db()
    assert letter.revoked_at is not None and letter.revoke_reason == services.REMOVED_FROM_DELEGATION


def test_erasure_before_event_end_makes_letter_invalid(iqo, leader, students, officer, event):
    """M2: dane wyczyszczone przed końcem wydarzenia (usunięcie konta) – list nie jest już ważny."""
    from apps.accounts.profile import _erase_account

    letter = issued(iqo, leader, students, officer)
    _erase_account(students[0].user, actor=officer)
    result = verification.verify(iqo, letter.verification_code)
    assert result["valid"] is False and result["withdrawn"] is True


def test_retention_clears_revoke_reasons(iqo, leader, students, officer, event):
    """L3: powód unieważnienia (wolny tekst) znika razem z danymi osób."""
    letter = issued(iqo, leader, students, officer)
    letter_requests.revoke(iqo, letter, reason="odmowa wizy", actor=officer)
    event.starts_on = timezone.localdate() - timedelta(days=50)
    event.ends_on = timezone.localdate() - timedelta(days=40)
    event.save()
    privacy.purge_expired()
    letter.refresh_from_db()
    assert letter.revoke_reason == "" and letter.revoked_at is not None
    # Wyczyszczone po końcu wydarzenia: list był ważny, strona mówi tylko o usunięciu danych.
    assert verification.verify(iqo, letter.verification_code)["withdrawn"] is False


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
VISA_MIGRATION = "0003_visa_letter_workflow"
