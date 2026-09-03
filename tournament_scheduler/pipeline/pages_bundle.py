"""Build a sanitized public Pages bundle and privacy report (issue #18).

``pages_publish.publish()`` (issue #17) commits whatever directory it is
given to the ``gh-pages`` branch verbatim — it doesn't know or care what's
in it. This module is the gate in front of that: it turns a raw Stage 4
export directory into a separate public bundle, keeping the published plan
and its downloads while stripping/redacting anything that looks internal or
sensitive.

Fail-closed defaults:

- Only an explicit allowlist of filenames is ever copied — by default that
  includes the public HTML/ICS views plus the plan workbook/CSV downloads;
  per-club review packets, Spond exports, and unknown file types stay out
  unless explicitly added.
- A probable credential, private key, or bearer-URL/token anywhere in an
  included file blocks the whole bundle (``CapabilityResult.blocked``) —
  the operator must review and either fix the source or explicitly
  acknowledge the finding via ``allow_findings`` before it can be published.
- Local filesystem paths and contact info (emails, phone numbers in a
  labeled context like ``tel:``/``Tlf``) are redacted rather than blocking,
  since they're common accidental inclusions rather than a leak severe
  enough to halt publication, but every redaction is recorded in the
  privacy report.

See ``pipeline/pages_publish.py`` for what happens to the bundle this
produces, and ``docs/ai-operator-roadmap.md`` for the product rationale.
"""
from __future__ import annotations

import html as _html
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .capability_result import CapabilityResult

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# The only files copied into the public bundle unless the caller extends
# this via `allowed_filenames`. The default bundle includes the public
# season-plan views plus the downloadable workbook/CSV exports, while
# review_packets/ and Spond exports remain excluded.
DEFAULT_ALLOWED_FILENAMES: frozenset[str] = frozenset(
    {
        "season_plan.html",
        "season_plan_report.html",
        "manual_schedule.html",
        "calendars.html",
        "input.html",
        "season_plan.ics",
        "season_plan.xlsx",
        "season_plan.csv",
        "season_plan_overview.csv",
        "activities.json",
        "index.html",
    }
)

# File types eligible for inclusion at all, even if the filename is
# allowlisted — an unknown extension fails closed rather than being copied
# on trust.
DEFAULT_ALLOWED_DIRECTORIES: frozenset[str] = frozenset({"activities", "registered-teams"})
DEFAULT_EXCLUDED_PUBLIC_PATHS: frozenset[str] = frozenset({"registered-teams/validation-report.json"})

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".html", ".htm", ".css", ".js", ".json", ".ics", ".csv", ".xlsx", ".png", ".jpg", ".jpeg", ".svg", ".ico"}
)

_TEXT_EXTENSIONS: frozenset[str] = frozenset({".html", ".htm", ".css", ".js", ".json", ".ics", ".csv"})

_PRIVACY_REPORT_FILENAME = "pages_privacy_report.json"

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "generic_secret_assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|passwd|token|auth)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-.]{12,}['\"]?"
        ),
    ),
    ("bearer_header", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{10,}")),
    (
        "bearer_url",
        re.compile(r"(?i)[?&](access_token|api_key|token|auth)=[^&\s\"'<>]{6,}"),
    ),
]

_LOCAL_PATH_PATTERN = re.compile(
    r"(/Users/[^\s\"'<>]+|/home/[^\s\"'<>]+|[A-Za-z]:\\\\?Users\\\\?[^\s\"'<>]+)"
)
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Phone numbers are only treated as contact info in a labeled context
# (tel: link, or a "Tlf"/"Tel"/"Phone" label) — an unqualified run of
# digits is far too likely to be a date, id, or score to redact on sight.
_PHONE_PATTERN = re.compile(
    r"(?i)(tel:|\b(tlf|tel|phone)[:\s]*)\+?[0-9][0-9\s\-()]{5,}[0-9]"
)
_ROOT_ABSOLUTE_LINK_PATTERN = re.compile(r"((?:href|src)=[\"'])/(?!/)([^\"']*)")
_LINK_ATTRIBUTE_PATTERN = re.compile(r'(?P<attr>href|src)=(?P<quote>["\'])(?P<target>.*?)(?P=quote)', re.IGNORECASE)

_REDACTED = "[redacted]"


def _excluded_link_scope(target: str, excluded_file_names: set[str], excluded_directory_names: set[str]) -> str | None:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or target.startswith(("#", "mailto:", "tel:", "//")):
        return None

    path = parsed.path.lstrip("/")
    if not path:
        return None

    parts = [part for part in path.split("/") if part not in {"", ".", ".."}]
    if not parts:
        return None

    if parts[-1] in excluded_file_names:
        return "excluded_file"
    if any(part in excluded_directory_names for part in parts):
        return "excluded_directory"
    return None


def _rewrite_excluded_file_links(
    text: str,
    excluded_file_names: set[str],
    excluded_directory_names: set[str],
    *,
    source_file: str,
) -> tuple[str, list[dict[str, Any]]]:
    counts: dict[tuple[str, str, str, str], int] = {}

    def replace(match: re.Match[str]) -> str:
        attr = match.group("attr").lower()
        target = match.group("target")
        scope = _excluded_link_scope(target, excluded_file_names, excluded_directory_names)
        if scope is None:
            return match.group(0)

        escaped_target = _html.escape(target, quote=True)
        if attr == "href":
            action = "disabled"
            replacement = (
                f'href="#" aria-disabled="true" data-excluded-href="{escaped_target}" '
                'style="pointer-events:none;opacity:0.6;cursor:not-allowed"'
            )
        else:
            action = "removed"
            replacement = f'data-excluded-src="{escaped_target}"'

        key = (attr, target, action, scope)
        counts[key] = counts.get(key, 0) + 1
        return replacement

    rewritten = _LINK_ATTRIBUTE_PATTERN.sub(replace, text)
    rewrites: list[dict[str, Any]] = []
    for (attr, target, action, scope), count in counts.items():
        rewrites.append(
            {
                "file": source_file,
                "attribute": attr,
                "target": target,
                "action": action,
                "scope": scope,
                "replacement": "disabled href" if action == "disabled" else "removed src",
                "count": count,
            }
        )
    return rewritten, rewrites


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class PrivacyReport:
    included_files: list[str] = field(default_factory=list)
    excluded_files: list[dict[str, str]] = field(default_factory=list)
    redactions: list[dict[str, Any]] = field(default_factory=list)
    rewritten_links: list[dict[str, Any]] = field(default_factory=list)
    blocking_findings: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "included_files": list(self.included_files),
            "excluded_files": list(self.excluded_files),
            "redactions": list(self.redactions),
            "rewritten_links": list(self.rewritten_links),
            "blocking_findings": list(self.blocking_findings),
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _rewrite_root_absolute_links(text: str) -> str:
    """Rewrite ``href="/x"``/``src="/x"`` to a relative ``"x"``.

    A root-absolute link resolves against the site root, which breaks when
    the bundle is served from a GitHub Pages project subpath
    (``/<repo>/latest/`` or ``/<repo>/runs/<run-id>/``) instead of the
    domain root. Protocol-relative (``//host/...``) and normal
    ``http(s)://`` links are left untouched.
    """
    return _ROOT_ABSOLUTE_LINK_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}", text)


def _find_secrets(text: str, allow_findings: frozenset[str]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for name, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            matched_text = match.group(0)
            if any(allowed and allowed in matched_text for allowed in allow_findings):
                continue
            findings.append((name, matched_text))
    return findings


def _redact_contact_and_paths(text: str) -> tuple[str, list[tuple[str, int]]]:
    counts: list[tuple[str, int]] = []
    for category, pattern in (
        ("local_path", _LOCAL_PATH_PATTERN),
        ("contact_email", _EMAIL_PATTERN),
        ("contact_phone", _PHONE_PATTERN),
    ):
        text, n = pattern.subn(_REDACTED, text)
        if n:
            counts.append((category, n))
    return text, counts


def build_public_bundle(
    export_dir: str,
    output_dir: str,
    *,
    allowed_filenames: frozenset[str] | set[str] | None = None,
    allow_findings: frozenset[str] | set[str] | None = None,
) -> CapabilityResult:
    """Build a sanitized public bundle from *export_dir* into *output_dir*.

    Only top-level files in *export_dir* whose name is in
    *allowed_filenames* (default :data:`DEFAULT_ALLOWED_FILENAMES`) and
    whose extension is a known static-asset type are copied; everything
    else (including subdirectories such as ``review_packets/``) is
    excluded and recorded in the privacy report, not silently skipped.

    *allow_findings* is a set of literal substrings that, when they appear
    inside an otherwise-blocking secret match, mark that specific match as
    an acknowledged false positive instead of a blocker — for a human who
    has reviewed a flagged string and confirmed it isn't actually
    sensitive (e.g. a placeholder value in a fixture).

    Returns a :class:`CapabilityResult`:

    - ``blocked`` (``requires_human=True``) if any probable secret was
      found — *output_dir* is not left in a publishable state.
    - ``ok`` otherwise, with the bundle written to *output_dir* and the
      privacy report path in ``artifacts``.

    Never raises for anything past basic argument validation.
    """
    export_path = Path(export_dir)
    if not export_path.is_dir():
        return CapabilityResult.failed(
            f"Eksportmappe '{export_dir}' finnes ikke.", capability="pages_bundle"
        )

    names = frozenset(allowed_filenames) if allowed_filenames is not None else DEFAULT_ALLOWED_FILENAMES
    overrides = frozenset(allow_findings) if allow_findings is not None else frozenset()

    output_path = Path(output_dir)
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    report = PrivacyReport()
    excluded_file_names: set[str] = set()
    excluded_directory_names: set[str] = set()
    binary_assets: list[tuple[Path, str]] = []
    text_assets: list[tuple[Path, str, str]] = []

    def _consider_file(entry: Path, rel: str) -> None:
        if rel in DEFAULT_EXCLUDED_PUBLIC_PATHS:
            report.excluded_files.append(
                {"file": rel, "reason": "private validation metadata is not published"}
            )
            excluded_file_names.add(Path(rel).name)
            return

        if entry.suffix.lower() not in _ALLOWED_EXTENSIONS:
            report.excluded_files.append(
                {"file": rel, "reason": f"unknown/unapproved file type '{entry.suffix}'"}
            )
            excluded_file_names.add(Path(rel).name)
            return

        if entry.suffix.lower() not in _TEXT_EXTENSIONS:
            binary_assets.append((entry, rel))
            return

        try:
            text = entry.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            report.excluded_files.append(
                {"file": rel, "reason": "could not be read as UTF-8 text"}
            )
            excluded_file_names.add(Path(rel).name)
            return

        secrets = _find_secrets(text, overrides)
        if secrets:
            for category, matched_text in secrets:
                report.blocking_findings.append(
                    {"file": rel, "category": category, "detail": matched_text}
                )
            excluded_file_names.add(Path(rel).name)
            return

        text_assets.append((entry, rel, text))

    for entry in sorted(export_path.iterdir()):
        if entry.is_dir():
            if entry.name not in DEFAULT_ALLOWED_DIRECTORIES:
                report.excluded_files.append(
                    {"file": entry.name, "reason": "directory is not in the public directory allowlist"}
                )
                excluded_directory_names.add(entry.name)
                continue
            for child in sorted(path for path in entry.rglob("*") if path.is_file()):
                rel = child.relative_to(export_path).as_posix()
                _consider_file(child, rel)
            continue

        if entry.name not in names:
            report.excluded_files.append(
                {"file": entry.name, "reason": "not in the public filename allowlist"}
            )
            excluded_file_names.add(entry.name)
            continue

        _consider_file(entry, entry.name)

    if report.blocking_findings:
        report_path = output_path.parent / _PRIVACY_REPORT_FILENAME
        report.write(report_path)
        shutil.rmtree(output_path, ignore_errors=True)
        finding_summary = ", ".join(
            f"{f['file']}:{f['category']}" for f in report.blocking_findings
        )
        return CapabilityResult.blocked(
            f"Publisering blokkert — mulige hemmeligheter funnet: {finding_summary}",
            capability="pages_bundle",
            problems=[f"{f['file']}: {f['category']}" for f in report.blocking_findings],
            artifacts=[str(report_path)],
        )

    for entry, rel in binary_assets:
        destination = output_path / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry, destination)
        report.included_files.append(rel)

    for entry, rel, text in text_assets:
        text, redaction_counts = _redact_contact_and_paths(text)
        text = _rewrite_root_absolute_links(text)
        text, link_rewrites = _rewrite_excluded_file_links(
            text,
            excluded_file_names,
            excluded_directory_names,
            source_file=rel,
        )
        for category, count in redaction_counts:
            report.redactions.append({"file": rel, "category": category, "count": count})
        report.rewritten_links.extend(link_rewrites)

        destination = output_path / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        report.included_files.append(rel)

    report_path = output_path.parent / _PRIVACY_REPORT_FILENAME
    report.write(report_path)

    if report.blocking_findings:
        shutil.rmtree(output_path, ignore_errors=True)
        finding_summary = ", ".join(
            f"{f['file']}:{f['category']}" for f in report.blocking_findings
        )
        return CapabilityResult.blocked(
            f"Publisering blokkert — mulige hemmeligheter funnet: {finding_summary}",
            capability="pages_bundle",
            problems=[f"{f['file']}: {f['category']}" for f in report.blocking_findings],
            artifacts=[str(report_path)],
        )

    return CapabilityResult.ok(
        f"{len(report.included_files)} fil(er) godkjent for offentlig publisering, "
        f"{len(report.excluded_files)} ekskludert, {len(report.redactions)} redigering(er)",
        capability="pages_bundle",
        evidence=[
            f"included={len(report.included_files)}",
            f"excluded={len(report.excluded_files)}",
            f"redactions={len(report.redactions)}",
        ],
        artifacts=[str(output_path), str(report_path)],
    )
