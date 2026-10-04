"""Monitor dostępności (OPS-02 § 4): cele, deduplikacja, przypomnienia, powrót, TLS, limit listów.

Logika alarmów to czysta funkcja stanu (``evaluate``) – bez sieci i bez zegara. Sprawdzenie HTTP –
na lokalnym serwerze z biblioteki standardowej.
"""

from __future__ import annotations

import ast
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from apps.monitoring import uptime
from apps.monitoring.uptime import Config, Result, Target, evaluate, take_batch

H = 3600
CFG = Config(targets=(), recipients=("dyzurny@example.org",), site="example.org")
SITE = "http https://example.org/"
TLS = "tls example.org:443"


def _run(state, results, now, cfg=CFG):
    evaluation = evaluate(state, results, now, cfg)
    return evaluation.state, [n.kind for n in evaluation.notifications]


def test_the_module_needs_nothing_but_the_standard_library():
    """Plik ma się dać skopiować na inny serwer i uruchomić z crona – bez Django i bez aplikacji."""
    tree = ast.parse(Path(uptime.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not imported & {"django", "apps", "config", "requests", "sentry_sdk"}


def test_default_targets_cover_both_sites_health_status_livekit_and_errors():
    targets = uptime.default_targets(
        {
            "SITE_DOMAIN": "olimpiadakwantowa.pl",
            "EXTRA_DOMAINS": "iqo-official.org www.iqo-official.org localhost:8001",
            "LIVEKIT_URL": "wss://live.olimpiadakwantowa.pl",
            "ERRORS_PROXY": "1",
            "UPTIME_NOTEBOOK_LAB": "1",
            "UPTIME_EXTRA_URLS": "https://nb.example.org/hub/health|json",
        }
    )

    assert [(t.url, t.expect) for t in targets] == [
        ("https://olimpiadakwantowa.pl/", "page"),
        ("https://olimpiadakwantowa.pl/healthz/", "json-ok"),
        ("https://iqo-official.org/", "page"),
        ("https://iqo-official.org/healthz/", "json-ok"),
        ("https://olimpiadakwantowa.pl/status.json", "json-ok"),
        ("https://errors.olimpiadakwantowa.pl/_health/", "page"),
        ("https://olimpiadakwantowa.pl/static/notebook-lab/current.json", "page"),
        ("https://live.olimpiadakwantowa.pl/", "page"),
        ("https://nb.example.org/hub/health", "json-ok"),
    ]


def test_explicit_urls_replace_the_defaults_and_localhost_has_none():
    assert uptime.default_targets({"SITE_DOMAIN": "x.org", "UPTIME_URLS": "https://a.org/"}) == (
        Target("https://a.org/"),
    )
    assert uptime.default_targets({"SITE_DOMAIN": "localhost"}) == ()


def test_recipients_fall_back_to_alert_emails():
    cfg = uptime.config_from_env({"SITE_DOMAIN": "x.org", "ALERT_EMAILS": "a@x.org, b@x.org"})

    assert cfg.recipients == ("a@x.org", "b@x.org")
    assert cfg.sender == "uptime@x.org"


def test_one_failure_is_not_an_outage_and_a_short_blip_sends_nothing():
    state, kinds = _run({}, {SITE: Result(False, "HTTP 502")}, 0)
    state, kinds2 = _run(state, {SITE: Result(False, "HTTP 502")}, 60)
    state, kinds3 = _run(state, {SITE: Result(True, "HTTP 200")}, 120)
    state, kinds4 = _run(state, {SITE: Result(True, "HTTP 200")}, 180)

    assert kinds + kinds2 + kinds3 + kinds4 == []
    assert state["targets"][SITE]["down"] is False


def test_outage_alerts_once_then_reminds_with_backoff_then_recovers_once():
    state, sent = {}, []
    fail = {SITE: Result(False, "HTTP 502")}
    for minute in range(0, 8 * 60):  # osiem godzin awarii, przebieg co minutę
        state, kinds = _run(state, fail, minute * 60)
        sent += [(minute, k) for k in kinds]

    assert sent[0] == (2, "down")  # trzecia porażka
    reminders = [m for m, k in sent if k == "reminder"]
    assert reminders == [62, 182, 422]  # +1 h, +2 h, +4 h od poprzedniego listu
    assert len(sent) == 4

    state, kinds = _run(state, {SITE: Result(True, "HTTP 200")}, 8 * H)
    assert kinds == []  # jeden sukces to jeszcze nie powrót
    evaluation = evaluate(state, {SITE: Result(True, "HTTP 200")}, 8 * H + 60, CFG)
    assert [n.kind for n in evaluation.notifications] == ["recovered"]
    assert "przerwa 481 min" in evaluation.notifications[0].line()


def test_reminder_interval_is_capped_at_a_day():
    cfg = Config(targets=(), remind_first=H, remind_max=4 * H)
    state, sent = {}, []
    for step in range(0, 30):  # przebieg co godzinę
        state, kinds = _run(state, {SITE: Result(False, "x")}, step * H, cfg)
        sent += [step for k in kinds if k == "reminder"]

    gaps = [b - a for a, b in zip(sent, sent[1:], strict=False)]
    assert gaps and max(gaps) == 4


def test_tls_warns_immediately_reminds_daily_and_reports_renewal():
    state, kinds = _run({}, {TLS: Result(False, "certyfikat wygasa za 9.0 dni")}, 0)
    assert kinds == ["tls"]
    state, kinds = _run(state, {TLS: Result(False, "8.9")}, 12 * H)
    assert kinds == []
    state, kinds = _run(state, {TLS: Result(False, "8.5")}, 24 * H)
    assert kinds == ["tls-reminder"]
    state, kinds = _run(state, {TLS: Result(True, "89 dni")}, 25 * H)
    assert kinds == ["tls-ok"]


def test_unknown_result_does_not_change_state():
    state, _ = _run({}, {TLS: Result(False, "9 dni")}, 0)
    state2, kinds = _run(state, {TLS: Result(None, "brak połączenia")}, 60)

    assert kinds == []
    assert state2["targets"][TLS] == state["targets"][TLS]


def test_targets_no_longer_checked_are_forgotten():
    state, _ = _run({}, {SITE: Result(False, "x")}, 0)
    state, _ = _run(state, {TLS: Result(True, "ok")}, 60)

    assert SITE not in state["targets"]


def test_everything_from_one_run_is_one_mail_and_the_hourly_cap_defers_the_rest():
    cfg = Config(
        targets=(),
        recipients=("a@x.org",),
        max_mails_per_hour=2,
        fail_threshold=1,
        recover_threshold=1,
        site="x.org",
    )
    results = {f"http https://s{i}.org/": Result(False, "HTTP 503") for i in range(10)}
    evaluation = evaluate({}, results, 0, cfg)
    state = evaluation.state

    batch = take_batch(state, evaluation.notifications, 0, cfg)
    assert len(batch) == 10
    message = uptime.compose_mail(batch, cfg)
    assert message["Subject"] == "[x.org] dostępność – awaria: 10"
    uptime.mark_sent(state, 0)
    uptime.mark_sent(state, 10)

    later = evaluate(state, {"http https://s0.org/": Result(True, "ok")}, 120, cfg)
    assert take_batch(later.state, later.notifications, 120, cfg) == []
    assert len(later.state["pending"]) == 1  # powrót czeka – limit 2 listów na godzinę

    after_hour = evaluate(later.state, {"http https://s0.org/": Result(True, "ok")}, H + 20, cfg)
    assert [n["kind"] for n in take_batch(after_hour.state, after_hour.notifications, H + 20, cfg)] == [
        "recovered"
    ]


def test_run_once_sends_one_mail_and_keeps_it_pending_when_smtp_fails():
    cfg = Config(targets=(Target("https://a.org/"),), recipients=("a@x.org",), fail_threshold=1, site="x.org")
    sent = []

    def broken(msg, cfg):
        raise OSError("relay leży")

    state = uptime.run_once(
        cfg, {}, now=0, checker=lambda c: {SITE: Result(False, "HTTP 502")}, sender=broken
    )
    assert len(state["pending"]) == 1 and state["mails"] == []

    state = uptime.run_once(
        cfg,
        state,
        now=60,
        checker=lambda c: {SITE: Result(False, "HTTP 502")},
        sender=lambda msg, cfg: sent.append(msg),
    )
    assert len(sent) == 1 and state["pending"] == []
    assert "[AWARIA] http https://example.org/: HTTP 502" in sent[0].get_content()


def test_the_module_parses_on_python_3_10():
    """M3: kopia poza serwerem bywa na 3.10 – bez składni PEP 758 (``except A, B:``) i nowszych."""
    ast.parse(Path(uptime.__file__).read_text(encoding="utf-8"), feature_version=(3, 10))


def test_planned_maintenance_suppresses_http_alerts_but_not_tls(tmp_path):
    """L5: plik ``on`` (scripts/maintenance.sh on) – porażki HTTP bez listu; certyfikat dalej."""
    flag = tmp_path / "on"
    flag.write_text("")
    cfg = Config(
        targets=(), recipients=("a@x.org",), fail_threshold=1, maintenance_file=str(flag), site="x.org"
    )
    sent = []
    results = {SITE: Result(False, "HTTP 503"), TLS: Result(False, "certyfikat wygasa za 3.0 dni")}

    state = uptime.run_once(cfg, {}, now=0, checker=lambda c: results, sender=lambda m, c: sent.append(m))

    assert len(sent) == 1
    assert "[CERTYFIKAT]" in sent[0].get_content() and "[AWARIA]" not in sent[0].get_content()
    assert SITE not in state["targets"] or state["targets"][SITE]["down"] is False
    flag.unlink()
    uptime.run_once(cfg, state, now=60, checker=lambda c: results, sender=lambda m, c: sent.append(m))
    assert "[AWARIA]" in sent[-1].get_content()


def test_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "state.json")
    state, _ = _run({}, {SITE: Result(False, "x")}, 0)
    uptime.save_state(path, state)

    assert uptime.load_state(path) == json.loads(json.dumps(state))
    assert uptime.load_state(str(tmp_path / "brak.json")) == {}


class _Handler(BaseHTTPRequestHandler):
    routes = {
        "/healthz/": (200, b'{"status": "ok", "db": true}'),
        "/degraded/": (200, b'{"status": "degraded"}'),
        "/maintenance/": (503, b'{"status":"maintenance","retry_after":60}'),
        "/": (200, b"<html>ok</html>"),
    }

    def do_GET(self):  # noqa: N802 - API biblioteki standardowej
        status, body = self.routes.get(self.path, (404, b""))
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_check_http_reads_status_and_json(local_server):
    assert uptime.check_http(Target(f"{local_server}/"), 5).ok is True
    assert uptime.check_http(Target(f"{local_server}/healthz/", "json-ok"), 5).ok is True
    degraded = uptime.check_http(Target(f"{local_server}/degraded/", "json-ok"), 5)
    assert degraded.ok is False and "degraded" in degraded.detail
    maintenance = uptime.check_http(Target(f"{local_server}/maintenance/"), 5)
    assert maintenance.ok is False and "prac technicznych" in maintenance.detail
    assert uptime.check_http(Target(f"{local_server}/nie-ma/"), 5).detail == "HTTP 404"
