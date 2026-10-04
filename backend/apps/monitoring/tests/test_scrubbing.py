"""Filtr danych osobowych zdarzeń (OPS-02 § 2) – paszport, zdrowie, tokeny i e-mail nie wychodzą."""

from __future__ import annotations

import json
import time

import pytest

from apps.monitoring.scrubbing import FILTERED, mask_path, scrub_breadcrumb, scrub_event, scrub_text

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

    assert f"DETAIL:  {FILTERED}" in value
    assert f"Key (email)=({FILTERED})" in scrub_text(f"Key (email)=({EMAIL}) already exists.")


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


# --- poprawki po przeglądzie (H1, H2, H5, M2, L3) ------------------------------------------------

RESET_TOKEN = "c3k2-a1b2c3d4e5f6a7b8c9d0"
CONSENT_TOKEN = "AbCdEf123456XyZ_789"


def test_request_url_becomes_the_route_pattern_for_password_reset():
    """H1: token resetu hasła w **ścieżce** – adres żądania zastąpiony wzorcem trasy Django."""
    event = scrub_event(
        {
            "transaction": "/reset/{uidb64}/{token}/",
            "transaction_info": {"source": "route"},
            "request": {"method": "GET", "url": f"https://olimpiadakwantowa.pl/reset/MQ/{RESET_TOKEN}/"},
        }
    )

    assert event["request"]["url"] == "https://olimpiadakwantowa.pl/reset/{uidb64}/{token}/"
    assert RESET_TOKEN not in json.dumps(event)


def test_without_a_route_the_path_is_masked_for_consent_and_reset():
    """H1: bez trasy (404, transakcja ``url``) – segmenty-tokeny i wszystko po ``zgoda/``/``reset/``."""
    event = scrub_event(
        {
            "transaction": f"/zgoda/{CONSENT_TOKEN}/",
            "transaction_info": {"source": "url"},
            "request": {"method": "GET", "url": f"https://iqo-official.org/zgoda/{CONSENT_TOKEN}/?x=1"},
            "breadcrumbs": {
                "values": [
                    {
                        "category": "httplib",
                        "data": {"url": f"https://olimpiadakwantowa.pl/reset/MQ/{RESET_TOKEN}/"},
                    },
                    {
                        "category": "log",
                        "message": f"redirect next=/zgoda/{CONSENT_TOKEN}/ GET /reset/MQ/abc/",
                    },
                ]
            },
            "spans": [
                {"op": "http.client", "description": f"GET https://hooks.example.org/x/{CONSENT_TOKEN}"}
            ],
        }
    )
    dumped = json.dumps(event)

    assert event["request"]["url"] == f"https://iqo-official.org/zgoda/{FILTERED}/"
    assert event["transaction"] == f"/zgoda/{FILTERED}/"
    for secret in (CONSENT_TOKEN, RESET_TOKEN, "/MQ/", "abc/"):
        assert secret not in dumped, secret


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"/reset/MQ/{RESET_TOKEN}/", f"/reset/{FILTERED}/{FILTERED}/"),
        (f"/zgoda/{CONSENT_TOKEN}/", f"/zgoda/{FILTERED}/"),
        ("/zaproszenie/abc/", f"/zaproszenie/{FILTERED}/"),
        ("/visa/verify/AB12CD/", f"/visa/verify/{FILTERED}/"),
        ("/dyplomy/XK2P9/", f"/dyplomy/{FILTERED}/"),
        ("/me/messages/new/tok/", f"/me/messages/new/{FILTERED}/"),
        (f"/p/{CONSENT_TOKEN}.html", f"/p/{FILTERED}"),
        ("/coordinator/processing-register/", "/coordinator/processing-register/"),
        ("/static/js/app.3f2a1b9c8d7e.js", "/static/js/app.3f2a1b9c8d7e.js"),
        ("/coordinator/participants/123/", "/coordinator/participants/123/"),
    ],
)
def test_mask_path(path, expected):
    assert mask_path(path) == expected


def test_postgres_failing_row_and_detail_lines_are_filtered():
    """H2: ``Failing row contains (…)`` i każda linia ``DETAIL:`` – wartości z bazy."""
    value = scrub_text(
        'null value in column "school_id" violates not-null constraint\n'
        "DETAIL:  Failing row contains (17, Jan, Kowalski, 2011-04-03, ul. Lipowa 5, alergia na orzechy).\n"
        "CONTEXT: x"
    )

    assert "Kowalski" not in value and "alergia" not in value and "Lipowa" not in value
    assert value.startswith('null value in column "school_id"')
    assert f"DETAIL:  {FILTERED}" in value
    assert "Kowalski" not in scrub_text("Failing row contains (17, Jan, Kowalski).")


def test_redis_breadcrumb_keeps_only_the_command():
    """M2: klucz limitu żądań niesie adres IP – z okruszka Redisa zostaje sama nazwa polecenia."""
    crumb = scrub_breadcrumb(
        {
            "type": "redis",
            "category": "redis",
            "message": "GET ':1:throttle_anon_83.21.4.17'",
            "data": {"redis.command": "GET", "redis.key": ":1:throttle_anon_83.21.4.17"},
        }
    )

    assert crumb["message"] == "GET"
    assert crumb["data"] == {"redis.command": "GET"}


def test_ip_addresses_are_filtered_but_times_and_versions_stay():
    text = scrub_text("klient 83.21.4.17 i 2001:db8::1 oraz fe80::1, o 12:00:00, wersja 3.14.0, Foo::bar")

    assert "83.21.4.17" not in text and "2001:db8::1" not in text and "fe80::1" not in text
    assert "12:00:00" in text and "3.14.0" in text and "Foo::bar" in text


def test_argv_request_repr_and_query_keys_are_filtered():
    """L3 + logger Django z ``extra={"request": …}`` (``repr`` żądania z zapytaniem)."""
    event = scrub_event(
        {
            "extra": {
                "sys.argv": ["manage.py", "send", "--email", EMAIL],
                "request": "<WSGIRequest: POST '/delegation/logistics/?token=SECRETQ123&imie=Jan'>",
                "query": "imie=Jan",
                "fragment": "x",
            },
            "message": "<WSGIRequest: POST '/delegation/logistics/?token=SECRETQ123&imie=Jan'>",
        }
    )
    dumped = json.dumps(event)

    assert "sys.argv" not in event["extra"]
    for secret in (EMAIL, "SECRETQ123", "imie=Jan"):
        assert secret not in dumped, secret


@pytest.mark.parametrize(
    "hostile",
    [
        "a-" * 4000,
        "a@" * 4000,
        "a." * 4000,
        "/a" * 4000,
        "1:" * 4000,
        "x=token" * 1000,
        "eyJ" + "a." * 4000,
        "http://" + "a/" * 4000,
        "Key (" + "(" * 4000,
        "DETAIL: " + "x" * 9000,
        "ff" + ":" * 8000,
    ],
)
def test_hostile_input_is_scrubbed_in_linear_time(hostile):
    """H5: spreparowany komunikat nie zatrzymuje wątku wysyłki – przycięcie i wyrażenia liniowe."""
    started = time.perf_counter()
    scrub_text(hostile)

    assert time.perf_counter() - started < 0.05
