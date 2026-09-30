"""Baseline-aware admissibility policy for accepted canonical exceptions.

Rule-engine facts and admissibility of a proposed canonical mutation are two
different questions. The planning verifier answers the first one strictly: a
governing-floor shortfall, a real overlap, an actual playing shortfall or an
unverified placement is a hard fact regardless of workflow. This module owns
the second question for *accepted legacy exceptions*: an exact,
source-confirmed canonical fact that the season already carries.

An accepted exception is revision-bound and scoped to the precise facts the
operator accepted (rule, tournament, host, arena, date, start, end and
duration). It

* stays visible in findings/audit/export as a durable follow-up finding,
* does not block an unrelated canonical mutation, and
* never authorizes a new, worsened or materially modified violation, because
  the underlying matcher
  (:func:`tournament_scheduler.calendar_bookings.accepted_booking_interval_evidence`)
  requires an exact fact match.

The module also classifies a candidate's hard violations against the
authoritative baseline (``resolved``/``unchanged_accepted``/``unchanged_unaccepted``/
``introduced``/``worsened``/``materially_modified``) so every caller can report
the same structured evidence. It is deliberately conservative: an *unchanged
unaccepted* legacy violation is visible debt but stays blocking. Nothing here
waives a genuine overlap, a playing shortfall, a stale/wrong source or an
unverified placement, and there is no per-id allowlist or blanket bypass.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

from tournament_scheduler.calendar_bookings import (
    accepted_booking_interval_evidence,
    governing_floor_finding,
    tournament_occupancy_interval_facts,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

# Rule codes for which an exact, source-confirmed canonical exception may
# downgrade an otherwise-hard violation to a durable follow-up finding. Kept as
# a small explicit set (not an id allowlist): each entry names a rule whose
# acceptance evidence the canonical projection can prove exactly.
ACCEPTED_EXCEPTION_RULE_CODES = frozenset({"ice_time_governing_minimum"})

# Baseline-vs-candidate classification buckets.
RESOLVED = "resolved"
UNCHANGED_ACCEPTED = "unchanged_accepted"
UNCHANGED_UNACCEPTED = "unchanged_unaccepted"
INTRODUCED = "introduced"
WORSENED = "worsened"
MATERIALLY_MODIFIED = "materially_modified"

# Facts a rule is *about*. A candidate that changes any of them is
# re-evaluated even when the rule code and tournament id are unchanged, so an
# accepted exception cannot silently transfer to a modified interval.
_MATERIAL_FACT_FIELDS = (
    "date",
    "start_time",
    "end_time",
    "arena",
    "host_club",
    "age_group",
    "club",
    "team",
    "opponent",
    "scope",
    "field",
)


def _tournament_by_id(candidate: Mapping[str, Any], tournament_id: str) -> Mapping[str, Any] | None:
    for tournament in candidate.get("tournaments") or []:
        if isinstance(tournament, Mapping) and str(tournament.get("id") or "") == tournament_id:
            return tournament
    return None


def accepted_exception_identity(
    problem: Mapping[str, Any] | None,
    tournament: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the exact accepted-exception identity covering this tournament.

    ``None`` means no currently valid, fact-matching source evidence covers the
    candidate tournament, so the violation stays hard. The identity is
    intentionally JSON-safe and carries the rule, entity and occupied interval
    plus the evidence authority/record id for audit traceability.
    """

    evidence = accepted_booking_interval_evidence(problem, tournament)
    if evidence is None:
        return None
    interval = tournament_occupancy_interval_facts(tournament, problem)
    tournament_id = str(tournament.get("id") or "")
    return {
        "rule": "ice_time_governing_minimum",
        "tournament_id": tournament_id,
        "authority": str(evidence.get("authority") or ""),
        "record_id": str(evidence.get("record_id") or ""),
        "source_assertion_id": str(evidence.get("source_assertion_id") or ""),
        "interval": dict(interval),
    }


def _accepted_exception_finding(
    tournament: Mapping[str, Any],
    violation: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the durable follow-up finding for one matched accepted exception.

    The finding keeps the verifier's structured fields (so findings/audit/export
    agree on the deficit) and adds the exact accepted interval, its evidence
    authority and the structured exception identity. ``governing_floor_finding``
    remains the message owner for the shortfall.
    """

    accepted_minutes = (identity.get("interval") or {}).get("duration_minutes")
    floor_finding = governing_floor_finding(tournament, accepted_minutes)
    finding = dict(floor_finding or violation)
    # Preserve any structured verifier fields the message owner does not repeat.
    for key, value in violation.items():
        finding.setdefault(key, value)
    finding["accepted_booking_interval"] = True
    finding["accepted_exception"] = dict(identity)
    return finding


def _split_accepted_violations(
    violations: Iterable[Mapping[str, Any]],
    *,
    matches: Callable[[Mapping[str, Any]], bool],
    tournament: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Split violations into accepted follow-up findings and blockers.

    Only a violation whose rule is accepted and that ``matches`` the exception
    scope is reclassified; every other violation (including a changed interval,
    a real overlap and an unverified below-floor placement) stays blocking.
    """

    accepted: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    for violation in violations:
        item = dict(violation)
        code = str(item.get("code") or "")
        if code in ACCEPTED_EXCEPTION_RULE_CODES and matches(item):
            accepted.append(_accepted_exception_finding(tournament, item, identity))
            continue
        blocking.append(item)
    return {"accepted_exceptions": accepted, "blocking_violations": blocking}


def reclassify_accepted_exceptions(
    problem: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    violations: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Split hard violations for any tournament with exact accepted evidence.

    Returns ``{"accepted_exceptions": [...], "blocking_violations": [...]}``.
    This is the evidence-projection path used by findings/audit/export and by
    maintenance when the durable accepted evidence already exists.
    """

    accepted: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    for violation in violations:
        item = dict(violation)
        code = str(item.get("code") or "")
        if code in ACCEPTED_EXCEPTION_RULE_CODES:
            tournament_id = str(item.get("tournament_id") or "")
            tournament = _tournament_by_id(candidate, tournament_id)
            if tournament is not None:
                identity = accepted_exception_identity(problem, tournament)
                if identity is not None:
                    accepted.append(_accepted_exception_finding(tournament, item, identity))
                    continue
        blocking.append(item)
    return {"accepted_exceptions": accepted, "blocking_violations": blocking}


def reclassify_accepted_source_interval(
    problem: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    violations: Iterable[Mapping[str, Any]],
    *,
    tournament_id: str,
    accepted_interval: Mapping[str, Any] | None,
    authority: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Split violations for one just-aligned in-memory accepted source interval.

    Writer paths (confirming a calendar booking / recording a manual assertion)
    have not persisted the evidence record yet, so they pass the accepted source
    interval they aligned the tournament to. The interval must still equal the
    candidate's current occupied interval exactly, so a changed/rejected
    interval is never grandfathered.
    """

    tournament = _tournament_by_id(candidate, str(tournament_id))
    if tournament is None or not accepted_interval:
        return {"accepted_exceptions": [], "blocking_violations": [dict(v) for v in violations]}
    current = tournament_occupancy_interval_facts(tournament, problem)
    for key in ("date", "start_time", "duration_minutes", "end_time"):
        if str(accepted_interval.get(key) or "") != str(current.get(key) or ""):
            return {"accepted_exceptions": [], "blocking_violations": [dict(v) for v in violations]}
    identity = {
        "rule": "ice_time_governing_minimum",
        "tournament_id": str(tournament_id),
        "authority": str(authority or ""),
        "record_id": "",
        "source_assertion_id": "",
        "interval": {
            key: str(current.get(key) or "")
            for key in ("date", "start_time", "duration_minutes", "end_time")
        },
    }
    return _split_accepted_violations(
        violations,
        matches=lambda item: str(item.get("tournament_id") or "") == str(tournament_id),
        tournament=tournament,
        identity=identity,
    )


def _violation_identity(violation: Mapping[str, Any]) -> tuple[str, ...]:
    """Return a stable semantic identity for one structured violation.

    The identity is scoped to the rule and the exact entity it covers, so a
    candidate that changes the covered tournament (or the only identifier a
    non-tournament rule exposes) produces a different identity and is reported
    as a new violation rather than inherited from the baseline.
    """

    code = str(violation.get("code") or "")
    tournament_id = str(violation.get("tournament_id") or "")
    if tournament_id:
        return (code, tournament_id)
    parts: list[str] = [code]
    for key in ("club", "team", "age_group", "scope", "field", "date"):
        value = violation.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return tuple(parts)


def _violation_fact_fingerprint(violation: Mapping[str, Any]) -> str:
    """Return a stable fingerprint of the scheduling facts a violation is about."""

    facts = {
        key: str(violation.get(key) or "")
        for key in _MATERIAL_FACT_FIELDS
        if key in violation
    }
    return stable_payload_sha256(facts)


def violation_severity(violation: Mapping[str, Any]) -> float:
    """Return a deterministic higher-is-worse score for one violation.

    A governing-floor shortfall scores its missing minutes. Participation and
    other measurable deviations score their absolute size. Binary hard rules
    score ``1.0``. The comparison sums per identity, so migrating a deficit
    between entities under the same identity is not misread as a new rule.
    """

    try:
        minimum = violation.get("minimum_required_minutes")
        configured = violation.get("configured_ice_time_minutes")
        if minimum is not None and configured is not None:
            return float(max(0, int(minimum) - int(configured)))
    except (TypeError, ValueError):
        pass
    for key in ("deviation", "shortfall", "missing_minutes", "gap_days"):
        if violation.get(key) is not None:
            try:
                return float(abs(int(violation[key])))
            except (TypeError, ValueError):
                continue
    return 1.0


def _grouped(
    violations: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for violation in violations:
        grouped.setdefault(_violation_identity(violation), []).append(dict(violation))
    return grouped


def classify_candidate_violations(
    baseline_violations: Iterable[Mapping[str, Any]],
    candidate_violations: Iterable[Mapping[str, Any]],
    *,
    accepted_exception_violations: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Classify a candidate's hard violations against the authoritative baseline.

    Returns the explicit ``resolved``/``unchanged_accepted``/
    ``unchanged_unaccepted``/``introduced``/``worsened``/``materially_modified``
    buckets. ``acceptable`` is true only when the candidate introduces no new
    violation, worsens no existing one and leaves no unchanged *unaccepted*
    debt: an unchanged accepted exception is admissible (it stays visible as a
    follow-up finding), while unchanged unaccepted legacy debt remains blocking
    and is reported as ``unchanged_unaccepted``.

    ``accepted_exception_violations`` names the candidate violations already
    matched to exact accepted evidence (see :func:`reclassify_accepted_exceptions`),
    so the same comparison is used by every caller without re-deriving evidence.
    """

    baseline = _grouped(list(baseline_violations))
    candidate = _grouped(list(candidate_violations))
    accepted_keys = {_violation_identity(item) for item in accepted_exception_violations}

    resolved: list[dict[str, Any]] = []
    unchanged_accepted: list[dict[str, Any]] = []
    unchanged_unaccepted: list[dict[str, Any]] = []
    introduced: list[dict[str, Any]] = []
    worsened: list[dict[str, Any]] = []
    materially_modified: list[dict[str, Any]] = []

    for key, entries in candidate.items():
        previous = baseline.get(key)
        if previous is None:
            introduced.extend(entries)
            continue
        previous_severity = sum(violation_severity(item) for item in previous)
        candidate_severity = sum(violation_severity(item) for item in entries)
        previous_fingerprints = {_violation_fact_fingerprint(item) for item in previous}
        candidate_fingerprints = {_violation_fact_fingerprint(item) for item in entries}
        if previous_fingerprints != candidate_fingerprints:
            materially_modified.extend(entries)
        elif candidate_severity > previous_severity:
            worsened.extend(entries)
        elif candidate_severity < previous_severity:
            # A strictly smaller unchanged-facts deficit is an improvement, not
            # a regression; it is not a blocking bucket.
            resolved.extend(previous)
        elif key in accepted_keys:
            unchanged_accepted.extend(entries)
        else:
            unchanged_unaccepted.extend(entries)

    for key, entries in baseline.items():
        if key not in candidate:
            resolved.extend(entries)

    regressions = introduced + worsened + materially_modified
    return {
        "acceptable": not regressions and not unchanged_unaccepted,
        "regressions": regressions,
        "resolved": resolved,
        "unchanged_accepted": unchanged_accepted,
        "unchanged_unaccepted": unchanged_unaccepted,
        "introduced": introduced,
        "worsened": worsened,
        "materially_modified": materially_modified,
        "baseline_violations": [
            dict(item) for group in baseline.values() for item in group
        ],
        "candidate_violations": [
            dict(item) for group in candidate.values() for item in group
        ],
    }


__all__ = [
    "ACCEPTED_EXCEPTION_RULE_CODES",
    "INTRODUCED",
    "MATERIALLY_MODIFIED",
    "RESOLVED",
    "UNCHANGED_ACCEPTED",
    "UNCHANGED_UNACCEPTED",
    "WORSENED",
    "accepted_exception_identity",
    "classify_candidate_violations",
    "reclassify_accepted_exceptions",
    "reclassify_accepted_source_interval",
    "violation_severity",
]
