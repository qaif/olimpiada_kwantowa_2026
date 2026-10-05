"""``manage.py loadtest_seed`` (PERF-01): bezpieczniki i kształt danych testu obciążenia."""

from __future__ import annotations

import json

import pytest
from django.core.management import CommandError, call_command
from django.db import connection

from apps.accounts.models import Participant
from apps.chat.models import Conversation
from apps.competitions.models import StageEntry
from apps.results.models import ResultsPublication

pytestmark = pytest.mark.django_db


def test_refuses_a_database_without_loadtest_in_its_name(competition, settings):
    settings.DEBUG = True
    assert "loadtest" not in connection.settings_dict["NAME"]

    with pytest.raises(CommandError, match="loadtest"):
        call_command("loadtest_seed", "--students", "1", "--i-know-this-is-not-prod")


def test_refuses_without_debug_or_explicit_flag(competition, settings, monkeypatch):
    settings.DEBUG = False
    monkeypatch.setitem(connection.settings_dict, "NAME", "olimpiada_loadtest")

    with pytest.raises(CommandError, match="i-know-this-is-not-prod"):
        call_command("loadtest_seed", "--students", "1")


def test_seeds_students_entries_conversations_and_a_manifest(competition, settings, monkeypatch, tmp_path):
    settings.DEBUG = False
    monkeypatch.setitem(connection.settings_dict, "NAME", "olimpiada_loadtest")
    manifest = tmp_path / "manifest.json"
    args = [
        "--students",
        "4",
        "--coordinators",
        "1",
        "--news",
        "0",
        "--statement-kb",
        "2",
        "--quiz-questions",
        "2",
    ]

    call_command("loadtest_seed", *args, "--i-know-this-is-not-prod", "--manifest", str(manifest))
    call_command("loadtest_seed", *args, "--i-know-this-is-not-prod")  # drugi raz – bez duplikatów

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(data["students"]) == 4
    assert all(row["conversation_id"] for row in data["students"])
    assert Participant.objects.filter(user__email__endswith="@loadtest.local").count() == 4
    # Trzy etapy (pisemny, test, poprzedni z wynikami) po jednym wpisie na ucznia.
    assert StageEntry.objects.filter(participant__user__email__endswith="@loadtest.local").count() == 12
    assert Conversation.objects.count() == 4
    assert len(ResultsPublication.objects.get(stage_id=data["results_stage_id"]).rows) == 4
