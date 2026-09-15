"""Internal arena/time double-booking resolution decision.

Two of the *planner's own* tournaments can end up sharing overlapping ice
time in the same arena when Stage 3's search can't fully route around a
tight arena schedule within its budget. Unlike an external calendar
conflict (only one side is ever under the planner's control, so the
downgrade-to-manual-placement choice is automatic), a same-arena collision
between two of our own tournaments has two candidates either of which could
legitimately keep the automatic slot -- deciding which one keeps it and
which one is demoted to manual placement is a contextual judgement call,
not a fixed Python heuristic (mirrors ``shared_host_decision.py``).

This module builds the narrow
:class:`~.application.decisions.DecisionContext` for one collision pair.
Python's job stops at: computing the facts, constraining the choice to the
collision's own two tournaments (via the ``keep_tournament_id`` argument's
``enum``), and recording the resulting decision with provenance. Nothing
here picks a winner itself.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from .application.decisions import DecisionAction, DecisionContext

ARENA_CONFLICT_DECISION_ACTIONS: "tuple[str, ...]" = ("resolve_arena_conflict", "request_operator")


def side_label(side: Mapping[str, Any]) -> str:
    """Return the stable per-side identity used both in :func:`collision_key`
    and to re-map a resolved decision onto a freshly rebuilt candidate's
    (new, random) tournament ids -- ``age_group:host_club``."""
    return f"{side.get('age_group', '')}:{side.get('host_club', '')}"


def collision_key(facts: Mapping[str, Any]) -> str:
    """Return a stable identity for one collision pair.

    Tournament ids are fresh random UUIDs every time Stage 3 rebuilds a
    candidate (a new interactive invocation re-plans from scratch), so a
    decision keyed on ids from one attempt could never be recognized as
    "already resolved" against a later attempt's regenerated tournaments.
    ``(arena, date, sorted[age_group:host_club side])`` is stable across
    rebuilds the same way ``(registration, age_group)`` is for shared-host
    decisions.
    """
    sides = sorted(side_label(side) for side in facts.get("sides", []))
    return f"{facts.get('arena', '')}|{facts.get('date', '')}|{'|'.join(sides)}"


def build_arena_conflict_decision_context(run_id: str, facts: Mapping[str, Any]) -> DecisionContext:
    """Build the :class:`DecisionContext` for one arena/time collision pair.

    *facts* has ``{"arena", "date", "overlap", "sides": [{"tournament_id",
    "age_group", "host_club", "interval", "team_count"}, ...]}`` -- exactly
    two entries in ``sides``.
    """
    sides = list(facts.get("sides") or [])
    tournament_ids = [str(side.get("tournament_id", "")) for side in sides]
    return DecisionContext(
        run_id=run_id,
        capability="arena_conflict_resolution",
        stage="stage3_planning",
        objective=(
            f"Two of this plan's own tournaments overlap in the same arena "
            f"({facts.get('arena', '?')} on {facts.get('date', '?')}) -- Stage 3's search "
            "could not route around it within its budget. Decide which tournament keeps "
            "the automatic arena slot; the other is demoted to a manual placement (its "
            "start time is cleared and it is flagged for the operator to book a different "
            "slot by hand). Weigh which side has fewer realistic alternative slots left "
            "(e.g. hosting-fairness deficit, fewer remaining free dates) rather than picking "
            "arbitrarily."
        ),
        facts=dict(facts),
        available_actions=ARENA_CONFLICT_DECISION_ACTIONS,
        action_parameters={
            "resolve_arena_conflict": {
                "keep_tournament_id": {
                    "type": "string",
                    "enum": tournament_ids,
                    "description": (
                        "The tournament id (of this collision's own two) that keeps the "
                        "automatic arena/time slot. The other side is demoted to manual "
                        "placement."
                    ),
                },
            },
        },
    )


def arena_conflict_decision_record(
    facts: Mapping[str, Any],
    keep_tournament_id: str,
    manual_tournament_id: str,
    rationale: str,
    *,
    decided_by: str = "harness",
    decided_at: str = "",
) -> Dict[str, Any]:
    """Build the provenance dict persisted for one resolved collision.

    ``keep_tournament_id``/``manual_tournament_id`` are recorded verbatim
    for audit purposes only -- they're ids from *this* attempt's candidate
    and will not exist in a later Stage 3 rebuild. Re-applying the decision
    to a rebuilt candidate (``arena_conflict_decisions._resolve_arena_conflict_decisions``)
    matches on ``keep_side``/``manual_side`` (``age_group:host_club``)
    instead, which is stable across rebuilds.
    """
    sides = {str(s.get("tournament_id", "")): side_label(s) for s in facts.get("sides", [])}
    return {
        "key": collision_key(facts),
        "arena": facts.get("arena", ""),
        "date": facts.get("date", ""),
        "keep_tournament_id": keep_tournament_id,
        "manual_tournament_id": manual_tournament_id,
        "keep_side": sides.get(keep_tournament_id, ""),
        "manual_side": sides.get(manual_tournament_id, ""),
        "rationale": rationale,
        "decided_by": decided_by,
        "decided_at": decided_at,
    }


def build_arena_conflict_decision_prompt(context: DecisionContext) -> str:
    """Build the headless-judge prompt for one arena-conflict decision.

    Mirrors ``shared_host_decision.build_shared_host_decision_prompt``: the
    only useful action (``resolve_arena_conflict``) is meaningless without
    its ``keep_tournament_id`` argument, so the prompt asks for it
    explicitly on its own line.
    """
    tournament_ids = [str(s.get("tournament_id", "")) for s in context.facts.get("sides", [])]
    lines = [f"OBJECTIVE: {context.objective}", ""]
    if context.facts:
        lines.append("Facts:")
        lines.extend(f"  - {key}: {value}" for key, value in context.facts.items())
        lines.append("")
    lines += [
        "Respond in exactly this format:",
        "Line 1: one action id, one of " + ", ".join(context.available_actions),
        "Line 2: only if line 1 is resolve_arena_conflict, the tournament id to KEEP, "
        "exactly as written, one of: " + ", ".join(tournament_ids),
        "Remaining lines: a brief rationale (never chain-of-thought).",
    ]
    return "\n".join(lines)


def parse_arena_conflict_verdict(context: DecisionContext, raw_verdict: str) -> DecisionAction:
    """Parse a headless judge reply into a :class:`DecisionAction`.

    Mirrors ``shared_host_decision.parse_shared_host_verdict``: a verdict
    that doesn't name one of the pair's own tournament ids degrades to
    ``request_operator`` rather than guessing.
    """
    lines = [ln.strip() for ln in raw_verdict.strip().splitlines() if ln.strip()]
    if not lines:
        return DecisionAction(
            action_id="request_operator",
            arguments={"question": "empty judge verdict"},
            rationale="empty judge verdict",
        )

    first = lines[0].lower().strip(" .:-")
    action_id = None
    for candidate in context.available_actions:
        if first == candidate.lower():
            action_id = candidate
            break
    if action_id is None:
        return DecisionAction(
            action_id="request_operator",
            arguments={"question": f"Could not parse judge verdict: {lines[0]!r}"},
            rationale=f"Unparsed judge verdict: {raw_verdict.strip()[:200]}",
        )

    if action_id != "resolve_arena_conflict":
        return DecisionAction(action_id=action_id, rationale="\n".join(lines[1:]).strip())

    tournament_ids = [str(s.get("tournament_id", "")) for s in context.facts.get("sides", [])]
    chosen = None
    for line in lines[1:]:
        stripped = line.strip(" .:-")
        for tid in tournament_ids:
            if stripped == tid:
                chosen = tid
                break
        if chosen:
            break
    if chosen is None:
        for tid in tournament_ids:
            if tid and tid in raw_verdict:
                chosen = tid
                break

    rationale = "\n".join(lines[1:]).strip()
    if chosen is None:
        return DecisionAction(
            action_id="request_operator",
            arguments={"question": "resolve_arena_conflict verdict did not name a tournament id"},
            rationale=rationale or raw_verdict.strip()[:200],
        )
    return DecisionAction(
        action_id="resolve_arena_conflict", arguments={"keep_tournament_id": chosen}, rationale=rationale
    )
