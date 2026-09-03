#!/usr/bin/env python3
"""Validate that the repository roster is an authoritative SharePoint snapshot.

The registration CSV is source-managed: SharePoint replaces the complete
snapshot.  The companion source-state file fingerprints that snapshot and
records the monotonically increasing GitHub sync issue number.  This prevents
an older branch/merge from silently resurrecting teams removed in SharePoint.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

CSV_PATH = Path("inputs/registrations/registered-teams.csv")
STATE_PATH = Path("inputs/registrations/source-state.json")
REQUIRED_COLUMNS = ("club", "label", "age_group")


def _git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()  # noqa: S324 - Git object identity, not security


def _read_state_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        state = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} er ikke gyldig JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise ValueError(f"{label} må være et JSON-objekt")
    return state


def _read_git_file(ref: str, path: Path) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path.as_posix()}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _csv_team_count(data: bytes) -> int:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{CSV_PATH} må være UTF-8: {exc}") from exc
    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != REQUIRED_COLUMNS:
        raise ValueError(
            f"{CSV_PATH} må ha kolonnene {', '.join(REQUIRED_COLUMNS)} i denne rekkefølgen"
        )
    count = 0
    for line_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue
        missing = [column for column in REQUIRED_COLUMNS if not (row.get(column) or "").strip()]
        if missing:
            raise ValueError(
                f"{CSV_PATH}:{line_number} mangler verdi i {', '.join(missing)}"
            )
        count += 1
    return count


def _validate_current() -> tuple[dict[str, Any], bytes]:
    if not CSV_PATH.exists():
        raise ValueError(f"Mangler {CSV_PATH}")
    if not STATE_PATH.exists():
        raise ValueError(
            f"Mangler {STATE_PATH}; registreringsdata må komme fra SharePoint-sync"
        )

    csv_bytes = CSV_PATH.read_bytes()
    state = _read_state_bytes(STATE_PATH.read_bytes(), label=str(STATE_PATH))

    if state.get("source") != "sharepoint":
        raise ValueError(f"{STATE_PATH}: source må være 'sharepoint'")

    issue_number = state.get("source_issue_number")
    if not isinstance(issue_number, int) or issue_number <= 0:
        raise ValueError(f"{STATE_PATH}: source_issue_number må være et positivt heltall")

    actual_blob_sha = _git_blob_sha(csv_bytes)
    if state.get("csv_git_blob_sha") != actual_blob_sha:
        raise ValueError(
            "Registrerings-CSV matcher ikke SharePoint-kilden: "
            f"state={state.get('csv_git_blob_sha')!r}, faktisk={actual_blob_sha}. "
            "Kjør SharePoint-synkronisering i stedet for å redigere CSV-en manuelt."
        )

    actual_team_count = _csv_team_count(csv_bytes)
    if state.get("team_count") != actual_team_count:
        raise ValueError(
            f"Team count mismatch: state={state.get('team_count')!r}, faktisk={actual_team_count}"
        )

    return state, csv_bytes


def _validate_against_base(state: dict[str, Any], csv_bytes: bytes, base_ref: str) -> None:
    base_state_bytes = _read_git_file(base_ref, STATE_PATH)
    if base_state_bytes is None:
        # First introduction of source-state: there is no monotonic baseline yet.
        return

    base_state = _read_state_bytes(base_state_bytes, label=f"{base_ref}:{STATE_PATH}")
    base_issue = base_state.get("source_issue_number")
    current_issue = state.get("source_issue_number")
    if isinstance(base_issue, int) and isinstance(current_issue, int) and current_issue < base_issue:
        raise ValueError(
            f"SharePoint-generasjonen gikk bakover: {current_issue} < {base_issue}. "
            "En eldre branch/snapshot forsøker trolig å gjeninnføre fjernede lag."
        )

    base_csv = _read_git_file(base_ref, CSV_PATH)
    if base_csv is not None and base_csv != csv_bytes and current_issue == base_issue:
        raise ValueError(
            "Registrerings-CSV er endret uten en nyere SharePoint-generasjon. "
            "Fjernede/endrede lag skal komme fra en ny SharePoint-sync."
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-ref",
        help="Optional Git ref to reject stale/regressing SharePoint generations against",
    )
    args = parser.parse_args()

    try:
        state, csv_bytes = _validate_current()
        if args.base_ref:
            _validate_against_base(state, csv_bytes, args.base_ref)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        "registration snapshot OK: "
        f"SharePoint issue #{state['source_issue_number']}, "
        f"{state['team_count']} lag, blob {state['csv_git_blob_sha']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
