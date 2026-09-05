"""T-02: serwis kodów zaproszeń, migracja grup RBAC i komenda `create_invitation`."""

from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import (
    RBAC_GROUPS,
    InvitationCode,
    InvitationGrantsStatus,
    hash_invitation_code,
)
from apps.accounts.services import create_invitation, redeem_invitation

from .factories import CoordinatorFactory, InvitationCodeFactory, UserFactory


@pytest.mark.django_db
def test_migracja_danych_tworzy_grupy_rbac():
    """Grupy `participant`, `reviewer`, `appeals`, `coordinator` istnieją po migracjach."""
    assert set(Group.objects.filter(name__in=RBAC_GROUPS).values_list("name", flat=True)) == set(RBAC_GROUPS)


@pytest.mark.django_db
def test_create_invitation_zwraca_kod_jawny_a_w_bazie_jest_tylko_hash():
    coordinator = CoordinatorFactory()

    invitation, plain_code = create_invitation(coordinator, max_uses=3, is_appeals=True)

    assert plain_code
    assert invitation.code_hash == hash_invitation_code(plain_code)
    assert not InvitationCode.objects.filter(code_hash=plain_code).exists()
    assert invitation.used_count == 0
    assert invitation.max_uses == 3
    assert invitation.is_appeals is True
    assert invitation.expires_at > timezone.now()


@pytest.mark.django_db
def test_redeem_invitation_zwieksza_used_count_o_jeden():
    invitation = InvitationCodeFactory(plain_code="kod-x", max_uses=2)

    redeemed = redeem_invitation("kod-x")

    assert redeemed.pk == invitation.pk
    invitation.refresh_from_db()
    assert invitation.used_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kwargs",
    [
        {"expires_at": timezone.now() - timedelta(seconds=1)},
        {"max_uses": 1, "used_count": 1},
    ],
)
def test_redeem_invitation_odrzuca_kod_wygasly_lub_zuzyty(kwargs):
    invitation = InvitationCodeFactory(plain_code="kod-x", **kwargs)

    with pytest.raises(Exception) as exc_info:
        redeem_invitation("kod-x")

    assert getattr(exc_info.value, "machine_code", None) == "INVALID_INVITATION"
    invitation.refresh_from_db()
    assert invitation.used_count == kwargs.get("used_count", 0)


@pytest.mark.django_db
def test_komenda_create_invitation_wypisuje_kod_ktorego_nie_ma_w_bazie():
    coordinator = CoordinatorFactory(email="koordynator@example.test")
    out = StringIO()

    call_command("create_invitation", "--email", "koordynator@example.test", "--pending", stdout=out)

    output = out.getvalue()
    invitation = InvitationCode.objects.get(created_by=coordinator)
    assert invitation.grants_status == InvitationGrantsStatus.PENDING
    plain_code = output.strip().split(": ")[1].split("\n")[0]
    assert hash_invitation_code(plain_code) == invitation.code_hash
    assert plain_code not in invitation.code_hash


@pytest.mark.django_db
def test_komenda_create_invitation_odmawia_uzytkownikowi_bez_roli_koordynatora():
    UserFactory(email="ktos@example.test")

    with pytest.raises(CommandError):
        call_command("create_invitation", "--email", "ktos@example.test", stdout=StringIO())

    assert InvitationCode.objects.count() == 0
