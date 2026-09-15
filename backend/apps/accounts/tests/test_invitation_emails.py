"""Zaproszenia do komitetu wysyłane e-mailem: kod na adres, ponowienie, unieważnienie.

Czego te testy pilnują, po kolei:

- **jeden adres = jeden kod.** Kod wspólny dla listy osób nie dałby się unieważnić pojedynczo
  i nie powiedziałby po fakcie, kto z niego skorzystał,
- **kod jawny istnieje wyłącznie w liście.** W bazie jest sha256, w audycie nie ma go wcale –
  to jedyny powód, dla którego „wyślij ponownie” znaczy „wystaw nowy”,
- **list wychodzi dopiero po commicie.** Worker nie może dostać kodu do zaproszenia, którego
  w bazie ostatecznie nie ma,
- **unieważniony kod nie rejestruje konta.** To jedyny moment, w którym da się jeszcze zatrzymać
  zaproszenie wysłane pod zły adres.
"""

import json
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.models import InvitationCode, InvitationGrantsStatus, InvitationStatus
from apps.accounts.services import (
    INVITATION_SUBJECT,
    MAX_INVITATION_EMAILS,
    parse_email_list,
    redeem_invitation,
    register_committee,
    resend_invitation,
    revoke_invitation,
    send_invitations,
)
from apps.core.api import DomainError
from apps.core.models import AuditLog

from .factories import CommitteeMemberFactory, CoordinatorFactory, InvitationCodeFactory, UserFactory

pytestmark = pytest.mark.django_db

PASSWORD = "Poprawne-Haslo-2026"


def plain_code_from(message) -> str:
    """Wyciąga kod jawny z treści listu – jedynego miejsca, w którym w ogóle istnieje."""
    line = next(row for row in message.body.splitlines() if row.startswith("Twój kod zaproszenia:"))
    return line.split(": ", 1)[1].strip()


def test_kazdy_adres_dostaje_wlasny_jednorazowy_kod(django_capture_on_commit_callbacks):
    coordinator = CoordinatorFactory()

    with django_capture_on_commit_callbacks(execute=True):
        result = send_invitations(
            coordinator,
            ["Ala@example.test", "bob@example.test"],
            district="mazowieckie",
        )

    assert result["sent_count"] == 2
    assert result["skipped"] == []
    codes = list(InvitationCode.objects.all())
    assert len(codes) == 2
    assert {code.email for code in codes} == {"ala@example.test", "bob@example.test"}
    # Osobny kod, nie ten sam rozesłany do dwóch osób.
    assert len({code.code_hash for code in codes}) == 2
    assert all(code.max_uses == 1 and code.sent_at is not None for code in codes)
    assert all(code.district == "mazowieckie" for code in codes)


def test_list_wychodzi_dopiero_po_commicie_i_niesie_kod_oraz_link(django_capture_on_commit_callbacks):
    coordinator = CoordinatorFactory()

    with django_capture_on_commit_callbacks(execute=True):
        send_invitations(coordinator, ["ala@example.test"], note="Piszemy po rozmowie na konferencji.")
        # W środku bloku transakcja jeszcze nie jest zatwierdzona – list nie ma prawa wyjść.
        assert mail.outbox == []

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == INVITATION_SUBJECT
    assert message.to == ["ala@example.test"]
    assert "/register/committee/" in message.body
    assert "Piszemy po rozmowie na konferencji." in message.body
    assert "jednorazowy i osobisty" in message.body
    invitation = InvitationCode.objects.get()
    # Kod z listu otwiera dokładnie ten wiersz w bazie – i nigdzie indziej go nie ma.
    assert redeem_invitation(plain_code_from(message)).pk == invitation.pk


def test_adres_z_kontem_komisji_jest_pomijany(django_capture_on_commit_callbacks):
    coordinator = CoordinatorFactory()
    member = CommitteeMemberFactory(user=UserFactory(email="recenzent@example.test"))

    with django_capture_on_commit_callbacks(execute=True):
        result = send_invitations(coordinator, [member.user.email, "nowy@example.test"])

    assert result["sent"] == ["nowy@example.test"]
    assert result["skipped"] == [{"email": "recenzent@example.test", "reason": "ma już konto komisji"}]
    assert mail.outbox[0].to == ["nowy@example.test"]
    assert not InvitationCode.objects.filter(email="recenzent@example.test").exists()


def test_audyt_zapisuje_adres_ale_nigdy_kodu(django_capture_on_commit_callbacks):
    coordinator = CoordinatorFactory()

    with django_capture_on_commit_callbacks(execute=True):
        send_invitations(coordinator, ["ala@example.test"], is_appeals=True)

    entry = AuditLog.objects.get(action="invitation.sent")
    assert entry.diff["email"] == "ala@example.test"
    assert entry.diff["is_appeals"] is True
    written = json.dumps(entry.diff, ensure_ascii=False)
    assert plain_code_from(mail.outbox[0]) not in written
    assert "Twój kod" not in written


def test_pusta_lista_i_przekroczony_limit_sa_odrzucane():
    coordinator = CoordinatorFactory()

    with pytest.raises(DomainError) as empty:
        send_invitations(coordinator, [])
    with pytest.raises(DomainError) as too_many:
        send_invitations(coordinator, [f"os{n}@example.test" for n in range(MAX_INVITATION_EMAILS + 1)])

    assert empty.value.machine_code == "NO_RECIPIENTS"
    assert too_many.value.machine_code == "TOO_MANY_RECIPIENTS"
    assert InvitationCode.objects.count() == 0


def test_ponowienie_uniewaznia_stary_kod_i_wysyla_nowy(django_capture_on_commit_callbacks):
    coordinator = CoordinatorFactory()
    with django_capture_on_commit_callbacks(execute=True):
        send_invitations(coordinator, ["ala@example.test"], district="podlaskie", is_appeals=True)
    old = InvitationCode.objects.get()
    old_plain = plain_code_from(mail.outbox[0])
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        fresh = resend_invitation(old, actor=coordinator)

    old.refresh_from_db()
    assert old.revoked_at is not None
    assert old.status() == InvitationStatus.REVOKED
    # Parametry zaproszenia przechodzą na nowy kod, adresat zostaje ten sam.
    assert (fresh.email, fresh.district, fresh.is_appeals) == ("ala@example.test", "podlaskie", True)
    assert fresh.code_hash != old.code_hash
    assert len(mail.outbox) == 1
    with pytest.raises(DomainError) as exc_info:
        redeem_invitation(old_plain)
    assert exc_info.value.machine_code == "INVALID_INVITATION"
    assert redeem_invitation(plain_code_from(mail.outbox[0])).pk == fresh.pk


def test_ponowienie_zachowuje_dlugosc_waznosci(django_capture_on_commit_callbacks):
    coordinator = CoordinatorFactory()
    invitation = InvitationCodeFactory(
        created_by=coordinator,
        email="ala@example.test",
        sent_at=timezone.now(),
        # Kod pierwotny miał dwa dni ważności – ponowienie ma dać tyle samo, licząc od teraz.
        created_at=timezone.now() - timedelta(days=1),
        expires_at=timezone.now() + timedelta(days=1),
    )

    with django_capture_on_commit_callbacks(execute=True):
        fresh = resend_invitation(invitation, actor=coordinator)

    original_window = invitation.expires_at - invitation.created_at
    assert abs((fresh.expires_at - fresh.created_at) - original_window).total_seconds() < 5


def test_uniewaznienie_i_ponowienie_sa_odmawiane_po_uzyciu_kodu():
    coordinator = CoordinatorFactory()
    used = InvitationCodeFactory(email="ala@example.test", sent_at=timezone.now(), used_count=1)

    with pytest.raises(DomainError) as revoke_error:
        revoke_invitation(used, actor=coordinator)
    with pytest.raises(DomainError) as resend_error:
        resend_invitation(used, actor=coordinator)

    assert revoke_error.value.machine_code == "INVITATION_USED"
    assert revoke_error.value.status_code == 409
    assert resend_error.value.machine_code == "INVITATION_USED"
    used.refresh_from_db()
    assert used.revoked_at is None


def test_uniewazniony_kod_nie_rejestruje_konta_komitetu():
    coordinator = CoordinatorFactory()
    invitation = InvitationCodeFactory(
        plain_code="kod-do-uniewaznienia", email="ala@example.test", sent_at=timezone.now()
    )

    revoke_invitation(invitation, actor=coordinator)

    with pytest.raises(DomainError) as exc_info:
        register_committee(
            email="ala@example.test",
            password=PASSWORD,
            first_name="Ala",
            last_name="Nowak",
            invitation_code="kod-do-uniewaznienia",
        )
    assert exc_info.value.machine_code == "INVALID_INVITATION"
    invitation.refresh_from_db()
    assert invitation.used_count == 0


def test_status_rozstrzyga_w_kolejnosci_uzyte_uniewaznione_wygasle_wyslane():
    now = timezone.now()
    used = InvitationCodeFactory(used_count=1, revoked_at=now)
    revoked = InvitationCodeFactory(plain_code="kod-2", revoked_at=now)
    expired = InvitationCodeFactory(plain_code="kod-3", expires_at=now - timedelta(days=1))
    pending = InvitationCodeFactory(plain_code="kod-4")

    assert used.status() == InvitationStatus.USED
    assert revoked.status() == InvitationStatus.REVOKED
    assert expired.status() == InvitationStatus.EXPIRED
    assert pending.status() == InvitationStatus.PENDING
    assert pending.status_label == "wysłane"


def test_status_pending_z_formularza_daje_kod_wymagajacy_zatwierdzenia(
    django_capture_on_commit_callbacks,
):
    coordinator = CoordinatorFactory()

    with django_capture_on_commit_callbacks(execute=True):
        send_invitations(coordinator, ["ala@example.test"], grants_status=InvitationGrantsStatus.PENDING)

    assert InvitationCode.objects.get().grants_status == InvitationGrantsStatus.PENDING


@pytest.mark.parametrize(
    "raw",
    [
        "ala@example.test\nbob@example.test",
        "ala@example.test, bob@example.test",
        "ala@example.test; bob@example.test",
        "ala@example.test   bob@example.test",
        "  ALA@example.test \n bob@Example.test \n ala@EXAMPLE.test  ",
    ],
)
def test_parser_przyjmuje_kazdy_separator_i_usuwa_powtorzenia(raw):
    valid, invalid = parse_email_list(raw)

    assert valid == ["ala@example.test", "bob@example.test"]
    assert invalid == []


def test_parser_zwraca_bledne_adresy_w_postaci_wpisanej_przez_czlowieka():
    valid, invalid = parse_email_list("ala@example.test\nbezmalpy\nbob@")

    assert valid == ["ala@example.test"]
    assert invalid == ["bezmalpy", "bob@"]
