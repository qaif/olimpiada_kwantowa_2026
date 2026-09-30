"""Wspólne kawałki testów Wiadomości – dla ``apps/chat/tests`` i ``apps/web/tests/test_chat*.py``.

Konta dostają **i** grupę, **i** członkostwo w konkursie – ta sama zasada, co w testach forum:
``has_role`` czyta jedno albo drugie zależnie od ``memberships_enforced``.
"""

from __future__ import annotations

import base64
import os
from datetime import timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.chat import services
from apps.chat.models import MIN_KDF_ITERATIONS, ChatSettings, PeerMode
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.tenancy.tests.factories import grant_membership


def participant_of(competition, first_name: str = "Ala", last_name: str = "Kowalska", **kwargs):
    """Uczestnik **tego** konkursu z rolą – i z nazwiskiem, żeby asercje o „Imię N.” miały na czym stać."""
    user = UserFactory(first_name=first_name, last_name=last_name, groups=["participant"])
    profile = ParticipantFactory(competition=competition, user=user, **kwargs)
    grant_membership(user, competition, CompetitionRole.PARTICIPANT)
    return profile


def coordinator_of(competition, **kwargs):
    user = CoordinatorFactory(**kwargs)
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


def configure(competition, *, peer_mode=PeerMode.NONE, enabled=True, e2e_enabled=False) -> ChatSettings:
    """Ustawienia Wiadomości zapisane wprost (bez audytu) – test ustawia stan, a nie go bada."""
    row, _ = ChatSettings.objects.update_or_create(
        competition=competition,
        defaults={"peer_mode": peer_mode, "enabled": enabled, "e2e_enabled": e2e_enabled},
    )
    return row


def in_directory(participant):
    return services.set_discoverable(participant, True)


def open_stage(competition):
    """Bieżąca edycja z nietreningowym etapem przyjmującym rozwiązania **teraz** (wymusza PRE)."""
    edition = CurrentEditionFactory(competition=competition)
    return StageFactory(competition=competition, edition=edition)


def closed_stage(competition):
    edition = CurrentEditionFactory(competition=competition)
    past = timezone.now() - timedelta(days=30)
    return StageFactory(
        competition=competition, edition=edition, opens_at=past, deadline_at=past + timedelta(days=1)
    )


def start(a, b, competition, body: str = "Cześć!"):
    """Rozmowa jawna między ``a`` i ``b`` zaczęta przez ``a`` (``b`` musi być w katalogu)."""
    return services.start_peer_conversation(
        user=a.user, competition=competition, token=in_directory(b).token, body=body
    )


def key_fields(curve=None) -> dict:
    """Komplet pól ``ChatKey`` w kształcie, jaki wysyła przeglądarka: SPKI P-256 i owinięta kopia.

    Klucz publiczny jest prawdziwy (``cryptography``), a „owinięta” kopia – losowymi bajtami
    o rozmiarze owiniętego PKCS8: serwer i tak nie może sprawdzić jej treści, tylko kształt.
    """
    private = ec.generate_private_key(curve or ec.SECP256R1())
    spki = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return {
        "public_key": base64.b64encode(spki).decode(),
        "wrapped_private_key": base64.b64encode(os.urandom(154)).decode(),
        "kdf_salt": base64.b64encode(os.urandom(16)).decode(),
        "wrap_iv": base64.b64encode(os.urandom(12)).decode(),
        "kdf_iterations": MIN_KDF_ITERATIONS,
    }


def give_key(participant):
    return services.save_key(user=participant.user, competition=participant.competition, **key_fields())


def fake_ciphertext(length: int = 40) -> dict:
    return {
        "ciphertext": base64.b64encode(os.urandom(length)).decode(),
        "iv": base64.b64encode(os.urandom(12)).decode(),
    }
