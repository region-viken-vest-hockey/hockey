"""Tests for the advisory pip-audit wrapper (GitHub issue #914)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dependency_audit", ROOT / "scripts" / "dependency-audit.py")
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

TODAY = dt.date(2026, 11, 9)
VULN = {"id": "PYSEC-1", "aliases": ["CVE-1"], "fix_versions": ["2.0"]}


def _report(*deps):
    return {"dependencies": list(deps)}


def _waiver_file(tmp_path, **overrides):
    entry = {"package": "Foo", "advisory": "CVE-1", "reason": "no fix", "expires": "2026-12-01", **overrides}
    path = tmp_path / "w.json"
    path.write_text(json.dumps({"waivers": [entry]}), encoding="utf-8")
    return path


def test_findings_report_package_version_advisory_and_fix():
    findings, waived, skipped = audit.classify(_report({"name": "foo", "version": "1.0", "vulns": [VULN, VULN]}), [], TODAY)
    assert findings == [{"package": "foo", "version": "1.0", "advisory": "PYSEC-1", "aliases": ["CVE-1"], "fixed": ["2.0"]}]
    assert not waived and not skipped


def test_waiver_matches_alias_and_expires(tmp_path):
    waivers = audit.load_waivers(_waiver_file(tmp_path), TODAY)
    report = _report({"name": "foo", "version": "1.0", "vulns": [VULN]})
    assert audit.classify(report, waivers, TODAY)[1]
    assert audit.classify(report, waivers, dt.date(2026, 12, 2))[0]


@pytest.mark.parametrize("override", [{"reason": ""}, {"expires": "soon"}, {"expires": "2030-01-01"}])
def test_invalid_waivers_fail_closed(tmp_path, override):
    with pytest.raises(audit.WaiverError):
        audit.load_waivers(_waiver_file(tmp_path, **override), TODAY)


def test_skipped_dependency_is_reported():
    _, _, skipped = audit.classify(_report({"name": "bar", "skip_reason": "not on PyPI"}), [], TODAY)
    assert skipped == ["bar: not on PyPI"]


def test_missing_scanner_is_unknown_not_green(monkeypatch, capsys):
    monkeypatch.setattr(audit.shutil, "which", lambda _name: None)
    assert audit.main(["--waivers", str(ROOT / "nope.json")]) == 2
    assert "UNKNOWN" in capsys.readouterr().err


def test_feed_failure_without_json_is_unknown(monkeypatch):
    class Proc:
        stdout, stderr, returncode = "", "ConnectionError via https://user:secret@proxy/simple", 1

    monkeypatch.setattr(audit.shutil, "which", lambda _name: "/bin/pip-audit")
    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **k: Proc())
    report, error = audit.run_pip_audit(ROOT / "requirements.lock", 5)
    assert report is None and "secret" not in error and "***@" in error


def test_committed_waivers_are_valid_and_workflow_is_advisory():
    audit.load_waivers(audit.DEFAULT_WAIVERS, dt.date.today())
    workflow = (ROOT / ".github" / "workflows" / "dependency-audit.yml").read_text(encoding="utf-8")
    assert "pull_request" not in workflow and "workflow_dispatch" in workflow
    assert "dependency-audit" not in (ROOT / "scripts" / "check").read_text(encoding="utf-8")
