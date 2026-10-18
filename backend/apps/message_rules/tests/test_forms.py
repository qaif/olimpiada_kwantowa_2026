"""``RuleForm`` bez bazy: walidacja szablonu i momentu wysyłki, pola grupy jak na ekranie komunikatów.

Listy wyboru podajemy puste (``Stage.objects.none()`` nie pyta bazy), więc formularz da się
sprawdzić w całości poza parametrem etapu – ten mają testy ekranu z bazą.
"""

from __future__ import annotations

import datetime as dt

from apps.competitions.models import Stage
from apps.message_rules.forms import RuleForm
from apps.message_rules.models import RuleKind, RuleTiming

MEET = "https://meet.google.com/izu-qrvp-kuw"

DATA = {
    "name": "Sobota 6:00",
    "enabled": "on",
    "group": "ALL_PARTICIPANTS_AND_TEACHERS",
    "timing": "DAY_AT",
    "days_before": "0",
    "send_time": "06:00",
    "link": MEET,
    "subject": "Dziś warsztat: {temat}",
    "body": "{temat} – {data}, {godziny}. Prowadzi {prowadzący}. Link: {link}",
}


def _form(data, kind=RuleKind.WORKSHOP) -> RuleForm:
    return RuleForm(data, kind=kind, stages=Stage.objects.none(), regions=None)


def test_valid_workshop_rule_gives_the_fields_of_a_scheduled_broadcast():
    form = _form(DATA)

    assert form.is_valid(), form.errors
    fields = form.rule_fields()
    assert fields["group"] == "ALL_PARTICIPANTS_AND_TEACHERS"
    assert fields["parameters"] == {"include_past_editions": False}
    assert fields["target"] == {"past_editions": False, "label": "bieżąca edycja"}
    assert fields["days_before"] == [0]
    assert fields["send_time"] == dt.time(6, 0)
    assert fields["hours_before"] is None
    assert fields["stage"] is None
    assert "send_at" not in form.fields and "addresses" not in form.fields
    assert "CUSTOM" not in dict(form.fields["group"].choices)


def test_unknown_placeholder_is_an_error_of_its_field():
    form = _form({**DATA, "subject": "{tematy}", "body": "{etap}"})

    assert not form.is_valid()
    assert "{tematy}" in str(form.errors["subject"])
    assert "{etap}" in str(form.errors["body"])


def test_link_placeholder_without_a_link_is_an_error_of_the_link_field():
    form = _form({**DATA, "link": ""})

    assert not form.is_valid()
    assert "link" in form.errors
    assert set(form.errors) == {"link"}


def test_hours_before_needs_hours_and_ignores_days():
    assert not _form({**DATA, "timing": RuleTiming.HOURS_BEFORE, "days_before": ""}).is_valid()

    form = _form({**DATA, "timing": RuleTiming.HOURS_BEFORE, "days_before": "", "hours_before": "2"})
    assert form.is_valid(), form.errors
    assert form.rule_fields()["hours_before"] == 2
    assert form.rule_fields()["days_before"] == []


def test_days_before_must_be_numbers_within_the_limit():
    assert "days_before" in _form({**DATA, "days_before": "w sobotę"}).errors
    assert "days_before" in _form({**DATA, "days_before": "15"}).errors


def test_stage_rule_has_no_hours_option_and_requires_a_stage():
    form = _form(
        {**DATA, "group": "ALL_PARTICIPANTS", "subject": "{etap}: {dni}", "body": "{termin} {link}"},
        kind=RuleKind.STAGE_DEADLINE,
    )

    assert "timing" not in form.fields and "hours_before" not in form.fields
    assert not form.is_valid()
    assert set(form.errors) == {"stage"}
    # Etap jest polem reguły – skrypt nie może go schować przy grupie bez etapu.
    assert "stage" not in form.visible_parameter_fields()
    assert "stage" in _form(DATA).visible_parameter_fields()
