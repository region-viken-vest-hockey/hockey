"""Plan critic: analyse a Stage 3 SeasonPlan for deterministic findings.

This module is intentionally free of LLM calls and external dependencies.
It reads the SeasonPlan data model and returns deterministic factual
findings (:func:`generate_critic_findings`) plus generic candidate repair
moves derived from them (:func:`suggest_moves`).

:func:`generate_critic_findings` returns every finding it detects, with no
cap and no severity ranking baked in — which finding matters most, and
whether/which repair to apply, is a contextual judgment for the LLM/agent
controller (or the operator), not something this module should decide for
them (issue #260 Phase 4). :func:`generate_critic_summary` remains as a
backward-compatible ranked-and-capped-at-5 string view for callers that
predate the structured decision path (e.g. ``rvv-miniputt critic``,
mid-planning-critic penalty-hint text) and were not part of this change.
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from tournament_scheduler.models import SeasonPlan

# Legacy display order for generate_critic_summary only — this is not a
# claim that "fail" always matters more than "clump" to the LLM/agent
# controller, just the order the pre-existing capped-at-5 text view showed
# issues in before this change, kept for that view's backward compatibility.
_LEGACY_CATEGORY_ORDER = ("fail", "collision", "outlier", "warn", "clump", "balance")


def generate_critic_findings(plan: "SeasonPlan") -> List[Dict[str, Any]]:
    """Analyse *plan* and return every deterministic finding, uncapped and unranked.

    Each finding is ``{"category": ..., "message": ...}`` where ``category``
    is one of ``"fail"``, ``"collision"``, ``"outlier"``, ``"warn"``,
    ``"clump"``, ``"balance"``. Findings are grouped by category (in the
    order below) but nothing here declares one category more severe than
    another or hides any finding from the caller — that prioritization
    belongs to the LLM/agent controller.

    Categories detected:
    - Fairness gate "fail" metrics
    - Arena-day collisions
    - Game count outliers (spread > 4)
    - Fairness gate "warn" metrics
    - Hosting clumps (>2 tournaments per club per month)
    - Low month balance (score < 0.6)

    Returns an empty list if no issues are detected.
    """
    fail_issues: List[str] = []
    collision_issues: List[str] = []
    outlier_issues: List[str] = []
    warn_issues: List[str] = []
    clump_issues: List[str] = []
    balance_issues: List[str] = []

    # ------------------------------------------------------------------
    # 1. Fairness gate (fail metrics)
    # ------------------------------------------------------------------
    fairness_gate = getattr(plan, "fairness_gate", {}) or {}
    gate_status = fairness_gate.get("status", "pass")
    if gate_status in ("warn", "fail"):
        for metric in fairness_gate.get("metrics", []):
            m_status = metric.get("status", "pass")
            label = metric.get("label", "unknown metric")
            value = metric.get("value")
            threshold = metric.get("threshold")
            detail = metric.get("detail", "")
            if m_status == "fail":
                msg = f"Fairness gate FAIL: {label} (value={value}, threshold={threshold})"
                if detail:
                    msg = f"Fairness gate FAIL: {label} — {detail}"
                fail_issues.append(msg[:120])
            elif m_status == "warn":
                msg = f"Fairness gate warning: {label} (value={value}, threshold={threshold})"
                if detail:
                    msg = f"Fairness gate warning: {label} — {detail}"
                warn_issues.append(msg[:120])

    # ------------------------------------------------------------------
    # 2. Arena-day collisions
    # ------------------------------------------------------------------
    collisions = getattr(plan, "arena_day_collisions", []) or []
    if collisions:
        collision_issues.append(
            f"{len(collisions)} arena-day collision(s) detected — "
            "review date assignments for arenas scheduled on the same day"
        )

    # ------------------------------------------------------------------
    # 3. Game count outliers — checked per age group so U7 is not compared
    #    against U12.
    # ------------------------------------------------------------------
    team_game_counts: dict = getattr(plan, "team_game_counts", {}) or {}
    spread_by_ag: dict = getattr(plan, "game_count_spread_by_age_group", {}) or {}

    # Build team-label -> age_group mapping from tournament entries.
    _team_label_to_ag: Dict[str, str] = {}
    for _t in getattr(plan, "tournaments", []) or []:
        _ag = getattr(_t, "age_group", None)
        if not _ag:
            continue
        for _tm in getattr(_t, "teams", []) or []:
            _lbl = getattr(_tm, "label", None) or getattr(_tm, "name", None)
            if _lbl and _lbl not in _team_label_to_ag:
                _team_label_to_ag[_lbl] = _ag

    # Group team_game_counts keys by age group.
    # Keys use the format "Label (Club, AgeGroup)" — parse the embedded age
    # group first so ambiguous labels like "Frisk Asker" don't bleed across
    # age groups via the tournament lookup.
    def _label_to_ag(key: str) -> str:
        # Primary: parse "(Club, AgeGroup)" suffix directly from the key.
        m = re.search(r"\(\s*[^,)]+,\s*([^)]+)\)\s*$", key)
        if m:
            return m.group(1).strip()
        # Fallback for plain labels without a parenthetical suffix.
        if key in _team_label_to_ag:
            return _team_label_to_ag[key]
        return ""

    if team_game_counts:
        ag_to_keys: Dict[str, List[str]] = defaultdict(list)
        for k in team_game_counts:
            ag = _label_to_ag(k)
            if ag:
                ag_to_keys[ag].append(k)

        if ag_to_keys:
            # Per-age-group comparison — U7 is never compared against U12.
            for ag, keys in sorted(ag_to_keys.items()):
                ag_counts = {k: team_game_counts[k] for k in keys}
                max_val = max(ag_counts.values())
                min_val = min(ag_counts.values())
                # Use stored spread if available; compute inline otherwise.
                ag_spread = spread_by_ag.get(ag, max_val - min_val) if spread_by_ag else (max_val - min_val)
                if ag_spread <= 4:
                    continue
                max_team = max(ag_counts, key=ag_counts.__getitem__)
                min_team = min(ag_counts, key=ag_counts.__getitem__)
                outlier_issues.append(
                    f"{ag} game count spread {ag_spread}: {max_team} plays {max_val} games "
                    f"vs {min_team}'s {min_val} — redistribute game assignments"
                )
        else:
            # No age-group mapping available at all — use global spread as last resort.
            game_count_spread: int = getattr(plan, "game_count_spread", 0) or 0
            if game_count_spread > 4:
                max_team = max(team_game_counts, key=team_game_counts.__getitem__)
                min_team = min(team_game_counts, key=team_game_counts.__getitem__)
                max_val = team_game_counts[max_team]
                min_val = team_game_counts[min_team]
                outlier_issues.append(
                    f"Game count spread {game_count_spread}: {max_team} plays {max_val} games "
                    f"vs {min_team}'s {min_val} — redistribute game assignments"
                )

    # ------------------------------------------------------------------
    # 5. Hosting clumps: >2 distinct hosting days at same club in same month
    #    for the same age group. A "hosting day" is a unique date — multiple
    #    tournaments of the same age group on the same day count as one duty.
    #    Tournaments of different age groups are counted independently so that
    #    a club hosting U7, U9, and U11 on three separate days is not flagged.
    # ------------------------------------------------------------------
    tournaments = getattr(plan, "tournaments", []) or []
    host_month_days: dict = defaultdict(set)
    for t in tournaments:
        if getattr(t, "cancelled", False):
            continue
        host_club = getattr(t, "host_club", None)
        t_date = getattr(t, "date", None)
        age_group = getattr(t, "age_group", None)
        if host_club and t_date and age_group:
            host_month_days[(host_club, t_date.year, t_date.month, age_group)].add(t_date)

    for (club, year, month, age_group), day_set in sorted(host_month_days.items()):
        count = len(day_set)
        if count > 2:
            month_name = calendar.month_name[month]
            clump_issues.append(
                f"{club} hosts {count} {age_group} tournaments in {month_name} {year} — "
                "consider moving one to another club"
            )

    # ------------------------------------------------------------------
    # 6. Low month balance
    # ------------------------------------------------------------------
    month_balance_score: float = getattr(plan, "month_balance_score", 1.0) or 1.0
    if month_balance_score < 0.6:
        balance_issues.append(
            f"Tournaments are unevenly spread across months "
            f"(balance score={month_balance_score:.2f}) — consider redistributing"
        )

    # ------------------------------------------------------------------
    # Every finding, tagged by category, uncapped and unranked — the
    # grouping below is just how they were computed above, not a claim
    # about relative severity (see generate_critic_summary for the
    # legacy ranked-and-capped view built from this).
    # ------------------------------------------------------------------
    findings: List[Dict[str, Any]] = []
    for category, messages in (
        ("fail", fail_issues),
        ("collision", collision_issues),
        ("outlier", outlier_issues),
        ("warn", warn_issues),
        ("clump", clump_issues),
        ("balance", balance_issues),
    ):
        findings.extend({"category": category, "message": message} for message in messages)
    return findings


def generate_critic_summary(plan: "SeasonPlan") -> List[str]:
    """Backward-compatible ranked-and-capped-at-5 issue strings.

    Built from :func:`generate_critic_findings`, ordered by
    :data:`_LEGACY_CATEGORY_ORDER` and capped at 5 — the exact shape this
    function had before issue #260 Phase 4, kept for callers that predate
    the structured decision path (``rvv-miniputt critic``, mid-planning
    critic penalty-hint text) and were not part of this change.
    """
    order = {category: index for index, category in enumerate(_LEGACY_CATEGORY_ORDER)}
    ranked = sorted(
        generate_critic_findings(plan),
        key=lambda finding: order.get(finding["category"], len(order)),
    )
    return [finding["message"] for finding in ranked[:5]]


def suggest_moves(plan: "SeasonPlan", issues: List[str]) -> List[Dict]:
    """Map each critic issue string to a concrete replan proposal.

    Returns a list of move dicts, each with keys:
      - ``tournament_id`` (str): ID of the tournament to move (or empty string if N/A).
      - ``new_date`` (str | None): ISO date string for the proposed new date, or None.
      - ``reason`` (str): Human-readable explanation of the proposed move.
      - ``can_auto_fix`` (bool): True if the harness can apply the fix without human input.
      - ``issue`` (str): The original issue string that triggered this move.

    Arena-day collisions and hosting clumps are considered auto-fixable (shift
    the involved tournament by +7 days).  Fairness-gate FAILs that require
    club-preference knowledge set ``can_auto_fix=False``.
    """
    moves: List[Dict] = []

    tournaments = getattr(plan, "tournaments", []) or []
    # Build a lookup from (arena, date_iso) → tournament id for collision resolution.
    # This dict is also mutated as moves are generated so that two moves in the same
    # call never target the same (arena, date) slot — preventing cascade conflicts.
    arena_date_to_tid: Dict[tuple, str] = {}
    for t in tournaments:
        if getattr(t, "cancelled", False):
            continue
        t_date = getattr(t, "date", None)
        t_arena = getattr(t, "arena", None)
        if t_date and t_arena:
            arena_date_to_tid[(t_arena, t_date.isoformat())] = t.id

    # Build a lookup from (host_club, year, month, age_group) → count (for target-month
    # selection).  Keyed per age group so candidate months are only considered when the
    # same age group has capacity there.
    _club_month_count: Dict[tuple, int] = defaultdict(int)
    for t in tournaments:
        if getattr(t, "cancelled", False):
            continue
        host_club = getattr(t, "host_club", None)
        t_date = getattr(t, "date", None)
        age_group = getattr(t, "age_group", None)
        if host_club and t_date and age_group:
            _club_month_count[(host_club, t_date.year, t_date.month, age_group)] += 1

    # Build a lookup from (host_club, year, month, age_group) → list of tournament ids
    host_month_to_tids: Dict[tuple, List[str]] = defaultdict(list)
    for t in tournaments:
        if getattr(t, "cancelled", False):
            continue
        host_club = getattr(t, "host_club", None)
        t_date = getattr(t, "date", None)
        age_group = getattr(t, "age_group", None)
        if host_club and t_date and age_group:
            host_month_to_tids[(host_club, t_date.year, t_date.month, age_group)].append(t.id)

    for issue in issues:
        # ------------------------------------------------------------------
        # Arena-day collisions — shift one of the colliding tournaments +7d
        # ------------------------------------------------------------------
        collision_match = re.match(r"(\d+) arena-day collision", issue)
        if collision_match:
            collisions = getattr(plan, "arena_day_collisions", []) or []
            if collisions:
                # Pick the first collision and suggest moving the second tournament
                col = collisions[0]
                col_arena = col.get("arena", "")
                col_date = col.get("date", "")
                # Try to find a tournament matching the conflicting entry
                conflicting_tid = arena_date_to_tid.get((col_arena, col_date), "")
                new_date = None
                if col_date:
                    try:
                        from datetime import date as _date
                        d = _date.fromisoformat(col_date)
                        new_date = (d + timedelta(weeks=1)).isoformat()
                    except (ValueError, TypeError):
                        pass
                moves.append(
                    {
                        "tournament_id": conflicting_tid,
                        "new_date": new_date,
                        "reason": (
                            f"Arena '{col_arena}' has two tournaments on {col_date}; "
                            "move one to the following weekend to resolve the collision."
                        ),
                        "can_auto_fix": True,
                        "issue": issue,
                    }
                )
            else:
                moves.append(
                    {
                        "tournament_id": "",
                        "new_date": None,
                        "reason": "Arena-day collision detected; review date assignments manually.",
                        "can_auto_fix": False,
                        "issue": issue,
                    }
                )
            continue

        # ------------------------------------------------------------------
        # Hosting clumps — move the entire latest hosting day out of the month
        # ------------------------------------------------------------------
        clump_match = re.match(r"(.+) hosts (\d+) (\S+) tournaments in (\w+) (\d{4})", issue)
        if clump_match:
            club = clump_match.group(1)
            clump_age_group = clump_match.group(3)
            month_name = clump_match.group(4)
            year = int(clump_match.group(5))
            # Convert month name to number
            month_num = list(calendar.month_name).index(month_name)
            tids = host_month_to_tids.get((club, year, month_num, clump_age_group), [])

            # Find the latest distinct hosting date in the clumped month
            latest_date = None
            for t in tournaments:
                if t.id in tids:
                    t_date = getattr(t, "date", None)
                    if t_date and (latest_date is None or t_date > latest_date):
                        latest_date = t_date

            # Collect all tournament IDs on that latest date (the whole hosting day)
            day_tids: List[str] = []
            if latest_date is not None:
                for t in tournaments:
                    if t.id in tids and getattr(t, "date", None) == latest_date:
                        day_tids.append(t.id)

            if not day_tids:
                day_tids = [tids[-1]] if tids else []

            # Determine the new date once for the whole hosting day (use first
            # tournament's arena as the representative slot to check availability).
            first_day_tid = day_tids[0] if day_tids else None
            target_arena = None
            for t in tournaments:
                if first_day_tid and t.id == first_day_tid:
                    target_arena = getattr(t, "arena", None)
                    break

            # Find a free weekend in a month where this club has ≤ 1 tournament
            # (so adding one stays under the >2 threshold).  Prefer the nearest.
            new_date = None
            if latest_date:
                from datetime import date as _date
                plan_end = getattr(plan, "end_date", None)
                # Collect months where club already has ≤ 1 tournament of the same age
                # group, sorted by proximity.  Only months with capacity for this age
                # group are valid targets so we don't push the clump into another month.
                candidate_months = sorted(
                    [
                        (abs((y2 * 12 + m2) - (year * 12 + month_num)), y2, m2)
                        for (c, y2, m2, ag), cnt in _club_month_count.items()
                        if c == club and ag == clump_age_group and cnt <= 1
                        and not (y2 == year and m2 == month_num)
                    ]
                )
                for _dist, ty, tm in candidate_months:
                    # Walk weekends within that month looking for a free arena slot
                    _, last_day = calendar.monthrange(ty, tm)
                    candidate = _date(ty, tm, 1)
                    month_end_date = _date(ty, tm, last_day)
                    while candidate <= month_end_date:
                        if candidate.weekday() in (5, 6):
                            key = (target_arena, candidate.isoformat())
                            if key not in arena_date_to_tid:
                                if plan_end is None or candidate <= plan_end:
                                    new_date = candidate.isoformat()
                                    break
                        candidate += timedelta(days=1)
                    if new_date:
                        break
                # Fall back to first free weekend after crowded month if no low-count month found
                if new_date is None:
                    _, last_day = calendar.monthrange(year, month_num)
                    month_end = _date(year, month_num, last_day)
                    candidate = month_end + timedelta(days=1)
                    for _ in range(60):
                        if candidate.weekday() in (5, 6):
                            key = (target_arena, candidate.isoformat())
                            if key not in arena_date_to_tid:
                                new_date = candidate.isoformat()
                                break
                        candidate += timedelta(days=1)
                if new_date is None:
                    new_date = (latest_date + timedelta(weeks=1)).isoformat()

            # Emit one move proposal per tournament in the hosting day.
            # Mark the chosen slot occupied so subsequent moves in this call
            # don't also target the same (arena, date).
            if new_date and target_arena:
                arena_date_to_tid[(target_arena, new_date)] = day_tids[0] if day_tids else ""
            for day_tid in day_tids:
                moves.append(
                    {
                        "tournament_id": day_tid,
                        "new_date": new_date,
                        "reason": (
                            f"{club} hosts too many tournaments in {month_name} {year}; "
                            "move one to a month where this club has capacity (≤1 tournament)."
                        ),
                        "can_auto_fix": True,
                        "issue": issue,
                    }
                )
            continue

        # ------------------------------------------------------------------
        # Fairness gate FAILs — flag for manual review
        # ------------------------------------------------------------------
        if issue.startswith("Fairness gate FAIL:"):
            moves.append(
                {
                    "tournament_id": "",
                    "new_date": None,
                    "reason": (
                        "Fairness gate failure requires club-preference knowledge to fix; "
                        "review the fairness metrics and adjust team assignments manually."
                    ),
                    "can_auto_fix": False,
                    "issue": issue,
                }
            )
            continue

        # ------------------------------------------------------------------
        # Fairness gate warnings — flag for manual review (lower priority)
        # ------------------------------------------------------------------
        if issue.startswith("Fairness gate warning:"):
            moves.append(
                {
                    "tournament_id": "",
                    "new_date": None,
                    "reason": (
                        "Fairness gate warning; monitor but no automatic fix is safe."
                    ),
                    "can_auto_fix": False,
                    "issue": issue,
                }
            )
            continue

        # ------------------------------------------------------------------
        # Game count spread outliers — flag for manual review
        # ------------------------------------------------------------------
        if issue.startswith("Game count spread"):
            moves.append(
                {
                    "tournament_id": "",
                    "new_date": None,
                    "reason": (
                        "Game count imbalance detected; redistribute team assignments "
                        "across tournaments to even out game counts."
                    ),
                    "can_auto_fix": False,
                    "issue": issue,
                }
            )
            continue

        # ------------------------------------------------------------------
        # Low month balance — flag for manual review
        # ------------------------------------------------------------------
        if "unevenly spread" in issue or "balance score" in issue:
            moves.append(
                {
                    "tournament_id": "",
                    "new_date": None,
                    "reason": (
                        "Tournaments are unevenly spread across months; "
                        "consider redistributing to improve month balance."
                    ),
                    "can_auto_fix": False,
                    "issue": issue,
                }
            )
            continue

        # ------------------------------------------------------------------
        # Unrecognised issue — pass through as non-auto-fixable
        # ------------------------------------------------------------------
        moves.append(
            {
                "tournament_id": "",
                "new_date": None,
                "reason": f"Unrecognised issue — manual review required: {issue}",
                "can_auto_fix": False,
                "issue": issue,
            }
        )

    return moves


def count_critic_issues_from_dict(plan_dict: dict) -> int:
    """Count critic issues from a serialised Stage 3 plan dict (no SeasonPlan needed).

    This is the lightweight variant used by ``rvv-miniputt status`` to show a
    one-line summary without reconstructing the full SeasonPlan object.  The
    logic mirrors ``generate_critic_summary`` but operates on the JSON-safe dict
    produced by ``_plan_to_dict``.
    """
    count = 0

    # Fairness gate failures/warnings
    fairness_gate = plan_dict.get("fairness_gate") or {}
    if fairness_gate.get("status") in ("warn", "fail"):
        for metric in fairness_gate.get("metrics", []):
            if metric.get("status") in ("fail", "warn"):
                count += 1

    # Arena-day collisions
    if plan_dict.get("arena_day_collisions"):
        count += 1

    # Game count outlier
    spread = plan_dict.get("game_count_spread") or 0
    if spread > 4:
        count += 1

    # Hosting clumps — scoped per age group so that hosting one tournament per
    # age group per month does not trigger a false-positive clump warning.
    host_month_days: dict = defaultdict(set)
    for t in plan_dict.get("tournaments") or []:
        if t.get("cancelled"):
            continue
        host_club = t.get("host_club")
        date_str = t.get("date")
        age_group = t.get("age_group")
        if host_club and date_str and age_group:
            try:
                from datetime import date as _date
                d = _date.fromisoformat(date_str)
                host_month_days[(host_club, d.year, d.month, age_group)].add(d)
            except (ValueError, TypeError):
                pass
    for day_set in host_month_days.values():
        if len(day_set) > 2:
            count += 1

    # Low month balance
    balance = plan_dict.get("month_balance_score") or 1.0
    if balance < 0.6:
        count += 1

    return min(count, 5)


def count_issues_from_plan(plan_raw: Any) -> int:
    """Count critic issues from a raw plan value (SeasonPlan object or dict).

    Converts *plan_raw* to a JSON-serialisable dict via
    :func:`~tournament_scheduler.pipeline.stage3_helpers._resolve_plan_dict`
    and then delegates to :func:`count_critic_issues_from_dict`.

    This is the single callable for all CLI commands that need an issue count
    without caring whether they hold a :class:`SeasonPlan` object or a plain
    dict.
    """
    from ..pipeline.stage3_helpers import _resolve_plan_dict

    plan_dict = _resolve_plan_dict(plan_raw)
    if not plan_dict:
        return 0
    return count_critic_issues_from_dict(plan_dict)


if __name__ == "__main__":
    pass
