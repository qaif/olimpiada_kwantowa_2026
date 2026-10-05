"""Vendor JS (``check``), reguły workflow i lista obrazów compose – offline."""

import datetime as dt
import hashlib
import shutil

import policy_check
import third_party_images
import vendor_check


def test_repository_vendored_files_match_their_records():
    lines, findings = vendor_check.run_check(vendor_check.load_registry())
    assert findings == [], [str(f) for f in findings]
    assert any(line.startswith("zwendorowany katex ") for line in lines)


def _fake_vendor(tmp_path):
    lib = tmp_path / "backend" / "static" / "vendor" / "lib"
    lib.mkdir(parents=True)
    (lib / "lib.js").write_bytes(b"console.log(1)\n")
    digest = hashlib.sha256(b"console.log(1)\n").hexdigest()
    (lib / "VERSION").write_text(f"lib 1.2.3\nsha256 {digest}\n", encoding="utf-8")
    registry = {
        "vendored": [
            {
                "name": "lib",
                "npm": "lib",
                "dir": "backend/static/vendor/lib",
                "version_file": "VERSION",
                "version_pattern": r"(?m)^lib (?P<version>\S+)$",
                "hash_files": ["VERSION"],
            }
        ],
        "cdn": [],
    }
    return lib, registry


def test_swapped_file_without_record_update_fails(tmp_path):
    lib, registry = _fake_vendor(tmp_path)
    assert vendor_check.run_check(registry, tmp_path)[1] == []
    (lib / "lib.js").write_bytes(b"console.log(2)\n")
    assert "lib.js" in str(vendor_check.run_check(registry, tmp_path)[1][0])


def test_unrecorded_extra_file_and_unregistered_vendor_dir_fail(tmp_path):
    lib, registry = _fake_vendor(tmp_path)
    (lib / "extra.js").write_text("x", encoding="utf-8")
    shutil.copytree(lib, lib.parent / "other")
    messages = [str(f) for f in vendor_check.run_check(registry, tmp_path)[1]]
    assert any("extra.js" in m for m in messages)
    assert any("vendor/other" in m and "nie jest opisany" in m for m in messages)


def test_sha256sums_entries_are_checked_both_ways(tmp_path):
    lib, registry = _fake_vendor(tmp_path)
    registry["vendored"][0]["hash_files"] = ["VERSION", "SHA256SUMS"]
    (lib / "SHA256SUMS").write_text(f"{'0' * 64}  gone.woff2\n", encoding="utf-8")
    messages = [str(f) for f in vendor_check.run_check(registry, tmp_path)[1]]
    assert any("nieistniejący gone.woff2" in m for m in messages)


def test_cdn_versions_must_agree_and_unknown_scripts_fail(tmp_path):
    templates = tmp_path / "backend" / "templates"
    templates.mkdir(parents=True)
    (templates / "a.html").write_text(
        '<script src="https://cdn.example/x@1.0.0/x.js"></script>', encoding="utf-8"
    )
    (templates / "b.html").write_text(
        '<script src="https://cdn.example/x@1.0.1/x.js"></script>', encoding="utf-8"
    )
    registry = {
        "cdn": [
            {
                "name": "x",
                "npm": "x",
                "files": ["backend/templates/a.html", "backend/templates/b.html"],
                "version_pattern": r"x@(?P<version>[0-9.]+)/",
            }
        ]
    }
    assert "różne wersje" in str(vendor_check.run_check(registry, tmp_path)[1][0])
    (templates / "c.html").write_text('<script src="https://evil.example/y.js"></script>', encoding="utf-8")
    assert any("evil.example" in str(f) for f in vendor_check.run_check(registry, tmp_path)[1])


def test_workflow_actions_must_be_pinned_by_sha(tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "x.yml").write_text(
        "steps:\n"
        "  - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0\n"
        "  - uses: ./.github/actions/local\n"
        "  - uses: github/codeql-action/upload-sarif@v4\n"
        "  - uses: docker/login-action@main\n",
        encoding="utf-8",
    )
    problems = policy_check.check_workflows(tmp_path)
    assert [p.split(":")[1] for p in problems] == ["4", "5"]


def test_repository_workflows_are_pinned():
    assert policy_check.check_workflows() == []


def test_trivyignore_requires_statement_and_bounded_expiry(tmp_path):
    path = tmp_path / "trivyignore.yaml"
    why = "Biblioteka nie jest ładowana przez żaden proces obrazu."
    path.write_text(
        "vulnerabilities:\n"
        f'  - id: CVE-1\n    statement: "{why}"\n    expired_at: 2026-12-01\n'
        "  - id: CVE-2\n    expired_at: 2026-12-01\n"
        f"  - id: CVE-3\n    statement: {why}\n    expired_at: 2026-01-01\n"
        f"  - id: CVE-4\n    statement: {why}  # komentarz\n    expired_at: 2028-01-01\n",
        encoding="utf-8",
    )
    problems = policy_check.check_trivyignore(path, dt.date(2026, 10, 5))
    assert [p.split(":")[0] for p in problems] == [
        "trivyignore.yaml CVE-2",
        "trivyignore.yaml CVE-3",
        "trivyignore.yaml CVE-4",
    ]


def test_compose_images_resolve_defaults_and_skip_own():
    assert third_party_images.resolve_defaults("${A:-x/${B:-y}}") == "x/y"
    assert third_party_images.resolve_defaults("img:${TAG}") is None
    images = third_party_images.list_images()
    assert "postgres:18-alpine" in images and "caddy:2.8" in images
    # OPS-02: GlitchTip przypięty digestem – referencja zostaje w całości (Trivy skanuje ten digest).
    assert any(ref.startswith("glitchtip/glitchtip:") and "@sha256:" in ref for ref in images)
    assert not [ref for ref in images if ref.startswith("olimpiada/")]


def test_report_counts_unique_vulnerabilities(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "index.json").write_text('{"a:1": ["docker-compose.yml"], "b:2": ["x.yml"]}', encoding="utf-8")
    vuln = {
        "VulnerabilityID": "CVE-1",
        "PkgName": "openssl",
        "InstalledVersion": "1",
        "FixedVersion": "2",
        "Severity": "HIGH",
    }
    data = {
        "Metadata": {"RepoDigests": ["a@sha256:abc"]},
        "Results": [{"Vulnerabilities": [vuln, vuln]}, {"Vulnerabilities": None}],
    }
    import json

    (out / "a_1.json").write_text(json.dumps(data), encoding="utf-8")
    (out / "b_2.error").write_text("pull failed", encoding="utf-8")
    body, fingerprint, attention = third_party_images.report(out)
    assert "| `a:1` | `docker-compose.yml` | `sha256:abc` | 0 | 1 | 1 |" in body
    assert "**błąd skanu**" in body and attention and len(fingerprint) == 16
