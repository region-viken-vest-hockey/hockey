"""Stage-specific prompt builders for inter-stage headless pipeline judgment.

Each builder produces a concise, structured prompt that describes what the
stage produced and asks whether the pipeline should continue. The
evaluation criteria are **not** defined here: per issue #260 / ADR 0002,
``.agents/skills/rvv/SKILL.md`` ("Stage gating policy" section) is the
single canonical soft-policy source for both interactive harnesses and
this headless judge path. This module only assembles the deterministic
facts (a :class:`~tournament_scheduler.application.decisions.DecisionContext`)
and quotes the matching policy section — it must not carry its own
independent thresholds (e.g. "most sources" / "fewer than half") that
could drift from the canonical policy.

Usage::

    from tournament_scheduler.llm_judge.prompts import build_stage_prompt

    prompt = build_stage_prompt("config", checkpoint_summary)
    verdict = judge.judge(prompt)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from ..application.decisions import DecisionContext

# ---------------------------------------------------------------------------
# Canonical shared policy (.agents/skills/rvv/SKILL.md)
# ---------------------------------------------------------------------------

_SKILL_MD_PATH = Path(__file__).resolve().parents[2] / ".agents" / "skills" / "rvv" / "SKILL.md"
_POLICY_SECTION_HEADING = "## Stage gating policy (soft judgment)"

_STAGE_POLICY_HEADINGS: dict[str, str] = {
    "config": "### Stage 1 — Configuration",
    "stage1": "### Stage 1 — Configuration",
    "scraping": "### Stage 2 — Scraping",
    "stage2": "### Stage 2 — Scraping",
    "planning": "### Stage 3 — Planning",
    "stage3": "### Stage 3 — Planning",
}

# Fallback text used only if SKILL.md is unreadable or has drifted away
# from the expected headings — keeps the judge functional, but should not
# happen in a normal checkout.
_FALLBACK_POLICY = (
    "Canonical policy at .agents/skills/rvv/SKILL.md was unavailable. "
    "Use best judgment: proceed only if the stage facts show no hard "
    "problems; abort if the stage produced clearly insufficient or "
    "invalid data."
)


@lru_cache(maxsize=1)
def _read_skill_md() -> str:
    return _SKILL_MD_PATH.read_text(encoding="utf-8")


def _extract_section(markdown: str, heading: str) -> str | None:
    """Return the body text under *heading* up to the next heading of equal-or-higher level."""
    lines = markdown.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return None
    body: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            stripped_level = len(stripped) - len(stripped.lstrip("#"))
            if stripped_level <= level:
                break
        body.append(line)
    return "\n".join(body).strip()


def load_stage_gating_policy(stage_key: str) -> str:
    """Return the canonical soft-policy text for *stage_key* from SKILL.md.

    Falls back to a conservative generic instruction if the shared policy
    document or the expected section heading cannot be found, so a
    documentation edit that breaks the heading text degrades gracefully
    instead of crashing the pipeline.
    """
    heading = _STAGE_POLICY_HEADINGS.get(stage_key.lower())
    if heading is None:
        return _FALLBACK_POLICY
    try:
        markdown = _read_skill_md()
    except OSError:
        return _FALLBACK_POLICY
    section = _extract_section(markdown, heading)
    return section or _FALLBACK_POLICY


# ---------------------------------------------------------------------------
# DecisionContext construction from a stage checkpoint summary
# ---------------------------------------------------------------------------


def _config_facts(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "sources": summary.get("sources", summary.get("source_count", "?")),
        "start_date": summary.get("start_date", "?"),
        "end_date": summary.get("end_date", "?"),
        "age_groups": summary.get("age_groups", []),
        "clubs": summary.get("clubs", []),
    }


def _scraping_facts(summary: dict[str, Any]) -> dict[str, Any]:
    blocked = summary.get("blocked", [])
    llm_fallback = summary.get("llm_fallback", [])
    return {
        "sources_scanned": summary.get("sources_scanned", summary.get("sources", "?")),
        "blocked_count": len(blocked) if isinstance(blocked, list) else blocked,
        "blocked_sources": blocked if isinstance(blocked, list) else [],
        "llm_fallback_count": len(llm_fallback) if isinstance(llm_fallback, list) else llm_fallback,
    }


def _planning_facts(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "tournaments_planned": summary.get("tournaments_planned", summary.get("n_tournaments", "?")),
        "clubs_covered": summary.get("clubs_covered", []),
        "age_groups_covered": summary.get("age_groups_covered", []),
        "warnings": summary.get("warnings", []),
        "tone": summary.get("tone"),
    }


def _export_facts(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "files_written": summary.get("files_written", []),
        "errors": summary.get("errors", []),
    }


_FACT_BUILDERS = {
    "config": _config_facts,
    "stage1": _config_facts,
    "scraping": _scraping_facts,
    "stage2": _scraping_facts,
    "planning": _planning_facts,
    "stage3": _planning_facts,
    "export": _export_facts,
    "stage4": _export_facts,
}

_STAGE_LABELS = {
    "config": "Configuration (Stage 1)",
    "stage1": "Configuration (Stage 1)",
    "scraping": "Calendar Scraping (Stage 2)",
    "stage2": "Calendar Scraping (Stage 2)",
    "planning": "Season Planning (Stage 3)",
    "stage3": "Season Planning (Stage 3)",
    "export": "Export (Stage 4)",
    "stage4": "Export (Stage 4)",
}


def build_decision_context(stage_name: str, checkpoint_summary: dict[str, Any]) -> DecisionContext:
    """Build a :class:`DecisionContext` for a stage-gate decision.

    ``available_actions`` always offers ``proceed``/``abort``/``retry_stage``/
    ``request_operator`` — the generic actions any stage-gate decision can
    reasonably need — plus ``recover_source`` specifically for a scraping
    stage with blocked sources, since that's the only stage where a "source"
    argument is meaningful (issue #260 Phase 5's interactive stage mode).

    Raises:
        ValueError: If *stage_name* is not recognised.
    """
    key = stage_name.lower()
    if key not in _FACT_BUILDERS:
        raise ValueError(
            f"Unknown stage name {stage_name!r}. Valid values: {', '.join(sorted(set(_FACT_BUILDERS)))}"
        )
    facts = _FACT_BUILDERS[key](checkpoint_summary)
    available_actions = ["proceed", "abort", "retry_stage", "request_operator"]
    if key in ("scraping", "stage2") and facts.get("blocked_count"):
        available_actions.append("recover_source")
    return DecisionContext(
        run_id="",
        capability=key,
        stage=key,
        objective="decide whether the pipeline should continue past this stage",
        facts=facts,
        available_actions=tuple(available_actions),
    )


def build_decision_prompt(context: DecisionContext) -> str:
    """Build a judgment prompt from a :class:`DecisionContext` plus canonical policy."""
    label = _STAGE_LABELS.get(context.stage, context.stage or context.capability)
    policy = load_stage_gating_policy(context.stage or context.capability)

    lines = [f"PIPELINE STAGE: {label}", "", "Facts:"]
    for key, value in context.facts.items():
        lines.append(f"  - {key}: {value}")

    lines += [
        "",
        "Policy (from .agents/skills/rvv/SKILL.md — canonical, do not override):",
        policy,
        "",
        "Respond with exactly one of:",
        "  PROCEED — continue to the next stage",
        "  ABORT   — do not continue; briefly explain after the keyword",
    ]
    return "\n".join(lines)


def build_stage_prompt(stage_name: str, checkpoint_summary: dict[str, Any]) -> str:
    """Build a structured judgment prompt for the given pipeline stage.

    Args:
        stage_name: One of ``"config"`` / ``"stage1"``, ``"scraping"`` /
                    ``"stage2"``, or ``"planning"`` / ``"stage3"``.
        checkpoint_summary: A dict of key metrics extracted from the stage
                            checkpoint. Unknown keys are ignored gracefully.

    Returns:
        A prompt string ready to pass to :meth:`LLMJudge.judge`.

    Raises:
        ValueError: If *stage_name* is not recognised.
    """
    context = build_decision_context(stage_name, checkpoint_summary)
    return build_decision_prompt(context)
