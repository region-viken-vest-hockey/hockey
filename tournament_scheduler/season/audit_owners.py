"""Catalog-owner evidence for the season-wide completion gate.

The season audit resolves every catalogued check against verifier codes and
actionable findings. Owners that live outside the ordinary verifier (request
constraints, hosting coverage/responsibility, accepted-change protections) are
run here, and their factual output is translated into that vocabulary. Each
owner path is read from the catalog so this audit cannot drift from the rule
registry it reports against.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from tournament_scheduler.rules.catalog import CATALOG_BY_ID
from tournament_scheduler.season.findings import HOSTING


# Owner paths are the catalog's verifier owners, so this audit cannot drift from
# the rule registry it reports against.
REQUEST_CONSTRAINT_OWNER = CATALOG_BY_ID["request_team_unavailable"].verifier_owner
HOSTING_COVERAGE_OWNER = CATALOG_BY_ID["hosting_age_group_coverage"].verifier_owner
HOSTING_RESPONSIBILITY_OWNER = CATALOG_BY_ID["hosting_responsibility"].verifier_owner
CHANGE_PROTECTION_OWNER = CATALOG_BY_ID["accepted_change_protection"].verifier_owner

_REQUEST_CONSTRAINT_RULES = (
    "request_team_unavailable",
    "request_minimum_gap",
    "request_opponent_avoidance",
)


def _audit_owner_evidence(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Tuple[str, ...], Dict[str, str]]:
    """Run the catalog rule owners the ordinary verifier does not cover.

    The season audit resolves every catalogued check against verifier codes and
    actionable findings. Request constraints, club x age-group hosting coverage
    and hosting responsibility are owned outside the ordinary verifier, so they
    are run here and their factual output is translated into that vocabulary.
    An owner is reported covered only after it ran on complete inputs; a
    missing input or a failed owner leaves its catalog rule ``incomplete`` with
    an actionable reason instead of a false green.
    """
    from ..hosting.coverage import hosting_coverage_matrix
    from ..hosting.responsibility import (
        RESPONSIBILITY_TRANSFER_CODE,
        hosting_responsibility_facts,
    )
    from tournament_scheduler.participation.withdrawals import eligible_hosting_teams
    from .request_constraints import request_constraint_violations

    covered: List[str] = []
    incomplete_reasons: Dict[str, str] = {}
    violations: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []

    try:
        violations.extend(
            dict(violation) for violation in request_constraint_violations(plan, decisions)
        )
        covered.append(REQUEST_CONSTRAINT_OWNER)
    except Exception as exc:  # defensive fail-closed boundary
        for rule_id in _REQUEST_CONSTRAINT_RULES:
            incomplete_reasons[rule_id] = f"request-constraint owner failed: {exc}"

    from .change_protections import protection_violations

    try:
        violations.extend(dict(violation) for violation in protection_violations(plan, decisions))
        covered.append(CHANGE_PROTECTION_OWNER)
    except Exception as exc:  # defensive fail-closed boundary
        incomplete_reasons["accepted_change_protection"] = f"change-protection owner failed: {exc}"

    teams = eligible_hosting_teams(problem)
    if not teams:
        # Coverage/responsibility are derived from the registered roster; with
        # no roster evidence an empty result is not a clean season.
        incomplete_reasons["hosting_age_group_coverage"] = (
            "problem carries no registered-team evidence; hosting coverage cannot be evaluated"
        )
        incomplete_reasons["hosting_responsibility"] = (
            "problem carries no registered-team evidence; hosting responsibility cannot be evaluated"
        )
    else:
        try:
            for row in hosting_coverage_matrix(teams, plan.get("tournaments") or []):
                if not row.get("unresolved"):
                    continue
                age_group = str(row.get("age_group") or "")
                club = str(row.get("club") or "")
                findings.append(
                    {
                        # Distinct from the balance finding's ``hosting_balance:`` id
                        # so a coverage obligation is never silently discarded by a
                        # same-scope balance finding with a different code.
                        "finding_id": f"hosting_coverage:{age_group}:{club}",
                        "code": "unresolved_hosting_obligation",
                        "category": HOSTING,
                        "severity": "strong_goal",
                        "age_group": age_group,
                        "club": club,
                        "teams": int(row.get("teams", 0)),
                        "hosted": int(row.get("hosted", 0)),
                        "coverage_unresolved": True,
                        "message": (
                            f"{club} hosts no {age_group} tournament although it fields "
                            f"{int(row.get('teams', 0))} team(s)"
                        ),
                    }
                )
            covered.append(HOSTING_COVERAGE_OWNER)
        except Exception as exc:  # defensive fail-closed boundary
            incomplete_reasons["hosting_age_group_coverage"] = (
                f"hosting coverage owner failed: {exc}"
            )
        try:
            for row in hosting_responsibility_facts(problem, plan):
                excess = int(row.get("excess", 0))
                if excess <= 0:
                    continue
                age_group = str(row.get("age_group") or "")
                club = str(row.get("club") or "")
                findings.append(
                    {
                        "finding_id": f"hosting_responsibility:{age_group}:{club}",
                        "code": RESPONSIBILITY_TRANSFER_CODE,
                        "category": HOSTING,
                        "severity": "strong_goal",
                        "age_group": age_group,
                        "club": club,
                        "target": int(row.get("target", 0)),
                        "actual": int(row.get("actual", 0)),
                        "excess": excess,
                        "message": (
                            f"{club} hosts {int(row.get('actual', 0))} {age_group} "
                            f"tournament(s) against a target of {int(row.get('target', 0))}; "
                            "hosting responsibility was transferred to a club that does not owe it"
                        ),
                    }
                )
            covered.append(HOSTING_RESPONSIBILITY_OWNER)
        except Exception as exc:  # defensive fail-closed boundary
            incomplete_reasons["hosting_responsibility"] = (
                f"hosting responsibility owner failed: {exc}"
            )

    return violations, findings, tuple(covered), incomplete_reasons
