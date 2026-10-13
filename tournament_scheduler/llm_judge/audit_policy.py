"""Hard and soft policy paragraphs for the semantic safety-net audit prompt.

Kept separate from :mod:`.audit` so the prompt builder stays a thin assembler;
each policy names the bounded evidence field that triggers it.
"""

from __future__ import annotations

from typing import Any


def audit_policy_lines(context: dict[str, Any]) -> list[str]:
    """Return the policy paragraphs that precede the audit mission."""
    return [
        "Do not assume the schedule is correct merely because deterministic verification "
        "passed. The scheduler and its deterministic verifier may share a logic defect, or "
        "may simply be missing a rule. Explain anything that does not make operational sense.",
        "",
        "Hard policy: pause/bye teams are invalid in every age group. If "
        "`plan_audit_summary.tournament_utilisation_summary.tournaments_with_byes_or_invalid_no_bye_roster` "
        "is greater than zero, or you independently find any odd-sized/byed tournament, the overall "
        "status must be `FAIL` -- never `REVIEW_REQUIRED` or `PASS`. Repeated even-roster but "
        "low-utilisation tournaments should still be reported as planning-quality findings.",
        "",
        "Hard policy: a tournament with more than "
        f"{context.get('hard_max_club_teams_per_tournament', 3)} teams from the same club is "
        "an invalid plan, not a quality concern. If "
        "`plan_audit_summary.same_club_per_tournament_summary.tournaments_over_hard_max` is "
        "greater than zero, or you independently find such a tournament, the overall status "
        "must be `FAIL` -- never `REVIEW_REQUIRED` or `PASS` -- regardless of any other score "
        "or `production_ready` label.",
        "",
        "Evidence-coverage policy (issue #679): if `evidence_coverage.calendar` "
        "is `not_scanned` and `evidence_coverage.unverified_placements` is "
        "greater than zero, the external-conflict placements were NOT checked against calendar "
        "evidence. The overall status must not be `PASS`, and checklist item 4 must report the "
        "gap explicitly as unverified -- never as a clear calendar check. Do not infer a negative "
        "calendar finding from the absence of scanned sources.",
        "",
        "Soft policy (issue #327): a team's participation count coming in under its nominal "
        "target is not automatically a defect. Check each shortfall's `category` in "
        "`plan_audit_summary.unresolved_participation_shortfalls` against "
        "`plan_audit_summary.club_participation_fairness` -- a "
        "`participation_under_target_club_share_ok` shortfall means the team's club already "
        "received its fair proportional share of participation slots with sibling teams "
        "rotated evenly (spread <=1), and is not by itself grounds for `REVIEW_REQUIRED`. Still "
        "scrutinize a club materially below its `target_share` without such evidence, or any "
        "shortfall where `sibling_spread` is greater than 1 -- those indicate a genuine "
        "fairness gap worth flagging.",
        "",
    ]
