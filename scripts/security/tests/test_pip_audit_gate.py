"""Bramka pip-audit: co blokuje, co jest ostrzeżeniem, kiedy wyjątek przestaje działać."""

import datetime as dt
import json

import pip_audit_gate as gate

TODAY = dt.date(2026, 10, 5)


def _report(*vulns, name="django", version="6.1.0"):
    return {"dependencies": [{"name": name, "version": version, "vulns": list(vulns)}], "fixes": []}


def _vuln(vid="GHSA-aaaa-bbbb-cccc", fixes=("6.1.2",), aliases=("CVE-2026-1",)):
    return {"id": vid, "fix_versions": list(fixes), "aliases": list(aliases), "description": "x"}


def _allowlist(tmp_path, body):
    path = tmp_path / "ignore.toml"
    path.write_text(body, encoding="utf-8")
    return path


REASON = "Podatna funkcja nie jest używana w naszym kodzie; poprawka wymaga nowej linii."


def test_fixable_vulnerability_blocks():
    verdict = gate.evaluate(_report(_vuln()), [])
    assert len(verdict.blocking) == 1 and "6.1.2" in verdict.blocking[0]


def test_vulnerability_without_fix_only_warns():
    verdict = gate.evaluate(_report(_vuln(fixes=())), [])
    assert verdict.blocking == [] and len(verdict.no_fix) == 1


def test_waiver_matches_alias_and_normalised_package_name(tmp_path):
    path = _allowlist(
        tmp_path,
        f'[[ignore]]\nid = "CVE-2026-1"\npackage = "Django"\nreason = "{REASON}"\nexpires = 2026-11-01\n',
    )
    waivers, errors = gate.load_allowlist(path, TODAY, 180)
    verdict = gate.evaluate(_report(_vuln()), waivers)
    assert errors == [] and verdict.blocking == [] and len(verdict.accepted) == 1


def test_waiver_for_other_package_does_not_apply(tmp_path):
    path = _allowlist(
        tmp_path,
        f'[[ignore]]\nid = "CVE-2026-1"\npackage = "wagtail"\nreason = "{REASON}"\nexpires = 2026-11-01\n',
    )
    waivers, _ = gate.load_allowlist(path, TODAY, 180)
    verdict = gate.evaluate(_report(_vuln()), waivers)
    assert len(verdict.blocking) == 1 and len(verdict.stale) == 1


def test_expired_short_reason_and_far_expiry_are_errors(tmp_path):
    path = _allowlist(
        tmp_path,
        f'[[ignore]]\nid = "A"\npackage = "x"\nreason = "{REASON}"\nexpires = 2026-10-04\n'
        '[[ignore]]\nid = "B"\npackage = "x"\nreason = "fałszywy alarm"\nexpires = 2026-11-01\n'
        f'[[ignore]]\nid = "C"\npackage = "x"\nreason = "{REASON}"\nexpires = 2027-10-01\n'
        f'[[ignore]]\nid = "D"\npackage = "x"\nreason = "{REASON}"\nexpires = "2026-11-01"\n',
    )
    waivers, errors = gate.load_allowlist(path, TODAY, 180)
    assert waivers == []
    assert [e.split(":")[0] for e in errors] == [
        f"ignore.toml wpis {n} ({i})" for n, i in enumerate("ABCD", 1)
    ]


def test_missing_report_is_a_failure_not_a_pass(tmp_path):
    assert gate.main([str(tmp_path / "brak.json"), "--allowlist", str(tmp_path / "x.toml")]) == 2


def test_main_exit_codes(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    report = tmp_path / "audit.json"
    report.write_text(json.dumps(_report(_vuln(fixes=()))), encoding="utf-8")
    assert gate.main([str(report), "--allowlist", str(tmp_path / "x.toml")]) == 0
    report.write_text(json.dumps(_report(_vuln())), encoding="utf-8")
    assert gate.main([str(report), "--allowlist", str(tmp_path / "x.toml")]) == 1


def test_repository_allowlist_is_valid():
    _waivers, errors = gate.load_allowlist(gate.DEFAULT_ALLOWLIST, dt.date.today(), gate.DEFAULT_MAX_DAYS)
    assert errors == []
