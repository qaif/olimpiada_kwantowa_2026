"""Adapter delegacji krajowych (DEL-01) – jedyne miejsce, w którym nadzór pyta o kraj ucznia.

Delegacje (``apps.accounts.delegations``, ``Participant.delegation``, ``delegation_services.leader_for``)
powstają na osobnej gałęzi. Nadzór ma działać i bez nich – wtedy nie ma delegacji (wszyscy uczniowie
w grupie ``m``) i nie ma opiekunów drużyn – a z nimi ma izolować opiekunów **pokojem LiveKit**
(``docs/tasks/PROC-01.md`` § 3). Dlatego żadnego importu modeli DEL-01 na poziomie modułu: każda
funkcja sprawdza, czy jest czego zapytać, i odpowiada „brak delegacji”, gdy nie ma.

Testy podmieniają te trzy funkcje (``monkeypatch``), a po scaleniu z DEL-01 test z prawdziwymi
modelami włącza się sam (``pytest.importorskip``).
"""

from __future__ import annotations

from django.apps import apps


def available() -> bool:
    """Czy instalacja ma modele delegacji (DEL-01)."""
    try:
        apps.get_model("accounts", "DelegationLeader")
    except LookupError:
        return False
    return True


def participant_delegation_id(participant) -> int | None:
    """Delegacja, która zgłosiła ucznia, albo ``None`` (otwarta rejestracja, brak DEL-01)."""
    value = getattr(participant, "delegation_id", None)
    return int(value) if value else None


def leader_delegation_id(user, competition, stage=None) -> int | None:
    """Delegacja, którą ta osoba **dziś** prowadzi w edycji etapu, albo ``None``.

    Reguła DEL-01: rola ``team_leader`` sama nie wystarcza – liczy się aktywny wiersz opiekuna
    w bieżącej edycji (``leader_for``). Odwołany opiekun traci nadzór w chwili odwołania, bo ta
    funkcja jest wołana przy każdym tokenie i każdej czynności.
    """
    if not available() or user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        from apps.accounts.delegation_services import leader_for
    except ImportError:  # pragma: no cover - modele bez serwisu: brak opiekunów
        return None
    leader = leader_for(user, competition)
    if leader is None:
        return None
    if stage is not None and getattr(leader, "edition_id", None) not in (None, stage.edition_id):
        return None
    return int(leader.delegation_id)


def delegation_label(delegation_id: int | None) -> str:
    """Nazwa kraju delegacji do listy grup (bez DEL-01 – numer)."""
    if not delegation_id:
        return ""
    if available():
        Delegation = apps.get_model("accounts", "Delegation")  # noqa: N806
        row = Delegation._base_manager.select_related("country").filter(pk=delegation_id).first()
        if row is not None:
            return str(row.country)
    return f"#{delegation_id}"
