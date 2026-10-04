"""Błędy JavaScriptu (OPS-02 § 5): CSP i HTML bez zmian, gdy funkcja jest wyłączona."""

from __future__ import annotations

import pytest
from django.template import Context, Template
from django.test import RequestFactory

from apps.monitoring.browser import BrowserConfig, parse_dsn
from apps.web.middleware import build_policy

DSN = "https://pub123@errors.example.org/3"
ORIGIN = "https://errors.example.org"


def test_parse_dsn():
    assert parse_dsn(DSN) == BrowserConfig(
        origin=ORIGIN, endpoint=f"{ORIGIN}/api/3/envelope/?sentry_key=pub123&sentry_version=7"
    )
    assert parse_dsn("http://k@glitchtip:8000/sub/12").endpoint == (
        "http://glitchtip:8000/sub/api/12/envelope/?sentry_key=k&sentry_version=7"
    )
    for bad in (
        "",
        "errors.example.org/3",
        "https://errors.example.org/3",
        "https://k@errors.example.org/x",
        "ftp://k@errors.example.org/3",
    ):
        assert parse_dsn(bad) is None, bad


def test_csp_is_unchanged_without_the_browser_switch(settings):
    settings.SENTRY_DSN = ""
    settings.SENTRY_BROWSER = False
    reference = build_policy("n")
    # Sam DSN serwera (bez SENTRY_BROWSER) nie dotyka polityki – klient serwerowy nie jest przeglądarką.
    settings.SENTRY_DSN = DSN

    assert build_policy("n") == reference
    assert "errors.example.org" not in reference


def test_csp_gets_the_errors_origin_only_in_connect_src(settings):
    settings.SENTRY_DSN = ""
    settings.SENTRY_BROWSER = False
    reference = build_policy("n")
    settings.SENTRY_DSN = DSN
    settings.SENTRY_BROWSER = True
    policy = build_policy("n")

    connect = next(d for d in policy.split("; ") if d.startswith("connect-src"))
    assert ORIGIN in connect.split()
    assert policy.replace(f" {ORIGIN}", "") == reference
    assert policy.count(ORIGIN) == 1


def test_browser_dsn_overrides_the_server_dsn(settings):
    settings.SENTRY_DSN = "http://k@glitchtip:8000/1"
    settings.SENTRY_BROWSER = True
    settings.SENTRY_BROWSER_DSN = DSN

    assert ORIGIN in build_policy("n")
    assert "glitchtip:8000" not in build_policy("n")


def _render(settings, **request_attrs) -> str:
    request = RequestFactory().get("/")
    for name, value in request_attrs.items():
        setattr(request, name, value)
    return Template("{% load monitoring_tags %}{% error_tracking_loader %}").render(
        Context({"request": request})
    )


def test_loader_tag_renders_nothing_when_disabled(settings):
    settings.SENTRY_DSN = DSN
    settings.SENTRY_BROWSER = False

    assert _render(settings, csp_nonce="abc") == ""


def test_loader_tag_renders_a_nonced_static_script(settings):
    settings.SENTRY_DSN = DSN
    settings.SENTRY_BROWSER = True

    class Competition:
        slug = "iqo"

    html = _render(settings, csp_nonce="abc", competition=Competition())

    assert html.startswith('<script nonce="abc" src="/static/monitoring/errors.js"')
    assert (
        'data-endpoint="https://errors.example.org/api/3/envelope/?sentry_key=pub123&amp;sentry_version=7"'
        in html
    )
    assert 'data-competition="iqo"' in html
    assert "cdn" not in html


@pytest.mark.django_db
def test_public_page_is_unchanged_when_disabled_and_carries_the_loader_when_enabled(client, settings):
    settings.SENTRY_DSN = ""
    settings.SENTRY_BROWSER = False
    off = client.get("/login/")
    assert off.status_code == 200
    assert b"monitoring/errors.js" not in off.content
    assert "errors.example.org" not in off.headers["Content-Security-Policy"]

    settings.SENTRY_DSN = DSN
    settings.SENTRY_BROWSER = True
    on = client.get("/login/")

    assert b"/static/monitoring/errors.js" in on.content
    assert ORIGIN in on.headers["Content-Security-Policy"]
