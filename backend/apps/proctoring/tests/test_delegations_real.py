"""Izolacja opiekuna drużyny na **prawdziwych** modelach DEL-01 (bez podstawionego adaptera).

Konkurs przestawiony na delegacje, dwóch opiekunów z dwóch krajów, po jednym uczniu: opiekun dostaje
token wyłącznie do pokoju swojej delegacji, cudzy uczeń to 404, a odwołanie opiekuna (DEL-01
``remove_leader``) wyprasza go z pokoju (sygnał) i zabiera zakres.
"""

from __future__ import annotations

from datetime import date

import jwt
import pytest
from django.utils import timezone

from apps.accounts import delegation_services
from apps.accounts.tests.factories import CoordinatorFactory
from apps.accounts.tests.test_delegations import add, leader_for_country, make_delegations_competition
from apps.competitions.services import current_edition
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.core.api import DomainError
from apps.proctoring import services
from apps.proctoring.models import ProctoringConfig, ProctoringSession, ProctorKind

from .fake_livekit import API_SECRET

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(competition):
    iqo = make_delegations_competition(competition)
    iqo.feature_flags = {**(iqo.feature_flags or {}), "proctoring": True}
    iqo.save(update_fields=["feature_flags"])
    coordinator = CoordinatorFactory()
    from apps.accounts.models import CompetitionRole
    from apps.tenancy.tests.factories import grant_membership

    grant_membership(coordinator, iqo, CompetitionRole.COORDINATOR)
    stage = StageFactory(edition=current_edition(iqo), competition=iqo)
    config = ProctoringConfig.objects.create(stage=stage, enabled=True)
    de = leader_for_country(iqo, coordinator, "de@example.test", country="de")
    fr = leader_for_country(iqo, coordinator, "fr@example.test", country="fr")
    kurt = add(de, email="kurt@example.test", birth_date=date(1990, 1, 1))  # pełnoletni – bez zgody opiekuna
    zoe = add(fr, email="zoe@example.test", first_name="Zoé", last_name="Martin")
    for participant in (kurt, zoe):
        StageEntryFactory(participant=participant, stage=stage, competition=iqo)
    services.add_assignment(stage, coordinator, de.user, ProctorKind.LEADER)
    services.ensure_sessions(stage)
    return {
        "iqo": iqo,
        "stage": stage,
        "config": config,
        "de": de,
        "fr": fr,
        "kurt": kurt,
        "zoe": zoe,
        "coord": coordinator,
    }


def test_real_leader_sees_only_own_delegation_room(world, fake_livekit):
    stage, config, de = world["stage"], world["config"], world["de"]
    kurt_session = ProctoringSession.objects.get(participant=world["kurt"])
    zoe_session = ProctoringSession.objects.get(participant=world["zoe"])
    assert kurt_session.group == f"d{de.delegation_id}"
    assert zoe_session.group == f"d{world['fr'].delegation_id}"

    scope = services.proctor_scope(de.user, stage)
    assert scope.kind == ProctorKind.LEADER and scope.delegation_id == de.delegation_id
    token = services.proctor_token(scope, kurt_session.group)["token"]
    claims = jwt.decode(token, API_SECRET, algorithms=["HS256"])
    assert claims["video"]["room"] == config.room_name(kurt_session.group)
    with pytest.raises(DomainError):
        services.proctor_token(scope, zoe_session.group)
    with pytest.raises(DomainError):
        services.session_in_scope(scope, zoe_session.pk)
    assert [item["id"] for item in services.roster(scope, group=kurt_session.group)["items"]] == [
        kurt_session.pk
    ]


def test_real_leader_cannot_be_assigned_to_another_country(world):
    zoe_session = ProctoringSession.objects.get(participant=world["zoe"])
    assignment = services.proctor_scope(world["de"].user, world["stage"]).assignment
    with pytest.raises(DomainError):
        services.reassign(world["stage"], zoe_session.pk, assignment.pk, world["coord"])


def test_removed_leader_is_kicked_and_loses_scope(world, fake_livekit, django_capture_on_commit_callbacks):
    de, stage, config = world["de"], world["stage"], world["config"]
    group = f"d{de.delegation_id}"
    identity = services.proctor_identity(stage, de.user)
    fake_livekit.publish(config.room_name(group), identity)
    with django_capture_on_commit_callbacks(execute=True):
        delegation_services.remove_leader(de, actor=world["coord"])
    assert {"room": config.room_name(group), "identity": identity} in [
        payload for name, payload in fake_livekit.calls if name == "RemoveParticipant"
    ]
    de.user.refresh_from_db()
    assert services.proctor_scope(de.user, stage) is None


def test_real_student_token_room_is_delegation_room(world, fake_livekit):
    kurt, config, stage = world["kurt"], world["config"], world["stage"]
    session = services.session_for(stage, kurt)
    services.give_consent(session, user=kurt.user)
    session.check_passed_at = timezone.now()
    session.save()
    data = services.student_token(session, config, user=kurt.user)
    claims = jwt.decode(data["token"], API_SECRET, algorithms=["HS256"])
    assert claims["video"]["room"] == config.room_name(f"d{world['de'].delegation_id}")
