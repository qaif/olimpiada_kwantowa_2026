"""Pomiar czasu pracy nad recenzją: sufit doliczenia, sumowanie i opis po polsku."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.grading.models import ReviewWorkLog
from apps.grading.tests.conftest import locked_submission
from apps.grading.tests.factories import ReviewFactory
from apps.grading.worklog import (
    MAX_HEARTBEAT_GAP,
    format_duration,
    heartbeat,
    review_seconds,
    reviewer_seconds,
)

pytestmark = pytest.mark.django_db


def _review(stage, reviewer=None):
    return ReviewFactory(submission=locked_submission(stage), reviewer=reviewer or ActiveReviewerFactory())


def test_pierwszy_sygnal_zaklada_licznik_i_nic_nie_dolicza(stage):
    """Nie wiemy jeszcze, od kiedy trwa praca – przyjęcie czegokolwiek byłoby zgadywaniem."""
    review = _review(stage)

    log = heartbeat(review)

    assert log.seconds == 0
    assert ReviewWorkLog.objects.count() == 1


def test_kolejny_sygnal_dolicza_odstep(stage):
    review = _review(stage)
    start = timezone.now()
    heartbeat(review, now=start)

    log = heartbeat(review, now=start + timedelta(seconds=60))

    assert log.seconds == 60


def test_odstep_jest_przycinany_do_sufitu(stage):
    """Karta zostawiona otwarta na noc nie może dopisać ośmiu godzin pracy."""
    review = _review(stage)
    start = timezone.now()
    heartbeat(review, now=start)

    log = heartbeat(review, now=start + timedelta(hours=8))

    assert log.seconds == MAX_HEARTBEAT_GAP


def test_sygnal_z_przeszlosci_nie_odejmuje_czasu(stage):
    """Przestawiony zegar albo spóźniony ``sendBeacon`` – licznik ma rosnąć albo stać."""
    review = _review(stage)
    start = timezone.now()
    heartbeat(review, now=start)
    heartbeat(review, now=start + timedelta(seconds=60))

    log = heartbeat(review, now=start + timedelta(seconds=10))

    assert log.seconds == 60


def test_recenzja_bez_licznika_ma_zero_sekund(stage):
    review = _review(stage)

    assert review_seconds(review) == 0


def test_suma_na_recenzenta_obejmuje_wszystkie_jego_recenzje_w_etapie(stage):
    reviewer = ActiveReviewerFactory()
    start = timezone.now()
    for _ in range(2):
        review = _review(stage, reviewer=reviewer)
        heartbeat(review, now=start)
        heartbeat(review, now=start + timedelta(seconds=30))

    assert reviewer_seconds(stage)[reviewer.pk] == 60


def test_suma_nie_miesza_etapow(stage, district_stage):
    reviewer = ActiveReviewerFactory()
    start = timezone.now()
    other = ReviewFactory(submission=locked_submission(district_stage), reviewer=reviewer)
    heartbeat(other, now=start)
    heartbeat(other, now=start + timedelta(seconds=45))

    assert reviewer_seconds(stage) == {}
    assert reviewer_seconds(district_stage)[reviewer.pk] == 45


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "poniżej minuty"),
        (59, "poniżej minuty"),
        (60, "1 min"),
        (12 * 60, "12 min"),
        (3600, "1 h"),
        (72 * 60, "1 h 12 min"),
    ],
)
def test_opis_czasu_po_polsku(seconds, expected):
    assert format_duration(seconds) == expected
