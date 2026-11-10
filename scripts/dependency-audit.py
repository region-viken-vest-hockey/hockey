#!/usr/bin/env python3
"""Advisory vulnerability audit of the hash-locked Python dependency set.

Thin wrapper over PyPA ``pip-audit`` (no custom scanner or advisory database).
It audits ``requirements.lock`` exactly as pinned -- every runtime and optional
extra -- without resolving or installing anything, and classifies the outcome so
a failed or partial scan can never be mistaken for a clean one.

Exit status:
  0  scan completed, no unwaived findings
  1  scan completed, unwaived vulnerabilities found
  2  scan UNKNOWN: pip-audit missing, advisory feed/network failure, unparseable
     output, or dependencies skipped (never a green result)
  3  waiver file invalid (fail closed)

This is deliberately not part of ``make check``: it needs network access to the
advisory feed. See docs/ci.md "Dependency vulnerability audit".
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "requirements.lock"
DEFAULT_WAIVERS = ROOT / "docs" / "dependency-audit-waivers.json"
MAX_WAIVER_DAYS = 180
REQUIRED_WAIVER_FIELDS = ("package", "advisory", "reason", "expires")
_URL_CREDENTIALS = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")


class WaiverError(ValueError):
    """The waiver file is malformed, incomplete, or over-long."""


def redact(text: str) -> str:
    """Strip URL userinfo so proxy/index credentials never reach logs."""
    return _URL_CREDENTIALS.sub(r"\1***@", text)


def load_waivers(path: Path, today: dt.date) -> list[dict]:
    if not path.exists():
        raise WaiverError(f"{path}: waiver file not found (use an explicit empty waivers list when none apply)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WaiverError(f"{path}: unreadable waiver file: {exc}") from exc
    entries = data.get("waivers") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise WaiverError(f'{path}: expected an object with a "waivers" list')
    for entry in entries:
        if not isinstance(entry, dict):
            raise WaiverError(f"{path}: waiver entries must be objects")
        missing = [f for f in REQUIRED_WAIVER_FIELDS if not str(entry.get(f, "")).strip()]
        if missing:
            raise WaiverError(f"{path}: waiver {entry.get('advisory', '?')} missing {', '.join(missing)}")
        try:
            expires = dt.date.fromisoformat(str(entry["expires"]))
        except ValueError as exc:
            raise WaiverError(f"{path}: waiver {entry['advisory']} has invalid expires date") from exc
        if (expires - today).days > MAX_WAIVER_DAYS:
            raise WaiverError(f"{path}: waiver {entry['advisory']} expires more than {MAX_WAIVER_DAYS} days out")
        entry["_expires"] = expires
    return entries


def _waiver_for(name: str, ids: set[str], waivers: list[dict], today: dt.date) -> dict | None:
    for waiver in waivers:
        if waiver["package"].lower() == name.lower() and waiver["advisory"] in ids and waiver["_expires"] >= today:
            return waiver
    return None


def classify(report: dict, waivers: list[dict], today: dt.date) -> tuple[list[dict], list[dict], list[str]]:
    """Return (findings, waived, skipped) from a pip-audit JSON report."""
    findings: list[dict] = []
    waived: list[dict] = []
    skipped: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for dep in report.get("dependencies", []):
        if "skip_reason" in dep:
            skipped.append(f"{dep.get('name')}: {dep['skip_reason']}")
            continue
        for vuln in dep.get("vulns", []):
            key = (dep["name"], dep["version"], vuln["id"])
            if key in seen:
                continue
            seen.add(key)
            ids = {vuln["id"], *vuln.get("aliases", [])}
            row = {
                "package": dep["name"],
                "version": dep["version"],
                "advisory": vuln["id"],
                "aliases": sorted(ids - {vuln["id"]}),
                "fixed": vuln.get("fix_versions") or [],
            }
            waiver = _waiver_for(dep["name"], ids, waivers, today)
            if waiver:
                waived.append({**row, "waiver_expires": waiver["expires"], "reason": waiver["reason"]})
            else:
                findings.append(row)
    return findings, waived, skipped


def locked_pins(lock: Path) -> set[str]:
    """Normalised names of every ``name==version`` pin in the lock file."""
    pins = set()
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
        if match:
            pins.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
    return pins


def report_problem(report: dict, expected: set[str]) -> str:
    """Return why *report* is not a complete audit of *expected*, or '' when it is."""
    deps = report.get("dependencies")
    if not isinstance(deps, list) or not deps:
        return "pip-audit report lists no dependencies"
    seen: set[str] = set()
    for dep in deps:
        if not isinstance(dep, dict) or not isinstance(dep.get("name"), str):
            return "pip-audit report has a malformed dependency entry"
        if "skip_reason" not in dep and not (isinstance(dep.get("version"), str) and isinstance(dep.get("vulns"), list)):
            return f"pip-audit report has no vulnerability result for {dep['name']}"
        seen.add(re.sub(r"[-_.]+", "-", dep["name"]).lower())
    missing = sorted(expected - seen)
    if missing:
        return f"pip-audit report does not cover locked dependencies: {', '.join(missing[:10])}"
    return ""


def run_pip_audit(lock: Path, timeout: int) -> tuple[dict | None, str]:
    """Run pip-audit; return (report, error). report is None when the scan is unknown."""
    exe = shutil.which("pip-audit")
    if exe is None:
        return None, "pip-audit is not installed (pip install pip-audit)"
    cmd = [exe, "-r", str(lock), "--require-hashes", "--disable-pip", "--format", "json", "--progress-spinner", "off"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, f"pip-audit timed out after {timeout}s"
    # pip-audit exits 0 (clean) or 1 (vulnerabilities found); any other status is a failed scan even with JSON.
    if proc.returncode not in (0, 1):
        return None, redact(proc.stderr.strip()[-800:]) or f"pip-audit exited {proc.returncode}"
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        # pip-audit exits 1 for BOTH findings and feed failures; only parseable JSON proves a completed scan.
        return None, redact(proc.stderr.strip()[-800:]) or f"pip-audit exited {proc.returncode} with no report"
    if not isinstance(report, dict) or "dependencies" not in report:
        return None, "pip-audit produced an unrecognised report"
    problem = report_problem(report, locked_pins(lock))
    if problem:
        return None, problem
    return report, ""


def render(findings: list[dict], waived: list[dict], skipped: list[str], total: int) -> str:
    lines = [f"Audited {total} locked dependencies."]
    for row in findings:
        fixed = ", ".join(row["fixed"]) or "no fix released"
        alias = f" ({', '.join(row['aliases'])})" if row["aliases"] else ""
        lines.append(f"VULNERABLE {row['package']}=={row['version']}  {row['advisory']}{alias}  fixed in: {fixed}")
    for row in waived:
        lines.append(f"WAIVED     {row['package']}=={row['version']}  {row['advisory']}  until {row['waiver_expires']}: {row['reason']}")
    for item in skipped:
        lines.append(f"SKIPPED    {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--waivers", type=Path, default=DEFAULT_WAIVERS)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    today = dt.date.today()
    try:
        waivers = load_waivers(args.waivers, today)
    except WaiverError as exc:
        print(f"dependency-audit: INVALID WAIVERS: {exc}", file=sys.stderr)
        return 3
    report, error = run_pip_audit(args.lock, args.timeout)
    if report is None:
        print(f"dependency-audit: UNKNOWN (scan did not complete): {error}", file=sys.stderr)
        return 2
    findings, waived, skipped = classify(report, waivers, today)
    print(render(findings, waived, skipped, len(report["dependencies"])))
    if skipped:
        print("dependency-audit: UNKNOWN (dependencies could not be audited)", file=sys.stderr)
        return 2
    if findings:
        print(f"dependency-audit: {len(findings)} unwaived finding(s)", file=sys.stderr)
        return 1
    print("dependency-audit: OK (no unwaived findings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
