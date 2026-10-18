"""Bez bazy: reguła progu i zbieranie identyfikatorów w transakcji (na atrapie połączenia).

Testy z bazą (``test_triggers.py``) sprawdzają to samo na prawdziwym ``on_commit``; tutaj pilnujemy
samej mechaniki: jeden callback na transakcję, nowy zbiór po wycofanym punkcie zapisu, autocommit
bez pośrednika.
"""

from __future__ import annotations

import pytest

from apps.workshop_certificates import triggers
from apps.workshop_certificates.rules import meets_threshold


class FakeConnection:
    def __init__(self, in_atomic_block: bool = True):
        self.in_atomic_block = in_atomic_block
        self.run_on_commit: list = []

    def commit(self) -> None:
        callbacks, self.run_on_commit = self.run_on_commit, []
        for _sids, callback, _robust in callbacks:
            callback()


@pytest.fixture
def world(monkeypatch):
    connection = FakeConnection()
    queued: list[list[int]] = []
    monkeypatch.setattr(triggers.transaction, "get_connection", lambda using=None: connection)
    monkeypatch.setattr(
        triggers.transaction,
        "on_commit",
        lambda func, using=None, robust=False: connection.run_on_commit.append(((), func, robust)),
    )
    monkeypatch.setattr(triggers, "enqueue", queued.append)
    return connection, queued


def test_many_writes_in_one_transaction_make_one_callback(world):
    connection, queued = world

    triggers.schedule([3, 1])
    triggers.schedule([2, 3])
    triggers.schedule([])

    assert len(connection.run_on_commit) == 1
    assert connection.run_on_commit[0][2] is True  # robust: broker nie wywraca zapisu
    connection.commit()
    assert queued == [[1, 2, 3]]


def test_after_commit_the_next_transaction_starts_a_new_batch(world):
    connection, queued = world

    triggers.schedule([1])
    connection.commit()
    triggers.schedule([2])
    connection.commit()

    assert queued == [[1], [2]]


def test_rolled_back_callback_does_not_swallow_later_writes(world):
    connection, queued = world

    triggers.schedule([1])
    connection.run_on_commit.clear()  # wycofany punkt zapisu zabiera swój callback
    triggers.schedule([2])
    connection.commit()

    assert queued == [[2]]


def test_autocommit_enqueues_at_once(world):
    connection, queued = world
    connection.in_atomic_block = False

    triggers.schedule([5, 4])

    assert queued == [[4, 5]]
    assert connection.run_on_commit == []


def test_meets_threshold_is_at_least_n():
    assert not meets_threshold(2, 3)
    assert meets_threshold(3, 3)
    assert meets_threshold(4, 3)
    # Próg zero albo ujemny nie znaczy „wszyscy” – formularz go nie przepuści, a reguła nie zgadnie.
    assert not meets_threshold(5, 0)
