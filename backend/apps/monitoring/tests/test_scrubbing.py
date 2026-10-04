"""Filtr danych osobowych zdarzeń (OPS-02 § 2) – paszport, zdrowie, tokeny i e-mail nie wychodzą."""

from __future__ import annotations

import json

from apps.monitoring.scrubbing import FILTERED, scrub_breadcrumb, scrub_event, scrub_text

PASSPORT = "ZX1234567"
PESEL = "08241512345"
EMAIL = "jan.kowalski@example.org"
TOKEN = "s3cr3t-T0ken-value"


def _event() -> dict:
    """Zdarzenie w kształcie, jaki składa klient Sentry z integracjami Django i Celery."""
    return {
        "event_id": "a" * 32,
        "level": "error",
        "server_name": "web-1",
        "request": {
            "method": "POST",
            "url": f"https://iqo-official.org/delegation/logistics/?token={TOKEN}",
            "query_string": f"token={TOKEN}&email={EMAIL}",
            "data": {"passport_number": PASSPORT, "health_notes": "astma", "diet": "halal"},
            "cookies": {"sessionid": "abc", "csrftoken": "def"},
            "headers": {
                "Host": "iqo-official.org",
                "Authorization": f"Bearer {TOKEN}",
                "Cookie": "sessionid=abc",
                "Referer": f"https://iqo-official.org/reset/?token={TOKEN}",
                "User-Agent": "Mozilla/5.0",
                "X-Real-IP": "203.0.113.7",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            "env": {"REMOTE_ADDR": "203.0.113.7", "SERVER_NAME": "web"},
        },
        "user": {"id": 42, "email": EMAIL, "ip_address": "203.0.113.7"},
        "tags": {"competition": "iqo", "celery_task_name": "apps.core.tasks.send_mail_task"},
        "extra": {
            "celery-job": {
                "task_name": "apps.core.tasks.send_mail_task",
                "args": [EMAIL, "Twoje hasło"],
                "kwargs": {"to": EMAIL},
            },
            "birth_date": "2009-05-01",
            "guardian_phone": "+48 600 700 800",
        },
        "contexts": {
            "runtime": {"name": "CPython", "version": "3.14.0"},
            "delegation": {"passport": PASSPORT, "country": "PL"},
        },
        "logentry": {"message": "Nie wysłano listu do %s", "params": [EMAIL]},
        "exception": {
            "values": [
                {
                    "type": "IntegrityError",
                    "value": "duplicate key value violates unique constraint\n"
                    f"DETAIL:  Key (email)=({EMAIL}) already exists. pesel={PESEL}",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "apps/delegation_logistics/services.py",
                                "function": "save_person",
                                "lineno": 10,
                                "vars": {"passport_number": PASSPORT, "health": "astma"},
                            }
                        ]
                    },
                }
            ]
        },
        "breadcrumbs": {
            "values": [
                {"category": "query", "message": "SELECT 1 FROM accounts_user WHERE email = %s"},
                {
                    "category": "httplib",
                    "data": {"url": f"https://api.example.org/x?key={TOKEN}", "method": "GET"},
                },
                {"category": "log", "message": f"Reset hasła dla {EMAIL} tel. 600-700-800"},
            ]
        },
    }


def test_nothing_sensitive_survives_the_filter():
    scrubbed = json.dumps(scrub_event(_event()), ensure_ascii=False)

    for secret in (
        PASSPORT,
        PESEL,
        EMAIL,
        TOKEN,
        "astma",
        "halal",
        "203.0.113.7",
        "sessionid",
        "2009-05-01",
        "600 700 800",
        "600-700-800",
        "Twoje hasło",
        "Mozilla",
    ):
        assert secret not in scrubbed, secret


def test_request_keeps_only_method_path_and_allowed_headers():
    request = scrub_event(_event())["request"]

    assert request == {
        "method": "POST",
        "url": "https://iqo-official.org/delegation/logistics/",
        "headers": {"Host": "iqo-official.org", "Content-Type": "application/x-www-form-urlencoded"},
    }


def test_user_is_dropped_entirely():
    assert "user" not in scrub_event(_event())


def test_frame_variables_are_removed_but_code_location_stays():
    frame = scrub_event(_event())["exception"]["values"][0]["stacktrace"]["frames"][0]

    assert "vars" not in frame
    assert frame["function"] == "save_person"
    assert frame["lineno"] == 10


def test_technical_context_and_task_name_survive():
    event = scrub_event(_event())

    assert event["contexts"]["runtime"] == {"name": "CPython", "version": "3.14.0"}
    assert event["contexts"]["delegation"]["passport"] == FILTERED
    assert event["extra"]["celery-job"]["task_name"] == "apps.core.tasks.send_mail_task"
    assert event["extra"]["celery-job"]["args"] == FILTERED
    assert event["tags"]["competition"] == "iqo"


def test_postgres_detail_keeps_the_column_but_not_the_value():
    value = scrub_event(_event())["exception"]["values"][0]["value"]

    assert f"Key (email)=({FILTERED})" in value


def test_breadcrumb_url_loses_its_query():
    crumb = scrub_breadcrumb({"category": "httplib", "data": {"url": f"https://x.org/a?sig={TOKEN}"}})

    assert crumb["data"]["url"] == "https://x.org/a"


def test_scrub_text_leaves_dates_and_ordinary_numbers():
    text = "Etap 2026-10-04 12:00, 15 prac, id=1234, wersja v0.40.1"

    assert scrub_text(text) == text


def test_scrub_text_filters_jwt_and_bearer():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJyb29tIjoiYSJ9.c2lnbmF0dXJl"

    assert jwt not in scrub_text(f"pokój {jwt}")
    assert TOKEN not in scrub_text(f"Authorization: Bearer {TOKEN}")


def test_a_malformed_event_does_not_raise():
    assert scrub_event({"request": "x", "exception": None, "breadcrumbs": [1, 2]}) is not None
