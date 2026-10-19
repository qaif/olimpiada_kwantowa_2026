"""Reguły przypomnień – planowanie bez bazy: szablon, terminy w czasie polskim, pominięcia, okno.

To jest warstwa, która psuje się po cichu (zmiana czasu, „N godzin przed”, literówka w polu
szablonu), więc testy wołają czyste funkcje ``apps.message_rules.planning`` z ręcznie podanymi
wierszami harmonogramu i niezapisanymi obiektami – bez bazy i bez zegara.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from apps.competitions.models import Edition, Stage, StageKind
from apps.message_rules import planning
from apps.message_rules.forms import parse_days
from apps.message_rules.models import MessageRule, RuleKind, RuleTiming

WARSAW = ZoneInfo("Europe/Warsaw")
UTC = dt.UTC


def _rule(**kwargs) -> MessageRule:
    defaults = {
        "competition_id": 1,
        "kind": RuleKind.WORKSHOP,
        "timing": RuleTiming.DAY_AT,
        "days_before": [0],
        "send_time": dt.time(6, 0),
        "subject_template": "Dziś warsztat: {temat}",
        "body_template": "{temat} ({data}, {godziny}) prowadzi {prowadzacy}. Link: {link}",
        "link": "https://meet.google.com/izu-qrvp-kuw",
        "group": "ALL_PARTICIPANTS_AND_TEACHERS",
        "parameters": {"include_past_editions": False},
    }
    return MessageRule(**{**defaults, **kwargs})


def _row(topic="Liczby zespolone (prowadzący: Tomasz Sowiński)", day=dt.date(2026, 10, 24), **kwargs) -> dict:
    row = {
        "topic": topic,
        "date": f"{day.day} października {day.year}" if day else "do potwierdzenia",
        "date_value": day,
        "time": "11:00–14:00",
        "lecturer": "",
        "key": f"{day.isoformat()}-x" if day else "",
    }
    row.update(kwargs)
    return row


# --- szablon ---------------------------------------------------------------------------------------


def test_workshop_template_is_filled_from_the_schedule_row():
    plan = planning.plan_workshops(_rule(), [_row()])

    (occurrence,) = plan.occurrences
    assert occurrence.subject == "Dziś warsztat: Liczby zespolone"
    assert occurrence.body == (
        "Liczby zespolone (24 października 2026, 11:00–14:00) prowadzi Tomasz Sowiński. "
        "Link: https://meet.google.com/izu-qrvp-kuw"
    )


def test_lecturer_column_wins_over_the_topic_suffix_and_missing_values_read_well():
    rule = _rule(body_template="{prowadzący} / {godziny}")

    with_column = planning.plan_workshops(rule, [_row(lecturer="Paweł Gora")]).occurrences[0]
    without = planning.plan_workshops(rule, [_row(topic="Algebra", time="")]).occurrences[0]

    assert with_column.body == "Paweł Gora / 11:00–14:00"
    assert without.body == "do potwierdzenia / do potwierdzenia"


def test_unknown_placeholder_and_link_without_value_are_errors():
    errors = planning.template_errors(
        RuleKind.WORKSHOP, subject="{temat}", body="{prowadzacz} {etap}", link=""
    )
    assert len(errors) == 1
    assert "{etap}" in errors[0] and "{prowadzacz}" in errors[0]
    assert "{temat}" in errors[0]  # lista dozwolonych

    errors = planning.template_errors(RuleKind.STAGE_DEADLINE, subject="Za {dni}", body="{link}", link=" ")
    assert errors == [planning.link_missing_error()]

    assert (
        planning.template_errors(RuleKind.STAGE_DEADLINE, subject="{etap}", body="{termin} {dni}", link="")
        == []
    )


def test_single_braces_stay_literal_and_subject_is_one_short_line():
    assert planning.render("qc.measure({q}) {temat", {"temat": "X"}) == "qc.measure({q}) {temat"
    subject = planning.render("{temat}\nDrugi wiersz " + "x" * 300, {"temat": "A"}, single_line=True)
    assert "\n" not in subject
    assert len(subject) == planning.SUBJECT_MAX


def test_days_phrase_and_polish_dates():
    assert planning.days_phrase(1) == "1 dzień"
    assert planning.days_phrase(7) == "7 dni"
    assert planning.polish_date(dt.date(2027, 2, 28)) == "28 lutego 2027"


def test_parse_days_accepts_commas_and_rejects_words():
    assert parse_days("30, 7,1 7") == [30, 7, 1]
    assert parse_days("tydzień") is None
    assert parse_days("") is None


# --- terminy i strefa ------------------------------------------------------------------------------


def test_same_day_at_six_is_polish_wall_clock_also_on_dst_days():
    rule = _rule()
    autumn = planning.plan_workshops(rule, [_row(day=dt.date(2026, 10, 25))]).occurrences[0]
    spring = planning.plan_workshops(rule, [_row(day=dt.date(2027, 3, 28))]).occurrences[0]
    winter = planning.plan_workshops(rule, [_row(day=dt.date(2026, 12, 5))]).occurrences[0]

    # 25.10.2026 – koniec czasu letniego (o 3:00 wraca 2:00), 28.03.2027 – początek.
    assert autumn.send_at.astimezone(UTC) == dt.datetime(2026, 10, 25, 5, 0, tzinfo=UTC)
    assert spring.send_at.astimezone(UTC) == dt.datetime(2027, 3, 28, 4, 0, tzinfo=UTC)
    assert winter.send_at.astimezone(WARSAW).time() == dt.time(6, 0)


def test_days_before_counts_calendar_days():
    rule = _rule(days_before=[1, 0], send_time=dt.time(18, 30))
    occurrences = planning.plan_workshops(rule, [_row(day=dt.date(2026, 10, 24))]).occurrences

    assert [o.send_at.astimezone(WARSAW) for o in occurrences] == [
        dt.datetime(2026, 10, 23, 18, 30, tzinfo=WARSAW),
        dt.datetime(2026, 10, 24, 18, 30, tzinfo=WARSAW),
    ]
    assert {o.key for o in occurrences} == {"w:2026-10-24-x:d1", "w:2026-10-24-x:d0"}


def test_hours_before_counts_real_hours_across_the_dst_change():
    rule = _rule(timing=RuleTiming.HOURS_BEFORE, hours_before=24)
    # Warsztat w niedzielę 25.10.2026 o 11:00 CET (10:00 UTC) – 24 h wcześniej to 10:00 UTC w sobotę,
    # czyli 12:00 czasu letniego, a nie 11:00 („ta sama godzina na zegarze”).
    occurrence = planning.plan_workshops(rule, [_row(day=dt.date(2026, 10, 25))]).occurrences[0]

    assert occurrence.send_at.astimezone(UTC) == dt.datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
    assert occurrence.send_at.astimezone(WARSAW).hour == 12
    assert occurrence.key == "w:2026-10-25-x:h24"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("11:00–14:00", dt.time(11, 0)), ("godz. 9.30 – 11.00", dt.time(9, 30)), ("do potwierdzenia", None)],
)
def test_start_time_is_the_first_hour_of_the_column(text, expected):
    assert planning.start_time(text) == expected


# --- pominięcia ------------------------------------------------------------------------------------


def test_rows_without_a_date_or_cancelled_are_skipped():
    rows = [
        _row(topic="Bez daty", day=None),
        _row(topic="Algebra liniowa", time="ODWOŁANE"),
        _row(topic="Mechanika (warsztat odwołany)"),
        _row(topic="Statystyka", day=dt.date(2026, 10, 31)),
    ]

    plan = planning.plan_workshops(_rule(), rows)

    assert [o.subject for o in plan.occurrences] == ["Dziś warsztat: Statystyka"]
    assert len(plan.skipped) == 3


def test_hours_before_skips_a_row_without_a_readable_start():
    rule = _rule(timing=RuleTiming.HOURS_BEFORE, hours_before=2)
    plan = planning.plan_workshops(rule, [_row(time="do potwierdzenia")])

    assert plan.occurrences == []
    assert plan.skipped


# --- termin etapu ----------------------------------------------------------------------------------


def _stage(kind=StageKind.ELIM, competition_id=1) -> Stage:
    stage = Stage(
        pk=7,
        kind=kind,
        name="Etap I",
        deadline_at=dt.datetime(2027, 2, 28, 23, 59, tzinfo=WARSAW),
    )
    stage.edition = Edition(competition_id=competition_id)
    return stage


def test_stage_reminders_days_before_the_deadline_in_polish_time():
    rule = _rule(
        kind=RuleKind.STAGE_DEADLINE,
        days_before=[30, 7, 1],
        send_time=dt.time(9, 0),
        subject_template="{etap}: zostało {dni}",
        body_template="Termin: {termin}. {link}",
        group="STAGE_NO_SUBMISSION",
    )

    plan = planning.plan_stage(rule, _stage())

    assert [o.send_at.astimezone(WARSAW) for o in plan.occurrences] == [
        dt.datetime(2027, 1, 29, 9, 0, tzinfo=WARSAW),
        dt.datetime(2027, 2, 21, 9, 0, tzinfo=WARSAW),
        dt.datetime(2027, 2, 27, 9, 0, tzinfo=WARSAW),
    ]
    assert plan.occurrences[-1].subject == "Etap I: zostało 1 dzień"
    assert plan.occurrences[0].body == "Termin: 28 lutego 2027, 23:59. https://meet.google.com/izu-qrvp-kuw"
    assert [o.key for o in plan.occurrences] == ["s:7:d30", "s:7:d7", "s:7:d1"]


def test_training_stage_and_stage_of_another_competition_give_nothing():
    rule = _rule(kind=RuleKind.STAGE_DEADLINE, subject_template="{etap}", body_template="{termin}")

    assert planning.plan_stage(rule, _stage(kind=StageKind.TRAINING)).occurrences == []
    assert planning.plan_stage(rule, _stage(competition_id=2)).occurrences == []
    assert planning.plan_stage(rule, None).occurrences == []


# --- okno generowania i odcisk ---------------------------------------------------------------------


def test_generation_window_is_five_minutes_to_seven_days_ahead():
    now = dt.datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

    assert not planning.in_window(now + dt.timedelta(minutes=4), now)
    assert planning.in_window(now + dt.timedelta(minutes=5), now)
    assert planning.in_window(now + dt.timedelta(days=7), now)
    assert not planning.in_window(now + dt.timedelta(days=7, minutes=1), now)
    assert not planning.in_window(now - dt.timedelta(hours=1), now)


def test_fingerprint_changes_with_text_time_and_group_only():
    rule = _rule()
    occurrence = planning.plan_workshops(rule, [_row()]).occurrences[0]
    same = planning.plan_workshops(_rule(name="inna nazwa"), [_row()]).occurrences[0]
    other_text = planning.plan_workshops(_rule(link="https://meet.google.com/abc"), [_row()]).occurrences[0]

    assert planning.fingerprint(rule, occurrence) == planning.fingerprint(rule, same)
    assert planning.fingerprint(rule, occurrence) != planning.fingerprint(rule, other_text)
    assert planning.fingerprint(rule, occurrence) != planning.fingerprint(
        _rule(group="SUPERVISORS"), occurrence
    )


def test_beat_runs_the_generator_every_five_minutes():
    from django.conf import settings

    from apps.message_rules.tasks import generate_rule_broadcasts

    entry = settings.CELERY_BEAT_SCHEDULE["generate-message-rule-broadcasts"]
    assert (
        entry["task"] == generate_rule_broadcasts.name == "apps.message_rules.tasks.generate_rule_broadcasts"
    )
    assert entry["schedule"] == 300.0
