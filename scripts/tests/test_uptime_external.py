"""Testy ``scripts/uptime_external.py`` (OPS-03) – bez sieci, wyłącznie ``unittest``.

Uruchomienie (CI: job ``uptime-script`` w ``.github/workflows/ci.yml``)::

    python3 -m unittest discover -s scripts/tests -p "test_uptime_external.py" -v

Sieć, zegar, ``sleep`` i ``gh`` są podstawiane – testy sprawdzają ocenę odpowiedzi, progi, liczenie
ważności TLS, „dwa razy z rzędu” i decyzje o zgłoszeniu, a nie dostępność produkcji.
"""

from __future__ import annotations

import ast
import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "uptime_external.py"
sys.path.insert(0, str(SCRIPT.parent))

import uptime_external as ue  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
SITE = "https://olimpiadakwantowa.pl"
LIVE = "https://live.olimpiadakwantowa.pl"

STATUS_OK = {
    "status": "ok",
    "services": {"database": True, "cache": True, "storage": True, "queue": True},
    "backup_restore_check": "ok",
}


def resp(status=200, body=b"", elapsed=0.3, error=None):
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    return ue.Response(status, body, elapsed, error)


def healthy(url):
    if url.endswith("/healthz/"):
        return resp(body={"status": "ok", "db": True})
    if url.endswith("/status.json"):
        return resp(body=STATUS_OK)
    if url.startswith(LIVE):
        return resp(body=b"OK")
    return resp(body=b"<html>...</html>")


def cert_ok(host):
    return NOW + timedelta(days=60)


class SyntaxCompatTests(unittest.TestCase):
    def test_python_310_syntax(self):
        # Skrypt ma chodzić z każdej maszyny z Pythonem ≥ 3.10 (laptop, drugi serwer z cronem),
        # a nie tylko z runnera GitHuba z najnowszym Pythonem.
        ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT), feature_version=(3, 10))

    def test_test_file_python_310_syntax(self):
        ast.parse(Path(__file__).read_text(encoding="utf-8"), feature_version=(3, 10))


class PageTests(unittest.TestCase):
    def test_ok(self):
        r = ue.eval_page(SITE, resp(body=b"x"))
        self.assertEqual((r.check, r.level), ("olimpiadakwantowa.pl /", ue.OK))

    def test_slow_is_warning_not_failure(self):
        r = ue.eval_page(SITE, resp(elapsed=7.5))
        self.assertEqual(r.level, ue.WARN)
        self.assertIn("7.5 s", r.detail)

    def test_threshold_is_exclusive(self):
        self.assertEqual(ue.eval_page(SITE, resp(elapsed=ue.SLOW_WARN_S)).level, ue.OK)

    def test_http_error(self):
        r = ue.eval_page(SITE, resp(status=502))
        self.assertEqual(r.level, ue.FAIL)
        self.assertIn("HTTP 502", r.detail)

    def test_no_response(self):
        r = ue.eval_page(SITE, resp(status=None, elapsed=20.0, error="TimeoutError: timed out"))
        self.assertEqual(r.level, ue.FAIL)
        self.assertIn("brak odpowiedzi", r.detail)

    def test_maintenance_is_named(self):
        r = ue.eval_page(SITE, resp(status=503, body={"status": "maintenance", "retry_after": 60}))
        self.assertEqual(r.level, ue.FAIL)
        self.assertIn("prac technicznych", r.detail)


class FetchTests(unittest.TestCase):
    def test_only_https_without_network(self):
        # Adresy mogą przyjść ze zmiennych repozytorium – ``file:`` nie może otworzyć pliku runnera.
        for url in ("file:///etc/passwd", "http://olimpiadakwantowa.pl/", "ftp://x/"):
            with self.subTest(url=url):
                r = ue.fetch(url)
                self.assertIsNone(r.status)
                self.assertIn("tylko https", r.error)


class HealthzTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(ue.eval_healthz(SITE, resp(body={"status": "ok"})).level, ue.OK)

    def test_503_degraded(self):
        r = ue.eval_healthz(SITE, resp(status=503, body={"status": "degraded"}))
        self.assertEqual(r.level, ue.FAIL)

    def test_200_but_not_ok(self):
        self.assertEqual(ue.eval_healthz(SITE, resp(body={"status": "degraded"})).level, ue.FAIL)

    def test_200_not_json(self):
        self.assertEqual(ue.eval_healthz(SITE, resp(body=b"<html>")).level, ue.FAIL)


class StatusJsonTests(unittest.TestCase):
    def levels(self, body, status=200):
        main, backup = ue.eval_status_json(SITE, resp(status=status, body=body))
        self.assertEqual(main.check, "olimpiadakwantowa.pl /status.json")
        self.assertEqual(backup.check, "olimpiadakwantowa.pl /status.json backup_restore_check")
        return main, backup

    def test_all_ok(self):
        main, backup = self.levels(STATUS_OK)
        self.assertEqual((main.level, backup.level), (ue.OK, ue.OK))

    def test_degraded_names_services(self):
        body = dict(
            STATUS_OK, status="degraded", services={"database": True, "storage": False, "queue": False}
        )
        main, _ = self.levels(body)
        self.assertEqual(main.level, ue.FAIL)
        self.assertIn("queue, storage", main.detail)

    def test_backup_failed_and_stale_fail(self):
        for value in ("failed", "stale"):
            with self.subTest(value=value):
                main, backup = self.levels(dict(STATUS_OK, backup_restore_check=value))
                self.assertEqual(main.level, ue.OK)  # kopia nie gasi serwisu – osobne sprawdzenie
                self.assertEqual(backup.level, ue.FAIL)

    def test_backup_unknown_or_missing_warns(self):
        _, backup = self.levels(dict(STATUS_OK, backup_restore_check="unknown"))
        self.assertEqual(backup.level, ue.WARN)
        body = {k: v for k, v in STATUS_OK.items() if k != "backup_restore_check"}
        _, backup = self.levels(body)
        self.assertEqual(backup.level, ue.WARN)

    def test_not_json(self):
        main, backup = self.levels(b"<html>Bad gateway</html>")
        self.assertEqual((main.level, backup.level), (ue.FAIL, ue.WARN))

    def test_json_list_is_not_status(self):
        main, _ = self.levels(b"[1, 2]")
        self.assertEqual(main.level, ue.FAIL)

    def test_maintenance(self):
        main, backup = self.levels({"status": "maintenance", "retry_after": 60}, status=503)
        self.assertEqual(main.level, ue.FAIL)
        self.assertIn("prac technicznych", main.detail)
        self.assertEqual(backup.level, ue.WARN)

    def test_unexpected_value_is_truncated(self):
        main, _ = self.levels(dict(STATUS_OK, status="x" * 500))
        self.assertLess(len(main.detail), 80)


class LiveTests(unittest.TestCase):
    def test_ok_with_newline(self):
        self.assertEqual(ue.eval_live(LIVE, resp(body=b"OK\n")).level, ue.OK)

    def test_other_body(self):
        r = ue.eval_live(LIVE, resp(body=b"<html>maintenance</html>"))
        self.assertEqual(r.level, ue.FAIL)
        self.assertEqual(r.check, "live.olimpiadakwantowa.pl / (LiveKit)")

    def test_down(self):
        self.assertEqual(ue.eval_live(LIVE, resp(status=502)).level, ue.FAIL)


class TlsTests(unittest.TestCase):
    def test_thresholds(self):
        cases = [
            (timedelta(days=60), ue.OK),
            (timedelta(days=14, minutes=1), ue.OK),
            (timedelta(days=13, hours=23), ue.WARN),
            (timedelta(days=7, minutes=1), ue.WARN),
            (timedelta(days=6, hours=23), ue.FAIL),
            (timedelta(seconds=-1), ue.FAIL),
        ]
        for delta, level in cases:
            with self.subTest(delta=delta):
                self.assertEqual(ue.eval_tls("a.pl", NOW + delta, NOW).level, level)

    def test_expired_message(self):
        r = ue.eval_tls("a.pl", NOW - timedelta(days=2), NOW)
        self.assertIn("wygasł", r.detail)

    def test_days_in_detail(self):
        r = ue.eval_tls("a.pl", NOW + timedelta(days=10, hours=12), NOW)
        self.assertIn("10.5 dni", r.detail)
        self.assertIn("2026-10-16 00:00 UTC", r.detail)

    def test_handshake_error(self):
        r = ue.eval_tls("a.pl", None, NOW, error="SSLCertVerificationError: certificate has expired")
        self.assertEqual(r.level, ue.FAIL)
        self.assertIn("certificate has expired", r.detail)

    def test_cert_time_fixture(self):
        # Ten sam zapis, który oddaje ``getpeercert()["notAfter"]`` – przez to samo przeliczenie.
        seconds = ue.ssl.cert_time_to_seconds("Dec 24 08:15:00 2026 GMT")
        not_after = datetime.fromtimestamp(seconds, tz=timezone.utc)
        self.assertEqual(not_after, datetime(2026, 12, 24, 8, 15, tzinfo=timezone.utc))
        self.assertEqual(ue.eval_tls("a.pl", not_after, NOW).level, ue.OK)


class RunChecksTests(unittest.TestCase):
    def test_all_healthy(self):
        results = ue.run_checks([SITE, "https://iqo-official.org"], [LIVE], healthy, cert_ok, NOW)
        self.assertEqual(ue.failing(results), [])
        # 2 witryny × 4 + LiveKit + 3 hosty TLS
        self.assertEqual(len(results), 12)
        self.assertEqual(len({r.check for r in results}), 12)

    def test_tls_once_per_host(self):
        results = ue.run_checks([SITE, SITE + "/"], [], healthy, cert_ok, NOW)
        self.assertEqual(sum(r.check.endswith(" TLS") for r in results), 1)

    def test_server_dead(self):
        def dead_fetch(url):
            return resp(status=None, elapsed=20, error="TimeoutError: timed out")

        def dead_cert(host):
            raise TimeoutError("timed out")

        results = ue.run_checks([SITE], [LIVE], dead_fetch, dead_cert, NOW)
        self.assertIn("olimpiadakwantowa.pl TLS", ue.failing(results))
        self.assertIn("live.olimpiadakwantowa.pl / (LiveKit)", ue.failing(results))
        self.assertIn("olimpiadakwantowa.pl /healthz/", ue.failing(results))


def r(check, level=ue.FAIL):
    return ue.Result(check, level, "x")


class RecheckTests(unittest.TestCase):
    def run_rounds(self, *rounds):
        it = iter(rounds)
        sleeps = []
        outcome = ue.run_with_recheck(lambda: list(next(it)), 120, sleep=sleeps.append)
        return outcome, sleeps

    def test_clean_first_round_no_recheck(self):
        outcome, sleeps = self.run_rounds([r("a", ue.OK), r("b", ue.WARN)])
        self.assertEqual((outcome.confirmed, outcome.any_failure, sleeps), ([], False, []))

    def test_twice_in_a_row_is_confirmed(self):
        outcome, sleeps = self.run_rounds([r("a"), r("b")], [r("a"), r("b", ue.OK)])
        self.assertEqual(outcome.confirmed, ["a"])
        self.assertEqual(sleeps, [120])
        self.assertTrue(outcome.any_failure)
        # Zgłoszenie pokazuje stan z drugiej próby.
        self.assertEqual({x.check: x.level for x in outcome.results}, {"a": ue.FAIL, "b": ue.OK})

    def test_single_blip_is_not_confirmed(self):
        outcome, _ = self.run_rounds([r("a")], [r("a", ue.OK)])
        self.assertEqual(outcome.confirmed, [])
        self.assertTrue(outcome.any_failure)

    def test_new_failure_in_second_round_only_is_not_confirmed(self):
        outcome, _ = self.run_rounds([r("a")], [r("a", ue.OK), r("b")])
        self.assertEqual(outcome.confirmed, [])


def outcome(confirmed=(), any_failure=None, results=None):
    confirmed = sorted(confirmed)
    if any_failure is None:
        any_failure = bool(confirmed)
    return ue.Outcome(results if results is not None else [r(c) for c in confirmed], confirmed, any_failure)


class DecideTests(unittest.TestCase):
    def test_no_issue(self):
        self.assertEqual(ue.decide(outcome(["a"]), None), ue.OPEN)
        self.assertEqual(ue.decide(outcome(), None), ue.NONE)
        self.assertEqual(ue.decide(outcome(any_failure=True), None), ue.NONE)

    def test_dedupe_same_set(self):
        self.assertEqual(ue.decide(outcome(["b", "a"]), ["a", "b"]), ue.NONE)

    def test_changed_set_updates(self):
        self.assertEqual(ue.decide(outcome(["a", "c"]), ["a", "b"]), ue.UPDATE)

    def test_recovery_closes(self):
        self.assertEqual(ue.decide(outcome(), ["a"]), ue.CLOSE)

    def test_flapping_keeps_issue_open(self):
        self.assertEqual(ue.decide(outcome(any_failure=True), ["a"]), ue.NONE)

    def test_issue_with_unreadable_state_and_failure_updates(self):
        self.assertEqual(ue.decide(outcome(["a"]), []), ue.UPDATE)


class IssueParsingTests(unittest.TestCase):
    def test_find_ours_ignores_manual(self):
        issues = [
            {"number": 9, "body": "awaria zgłoszona ręcznie"},
            {"number": 12, "body": ue.ISSUE_MARKER + "\nx"},
            {"number": 11, "body": ue.ISSUE_MARKER},
            {"number": 13, "body": None},
        ]
        self.assertEqual(ue.find_issue(issues)["number"], 11)
        self.assertIsNone(ue.find_issue([{"number": 1, "body": "nic"}]))

    def test_state_roundtrip(self):
        o = outcome(["b /", "a TLS"])
        body = ue.render_issue_body(o, NOW, "https://github.com/x/y/actions/runs/1")
        self.assertIn(ue.ISSUE_MARKER, body)
        self.assertEqual(ue.parse_state(body), ["a TLS", "b /"])

    def test_state_garbage(self):
        self.assertEqual(ue.parse_state("<!-- uptime-state: [nie json] -->"), [])
        self.assertEqual(ue.parse_state(""), [])
        self.assertEqual(ue.parse_state(None), [])

    def test_untrusted_detail_cannot_escape_code(self):
        evil = ue.Result("a /", ue.FAIL, "x` [link](https://evil) @someone | col\n## head")
        body = ue.render_issue_body(ue.Outcome([evil], ["a /"], True), NOW, "https://u")
        row = [line for line in body.splitlines() if line.startswith("| ❌")][0]
        self.assertEqual(row.count("`"), 4)  # dwie komórki w kodzie, nic nie wychodzi na zewnątrz
        self.assertEqual(row.count("|"), 4)
        self.assertNotIn("\n## head", body)

    def test_title(self):
        self.assertEqual(ue.issue_title(["a /"]), "Awaria widoczna z zewnątrz: a /")
        self.assertEqual(ue.issue_title(["a /", "b"]), "Awaria widoczna z zewnątrz: a / (+1)")

    def test_update_comment_lists_changes(self):
        text = ue.render_update_comment(outcome(["a", "c"]), ["a", "b"], NOW, "https://u")
        self.assertIn("Nowe: `c`", text)
        self.assertIn("Wróciły: `b`", text)


class FakeGh:
    def __init__(self, issues):
        self.issues = issues
        self.calls = []

    def __call__(self, args, stdin=None, check=True):
        self.calls.append((args[:2], stdin))
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.issues)
        return ""

    def verbs(self):
        return [" ".join(a) for a, _ in self.calls]


class ApplyIssueActionTests(unittest.TestCase):
    def apply(self, issues, o, dry_run=False):
        fake = FakeGh(issues)
        action = ue.apply_issue_action(o, "qaif/x", "https://u", NOW, dry_run=dry_run, runner=fake)
        return action, fake

    def test_open_creates_label_and_issue(self):
        action, fake = self.apply([], outcome(["a /"]))
        self.assertEqual(action, ue.OPEN)
        self.assertEqual(fake.verbs(), ["issue list", "label create", "issue create"])
        self.assertIn('<!-- uptime-state: ["a /"] -->', fake.calls[-1][1])

    def test_dedupe_no_calls(self):
        issue = {"number": 5, "body": ue.render_issue_body(outcome(["a /"]), NOW, "u")}
        action, fake = self.apply([issue], outcome(["a /"]))
        self.assertEqual((action, fake.verbs()), (ue.NONE, ["issue list"]))

    def test_update_comments_then_edits(self):
        issue = {"number": 5, "body": ue.render_issue_body(outcome(["a /"]), NOW, "u")}
        action, fake = self.apply([issue], outcome(["a /", "b TLS"]))
        self.assertEqual(action, ue.UPDATE)
        self.assertEqual(fake.verbs(), ["issue list", "issue comment", "issue edit"])
        self.assertEqual(ue.parse_state(fake.calls[-1][1]), ["a /", "b TLS"])

    def test_close_on_recovery(self):
        issue = {"number": 5, "body": ue.render_issue_body(outcome(["a /"]), NOW, "u")}
        action, fake = self.apply([issue], outcome(results=[r("a /", ue.OK), r("b", ue.WARN)]))
        self.assertEqual(action, ue.CLOSE)
        self.assertEqual(fake.verbs(), ["issue list", "issue comment", "issue close"])
        self.assertIn("ostrzeżenia", fake.calls[1][1])

    def test_manual_awaria_issue_is_left_alone(self):
        action, fake = self.apply([{"number": 3, "body": "ręczne"}], outcome())
        self.assertEqual((action, fake.verbs()), (ue.NONE, ["issue list"]))

    def test_dry_run_only_reads(self):
        action, fake = self.apply([], outcome(["a /"]), dry_run=True)
        self.assertEqual((action, fake.verbs()), (ue.OPEN, ["issue list"]))


class ReportTests(unittest.TestCase):
    def test_annotations_escape_newlines(self):
        o = ue.Outcome([ue.Result("a, b: c", ue.FAIL, "line1\n::set-env name=X::1")], ["a, b: c"], True)
        out = io.StringIO()
        ue.report(o, out=out, annotate=True)
        lines = out.getvalue().splitlines()
        annotations = [line for line in lines if line.startswith("::")]
        self.assertEqual(len(annotations), 1)
        self.assertTrue(annotations[0].startswith("::error title=a%2C b%3A c::line1%0A"))


if __name__ == "__main__":
    unittest.main()
