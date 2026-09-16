"""Baseline-aware planning overlay for a promoted canonical season.

Once an operator deliberately promotes a verified candidate, the canonical
season state in :mod:`tournament_scheduler.season_state` becomes the
operational truth.  Later planning/repair must optimise *around* that
baseline instead of regenerating a wholly new season:

- approved / placement-locked tournaments are hard-preserve constraints --
  a candidate that moves or drops one is rejected before any canonical write;
- every other published tournament is still mutable, but changing it costs
  something, so a candidate should prefer the smallest change that resolves
  the problem.

This module owns that contract: it turns canonical schedule facts plus
decision state into a JSON-safe ``canonical_baseline`` section that can ride
inside the stable ``planning_problem``/config payloads, verifies the hard
locks, and measures a weighted change cost between the baseline and a
candidate.  It is a pure function library over the existing contracts: no
search, no I/O beyond loading canonical files, no planner internals.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

CANONICAL_BASELINE_SCHEMA_VERSION = 1

# Default canonical-state root, mirroring ``season_state.DEFAULT_SEASON_ROOT``
# without importing it (``season_state`` imports this module lazily).  The
# environment variable is an explicit override used by tests and hermetic
# tooling so a committed repo ``season/`` cannot silently change an unrelated
# planning/verification run.
DEFAULT_SEASON_ROOT = "season"
SEASON_ROOT_ENV_VAR = "RVV_CANONICAL_SEASON_ROOT"


def default_season_root() -> str:
    return os.environ.get(SEASON_ROOT_ENV_VAR) or DEFAULT_SEASON_ROOT


# Soft multiplier applied to the weighted change cost when it is folded into
# a search objective (see ``stage3_optimizer``).  Kept here next to the change
# weights so the "prefer the smallest change" policy has one home; a caller
# may override it through the problem's ``canonical_change_cost_scale`` key.
DEFAULT_CHANGE_COST_SCALE = 5.0

# Fields frozen by a placement lock.  ``host_club`` is the physical host;
# ``arena``/``start_time`` are the booked slot; ``date`` is the day.
PLACEMENT_FIELDS: Tuple[str, ...] = ("date", "arena", "host_club", "start_time")

# Change-cost classes ordered cheapest-to-most-disruptive.  A participant-only
# adjustment is cheaper than moving a published tournament's slot, which is
# cheaper than deleting/replacing it.  Weights are configurable soft policy,
# deliberately separate from the hard approval locks.
DEFAULT_CHANGE_WEIGHTS: Dict[str, float] = {
    "none": 0.0,
    "participants": 1.0,
    "placement": 3.0,
    "replacement": 10.0,
}

_CHANGE_CATEGORIES: Tuple[str, ...] = ("none", "participants", "placement", "replacement")


def _team_identity(team: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    label = team.get("label")
    if not label:
        return None
    return (str(team.get("club") or ""), str(label), str(team.get("age_group") or ""))


def _participants(tournament: Dict[str, Any]) -> List[List[str]]:
    identities = {
        identity
        for team in tournament.get("teams", []) or []
        if (identity := _team_identity(team)) is not None
    }
    return sorted([list(identity) for identity in identities])


def _tournament_snapshot(tournament: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(tournament.get("id") or ""),
        "age_group": tournament.get("age_group"),
        "date": tournament.get("date"),
        "arena": tournament.get("arena"),
        "host_club": tournament.get("host_club"),
        "start_time": tournament.get("start_time"),
        "participants": _participants(tournament),
    }


def _normalized_approval_payload(tournament: Dict[str, Any]) -> Dict[str, Any]:
    """Return the exact protected scheduling state an approval covers.

    Normalized so semantically irrelevant ordering (the sequence teams happen
    to appear in, or the order games are listed) does not invalidate an
    approval, while any change to a protected field -- id, age group, date,
    arena, physical host, start time/occupied interval, participant roster or
    deterministic game/round structure -- does.
    """
    teams = sorted(
        (
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or ""),
        )
        for team in tournament.get("teams", []) or []
        if isinstance(team, dict) and team.get("label")
    )
    games = sorted(
        (
            int(game.get("round_number") or 0),
            str(game.get("home") or ""),
            str(game.get("away") or ""),
            int(game.get("parallel_slot") or 0),
        )
        for game in tournament.get("games", []) or []
        if isinstance(game, dict)
    )
    return {
        "id": str(tournament.get("id") or ""),
        "age_group": tournament.get("age_group"),
        "date": tournament.get("date"),
        "arena": tournament.get("arena"),
        "host_club": tournament.get("host_club"),
        "start_time": tournament.get("start_time"),
        "teams": [list(identity) for identity in teams],
        "games": [list(game) for game in games],
    }


def approval_fingerprint(tournament: Dict[str, Any]) -> str:
    """Return the protected-fields fingerprint an approval records.

    The fingerprint covers the normalized protected scheduling state (see
    :func:`_normalized_approval_payload`), so any real change to a protected
    field invalidates a stored approval instead of silently keeping it
    "approved".
    """
    # Imported lazily: ``tournament_scheduler.pipeline`` eagerly imports the
    # planning contract, which imports this module, so a top-level import
    # here would be a circular import.
    from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

    return stable_payload_sha256(_normalized_approval_payload(tournament))


def resolve_approval(
    record: Optional[Dict[str, Any]],
    tournament: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve a decision record plus the current tournament into effective state.

    A stored approval only counts while its ``approved_fingerprint`` still
    matches the current tournament.  A mismatch is a deterministic
    ``stale_approval``: the tournament is reported as no longer approved and
    its locks are dropped, so a legacy/buggy mutation path can never keep an
    approval (or its protection) alive unnoticed.  Locks without any stored
    approval fingerprint keep the legacy explicit-lock behavior.
    """
    record = record or {}
    current_fingerprint = approval_fingerprint(tournament)
    approved_fingerprint = record.get("approved_fingerprint")
    stale = bool(approved_fingerprint) and approved_fingerprint != current_fingerprint
    if str(record.get("status") or "") == "stale_approval":
        # An approval invalidated by a mutation stays invalid until the
        # operator explicitly reapproves, even if the tournament later
        # reverts to identical bytes.
        stale = True
    raw_status = str(record.get("status") or "pending_review")
    status = "stale_approval" if stale else raw_status
    return {
        "status": status,
        "stale": stale,
        "approved": status == "approved",
        "placement_locked": bool(record.get("placement_locked")) and not stale,
        "participants_locked": bool(record.get("participants_locked")) and not stale,
        "approved_fingerprint": approved_fingerprint,
        "current_fingerprint": current_fingerprint,
        "approved_at": record.get("approved_at"),
        "approved_by": record.get("approved_by"),
        "note": record.get("note") or "",
        "stale_reason": record.get("stale_reason"),
    }


def build_canonical_baseline(
    schedule: Dict[str, Any],
    decisions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the JSON-safe ``canonical_baseline`` section from canonical state.

    A tournament is placement-locked when its decision record says so
    (``approve_tournament`` sets this by default) and participant-locked when
    ``participants_locked`` is set.  Every tournament appears in the
    ``tournaments`` snapshot so change cost can be measured even for
    published-but-not-approved tournaments.
    """
    plan = schedule.get("plan") or {}
    records = (decisions or {}).get("decisions") or {}
    locks: Dict[str, Dict[str, bool]] = {}
    snapshots: List[Dict[str, Any]] = []
    approvals: Dict[str, Dict[str, Any]] = {}
    stale_approvals: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for tournament in plan.get("tournaments", []) or []:
        snapshot = _tournament_snapshot(tournament)
        tournament_id = snapshot["id"]
        if not tournament_id:
            continue
        seen_ids.add(tournament_id)
        snapshots.append(snapshot)
        record = records.get(tournament_id) or {}
        resolved = resolve_approval(record, tournament)
        approval_entry = {
            "status": resolved["status"],
            "stale": resolved["stale"],
            "placement_locked": resolved["placement_locked"],
            "participants_locked": resolved["participants_locked"],
            "approved_fingerprint": resolved["approved_fingerprint"],
            "current_fingerprint": resolved["current_fingerprint"],
            "approved_at": resolved["approved_at"],
            "approved_by": resolved["approved_by"],
            "note": resolved["note"],
        }
        approvals[tournament_id] = approval_entry
        if resolved["placement_locked"] or resolved["participants_locked"]:
            locks[tournament_id] = {
                "placement": resolved["placement_locked"],
                "participants": resolved["participants_locked"],
            }
        if resolved["stale"]:
            stale_approvals.append(
                {
                    "code": "stale_approval",
                    "tournament_id": tournament_id,
                    "message": (
                        f"Tournament {tournament_id} changed after it was approved; "
                        "the stored approval no longer matches and must be reapproved"
                    ),
                    "approved_fingerprint": resolved["approved_fingerprint"],
                    "current_fingerprint": resolved["current_fingerprint"],
                    "approved_at": resolved["approved_at"],
                    "approved_by": resolved["approved_by"],
                    "note": resolved["note"],
                    "stale_reason": resolved.get("stale_reason"),
                }
            )
    # A decision record that still claims an approval for a tournament the
    # canonical schedule no longer has is an orphaned approval, not a valid
    # one.  Surface it instead of silently ignoring the leftover state.
    orphaned_approvals: List[Dict[str, Any]] = []
    for tournament_id, record in records.items():
        if tournament_id in seen_ids:
            continue
        if not (record or {}).get("approved_fingerprint"):
            continue
        orphaned_approvals.append(
            {
                "code": "orphaned_approval",
                "tournament_id": tournament_id,
                "message": (
                    f"Approval for tournament {tournament_id} has no matching tournament "
                    "in the canonical schedule"
                ),
                "approved_fingerprint": record.get("approved_fingerprint"),
                "approved_at": record.get("approved_at"),
                "approved_by": record.get("approved_by"),
            }
        )
    locked_count = sum(
        1 for lock in locks.values() if lock.get("placement") or lock.get("participants")
    )
    approval_summary = {
        "total": len(seen_ids),
        "approved_count": sum(
            1 for entry in approvals.values() if entry["status"] == "approved"
        ),
        "stale_count": len(stale_approvals),
        "orphaned_count": len(orphaned_approvals),
        "locked_count": locked_count,
        "pending_count": sum(
            1 for entry in approvals.values() if entry["status"] == "pending_review"
        ),
    }
    return {
        "schema_version": CANONICAL_BASELINE_SCHEMA_VERSION,
        "season": schedule.get("season"),
        "revision": schedule.get("revision") or schedule.get("fingerprint"),
        "locks": locks,
        "tournaments": snapshots,
        "approvals": approvals,
        "stale_approvals": stale_approvals,
        "orphaned_approvals": orphaned_approvals,
        "approval_summary": approval_summary,
    }


def load_canonical_baseline(
    season: str,
    *,
    root: "str | os.PathLike[str]" = "season",
) -> Dict[str, Any]:
    """Load the baseline overlay for *season* from canonical Git-backed state."""
    from tournament_scheduler.season_state import load_decisions, load_schedule

    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    return build_canonical_baseline(schedule, decisions)


def canonical_baseline_for_window(
    start_year: int,
    end_year: int,
    *,
    root: "str | os.PathLike[str]" = "season",
) -> Optional[Dict[str, Any]]:
    """Return the baseline for ``<start_year>-<end_year>`` or ``None`` if absent."""
    from tournament_scheduler.season_state import SeasonStateError

    season = f"{start_year}-{end_year}"
    if not os.path.exists(os.path.join(str(root), season, "schedule.json")):
        return None
    try:
        return load_canonical_baseline(season, root=root)
    except SeasonStateError:
        return None


def candidate_season_ids(start: date, end: date) -> List[str]:
    """Plausible canonical season ids for a planning window, most specific first.

    A season id is ``<first-year>-<second-year>``.  A planning window can be
    expressed with either calendar year as its anchor (a Sep-Apr season, a
    Jan-Apr spring window inside an ongoing season, ...), so every season
    whose window could contain this planning window is a candidate; callers
    pick the first one that actually exists on disk.
    """
    ids: List[str] = [f"{start.year}-{end.year}"]
    for year in (end.year, start.year):
        for candidate in (f"{year - 1}-{year}", f"{year}-{year + 1}"):
            if candidate not in ids:
                ids.append(candidate)
    return ids


def resolve_canonical_season(
    config: Optional[Dict[str, Any]],
    start: date,
    end: date,
    *,
    root: Optional["str | os.PathLike[str]"] = None,
) -> Optional[str]:
    """Return the season id whose canonical state applies to this planning window.

    An explicit ``config["canonical_season"]`` always wins.  Otherwise the
    configured season root (``config["canonical_season_root"]`` or the
    default ``season/``) is probed for the candidate ids above; the first
    one with a ``schedule.json`` is used.  Returns ``None`` when no canonical
    season applies -- the from-scratch planning path.
    """
    config = config or {}
    explicit = config.get("canonical_season")
    if explicit:
        return str(explicit)
    resolved_root = root or config.get("canonical_season_root") or default_season_root()
    for season in candidate_season_ids(start, end):
        if os.path.exists(os.path.join(str(resolved_root), season, "schedule.json")):
            return season
    return None


def resolve_canonical_state(
    config: Optional[Dict[str, Any]],
    start: date,
    end: date,
    *,
    root: Optional["str | os.PathLike[str]"] = None,
) -> Optional[Dict[str, Any]]:
    """Load the canonical season that applies to this planning window, if any.

    Returns ``{"season", "root", "schedule", "decisions", "baseline"}`` or
    ``None`` when no canonical season applies (the from-scratch path).  This
    is the single resolver every default planning/verification path uses, so
    a normal run reconstructs its baseline from the durable canonical files
    even when ``.pipeline`` was deleted.
    """
    config = config or {}
    season = resolve_canonical_season(config, start, end, root=root)
    if not season:
        return None
    resolved_root = root or config.get("canonical_season_root") or default_season_root()
    try:
        from tournament_scheduler.season_state import load_decisions, load_schedule

        schedule = load_schedule(season, root=resolved_root)
        decisions = load_decisions(season, root=resolved_root)
    except Exception:
        # A broken/unreadable canonical file must not crash an unrelated
        # planning run; the canonical CLI reports the underlying error when
        # it is asked to load the file directly.
        return None
    return {
        "season": season,
        "root": str(resolved_root),
        "schedule": schedule,
        "decisions": decisions,
        "baseline": build_canonical_baseline(schedule, decisions),
    }


def resolve_canonical_baseline(
    config: Optional[Dict[str, Any]],
    start: date,
    end: date,
    *,
    root: Optional["str | os.PathLike[str]"] = None,
) -> Optional[Dict[str, Any]]:
    """Return the canonical baseline overlay for this planning window, if any."""
    state = resolve_canonical_state(config, start, end, root=root)
    return state["baseline"] if state else None


def verify_canonical_locks(
    baseline: Optional[Dict[str, Any]],
    candidate: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return hard violations for a candidate that breaks canonical locks."""
    if not baseline:
        return []
    snapshots = {
        snapshot.get("id"): snapshot
        for snapshot in baseline.get("tournaments", []) or []
        if snapshot.get("id")
    }
    candidate_by_id = {
        str(t.get("id")): t
        for t in candidate.get("tournaments", []) or []
        if not t.get("cancelled") and t.get("id")
    }
    violations: List[Dict[str, Any]] = []
    for tournament_id, lock in (baseline.get("locks") or {}).items():
        snapshot = snapshots.get(tournament_id)
        current = candidate_by_id.get(tournament_id)
        if current is None:
            violations.append(
                {
                    "code": "canonical_locked_tournament_missing",
                    "message": (
                        f"Locked tournament {tournament_id} is missing from the candidate; "
                        "an approved/booked tournament cannot be dropped by replanning"
                    ),
                    "tournament_id": tournament_id,
                }
            )
            continue
        if snapshot is None:
            continue
        if lock.get("placement"):
            for field in PLACEMENT_FIELDS:
                if current.get(field) != snapshot.get(field):
                    violations.append(
                        {
                            "code": "canonical_placement_locked",
                            "message": (
                                f"Tournament {tournament_id} is placed-locked; "
                                f"{field} changed from {snapshot.get(field)!r} to {current.get(field)!r}"
                            ),
                            "tournament_id": tournament_id,
                            "field": field,
                            "expected": snapshot.get(field),
                            "actual": current.get(field),
                        }
                    )
        if lock.get("participants"):
            expected = {tuple(entry) for entry in snapshot.get("participants", [])}
            actual = {
                identity
                for team in current.get("teams", []) or []
                if (identity := _team_identity(team)) is not None
            }
            if expected != actual:
                violations.append(
                    {
                        "code": "canonical_participants_locked",
                        "message": (
                            f"Tournament {tournament_id} is participant-locked; "
                            "its participant list changed"
                        ),
                        "tournament_id": tournament_id,
                        "expected": sorted(list(entry) for entry in expected),
                        "actual": sorted(list(entry) for entry in actual),
                    }
                )
    return violations


def _resolve_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    resolved = dict(DEFAULT_CHANGE_WEIGHTS)
    for key, value in (weights or {}).items():
        if key in resolved:
            try:
                resolved[key] = float(value)
            except (TypeError, ValueError):
                continue
    return resolved


def change_cost(
    baseline: Optional[Dict[str, Any]],
    candidate: Dict[str, Any],
    *,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Measure how far *candidate* moves away from the canonical baseline.

    Each baseline tournament is classified into exactly one bucket:

    - ``none`` -- identical placement and participants;
    - ``participants`` -- placement unchanged, participant list changed;
    - ``placement`` -- date/arena/host/start time moved (a participant change
      that rides along with a placement move is still one placement change,
      not two);
    - ``replacement`` -- the tournament is gone, or the candidate introduces a
      tournament id the baseline never had.

    Returns a JSON-safe breakdown plus ``total`` (the weighted sum).  The
    weights are soft policy: an ordering hint for search/ranking, never a
    correctness gate.
    """
    resolved_weights = _resolve_weights(weights)
    baseline_snapshots = {
        snapshot.get("id"): snapshot
        for snapshot in (baseline or {}).get("tournaments", []) or []
        if snapshot.get("id")
    }
    candidate_tournaments = [
        t for t in candidate.get("tournaments", []) or [] if not t.get("cancelled")
    ]
    candidate_by_id = {str(t.get("id")): t for t in candidate_tournaments if t.get("id")}

    counts = {category: 0 for category in _CHANGE_CATEGORIES}
    details: List[Dict[str, Any]] = []

    for tournament_id, snapshot in baseline_snapshots.items():
        current = candidate_by_id.get(tournament_id)
        if current is None:
            counts["replacement"] += 1
            details.append(
                {"tournament_id": tournament_id, "category": "replacement", "reason": "removed"}
            )
            continue
        placement_changed = any(
            current.get(field) != snapshot.get(field) for field in PLACEMENT_FIELDS
        )
        participants_changed = {
            tuple(entry) for entry in snapshot.get("participants", [])
        } != {
            identity
            for team in current.get("teams", []) or []
            if (identity := _team_identity(team)) is not None
        }
        if placement_changed:
            category = "placement"
        elif participants_changed:
            category = "participants"
        else:
            category = "none"
        counts[category] += 1
        details.append({"tournament_id": tournament_id, "category": category})

    for tournament_id in candidate_by_id.keys() - baseline_snapshots.keys():
        counts["replacement"] += 1
        details.append(
            {"tournament_id": tournament_id, "category": "replacement", "reason": "new_id"}
        )

    total = sum(counts[category] * resolved_weights[category] for category in _CHANGE_CATEGORIES)
    details.sort(key=lambda entry: str(entry.get("tournament_id")))
    return {
        "total": total,
        "counts": counts,
        "weights": resolved_weights,
        "details": details,
        "baseline_tournament_count": len(baseline_snapshots),
        "candidate_tournament_count": len(candidate_tournaments),
    }


def pinned_tournament_ids(baseline: Optional[Dict[str, Any]]) -> List[str]:
    """Tournament ids that must survive replanning (any lock, placement or participants)."""
    return sorted((baseline or {}).get("locks", {}).keys())


def locked_dates(baseline: Optional[Dict[str, Any]]) -> List[str]:
    """Dates that must stay occupied because a placement-locked tournament sits there."""
    locks = (baseline or {}).get("locks", {})
    dates = {
        snapshot.get("date")
        for snapshot in (baseline or {}).get("tournaments", []) or []
        if snapshot.get("id") in locks
        and locks.get(snapshot["id"], {}).get("placement")
        and snapshot.get("date")
    }
    return sorted(str(value) for value in dates)
