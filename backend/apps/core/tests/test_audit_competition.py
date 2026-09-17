"""Wydanie D: wpis audytowy wie, którego konkursu dotyczy (§ 3.9).

Dotąd zakres audytu wychodził **z obiektu**, o którym wpis mówi – i była to droga jednostronna:
dawało się nią stwierdzić, że wpis należy do sąsiada, ale nie dawało się stwierdzić, że należy
do nas. Wpis o obiekcie skasowanym zostawał więc widoczny wszędzie, a przeglądarka audytu płaciła
po jednym podzapytaniu na każdy rodzaj obiektu obecny w tabeli.

Kolumna zamyka to od strony zapisu, ale **nie kosztem widoczności**: wpisy platformowe (konto,
witryna, alert infrastruktury) nadal mają być widoczne dla koordynatora, bo czyta je od zawsze
(§ 0). Stąd dwie metody i dwie różne odpowiedzi – ``for_competition`` i ``visible_to`` – i to jest
główna rzecz sprawdzana w tym pliku.
"""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.core.models import AuditLog, audit

pytestmark = pytest.mark.django_db


def entry_for(competition, **kwargs) -> AuditLog:
    """Wpis należący wprost do wskazanego konkursu (albo do nikogo, gdy ``None``)."""
    values = {"action": "stage.closed", "target_type": "competitions.stage", "target_id": "1"}
    values.update(kwargs)
    return AuditLog.objects.create(competition=competition, **values)


def test_audit_takes_the_competition_from_the_context(as_competition, other_competition, competition):
    """Konkurs wpisu bierze się z kontekstu przebiegu – w żądaniu ustawia go warstwa."""
    with as_competition(other_competition):
        written = audit(None, "stage.closed", competition)

    assert written.competition_id == other_competition.pk


def test_audit_prefers_the_competition_of_the_request(rf, as_competition, competition, other_competition):
    """Żądanie wygrywa z kontekstem: to ono mówi, pod czyim adresem zdarzenie zaszło.

    Rozjazd między jednym a drugim jest w praktyce niemożliwy (warstwa ustawia oba naraz), ale
    kolejność musi być zapisana w jednym miejscu i sprawdzona – inaczej pierwszy kod wołający
    ``audit`` z własnym ``request`` w cudzym kontekście zapisałby zdarzenie nie tam, gdzie zaszło.
    """
    request = rf.get("/")
    request.competition = competition

    with as_competition(other_competition):
        written = audit(None, "stage.closed", competition, request=request)

    assert written.competition_id == competition.pk


def test_platform_events_have_no_competition(unbound_competition, db):  # noqa: ARG001
    """Zdarzenie zapisane bez konkursu w kontekście zostaje bez konkursu – i to jest poprawne."""
    user = CoordinatorFactory()

    written = audit(None, "account.created", user)

    assert written.competition_id is None


def test_for_competition_is_strict(competition, other_competition):
    """``for_competition`` oddaje wyłącznie wpisy jednego konkursu – bez platformowych."""
    ours = entry_for(competition)
    theirs = entry_for(other_competition)
    platform = entry_for(None, action="account.created", target_type="accounts.user")

    rows = list(AuditLog.objects.for_competition(competition))

    assert ours in rows
    assert theirs not in rows
    assert platform not in rows


def test_visible_to_adds_platform_entries_but_not_foreign_ones(competition, other_competition):
    """Przeglądarka koordynatora: własne wpisy **plus** platformowe, nigdy cudze.

    Wpisy platformowe zostają widoczne, bo koordynator Olimpiady Kwantowej czyta je od zawsze –
    schowanie ich byłoby zmianą, której nikt nie zamawiał (§ 0). Cudze nie zostają, bo istnienie
    cudzego etapu nie jest jego informacją (§ 3.6).
    """
    ours = entry_for(competition)
    theirs = entry_for(other_competition)
    platform = entry_for(None, action="account.created", target_type="accounts.user")

    rows = list(AuditLog.objects.visible_to(competition))

    assert ours in rows
    assert platform in rows
    assert theirs not in rows


def test_visible_to_without_a_competition_shows_nothing(competition, other_competition):
    """„Nie wiadomo, o który konkurs chodzi” nie może znaczyć „wszystkie” – domyślnie zamknięte."""
    entry_for(competition)
    entry_for(None, action="account.created", target_type="accounts.user")

    assert list(AuditLog.objects.visible_to(None)) == []
