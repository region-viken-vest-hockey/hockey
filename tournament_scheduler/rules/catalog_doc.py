"""Generated agent-facing rendering of the scheduling rule catalog.

The catalog in ``tournament_scheduler.rules.catalog`` is the only source of
truth; this module only renders it to ``docs/architecture/rule-catalog.md``.
Run ``scripts/render-rule-catalog.py`` to regenerate, or ``--check`` to fail
when the committed document is stale.
"""

from __future__ import annotations

from pathlib import Path

from tournament_scheduler.rules.catalog import (
    CATALOG_DOC_PATH,
    CLASSIFICATION_LABELS,
    FACT_EVIDENCE_SEMANTIC,
    HARD_CONSTRAINT,
    OPERATIONAL_OBLIGATION,
    OPERATOR_DECISION,
    RULE_CATALOG,
    SOFT_OBJECTIVE,
    TIER_LABELS,
    RuleEntry,
    entries_by_classification,
    precedence_pairs,
    priority_tier_for_rule,
)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _code_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "—"


def _owner_module(dotted: str) -> str:
    return f"`{dotted}`"


def _summary_table(entries: tuple[RuleEntry, ...]) -> list[str]:
    lines = [
        "| Rule ID | Tier | Meaning | Canonical owner | Verifier / measurement | Tests |",
        "|---|---|---|---|---|---|",
    ]
    for entry in entries:
        lines.append(
            "| `{id}` | {tier} | {meaning} | {owner} | {verifier} | {tests} |".format(
                id=entry.id,
                tier=TIER_LABELS[priority_tier_for_rule(entry.id)],
                meaning=_md_escape(entry.meaning),
                owner=_owner_module(entry.canonical_owner),
                verifier=_code_list((entry.verifier_owner,)) if entry.verifier_owner else "—",
                tests=_code_list(entry.tests),
            )
        )
    return lines


def _entry_detail(entry: RuleEntry) -> list[str]:
    classification = CLASSIFICATION_LABELS.get(entry.classification, entry.classification)
    lines = [
        f"### `{entry.id}`",
        "",
        f"`{classification}` · status `{entry.status}` · "
        f"operator-waivable: {'yes' if entry.waivable else 'no (structural)'}  ",
        f"**Priority tier:** {TIER_LABELS[priority_tier_for_rule(entry.id)]}  ",
        f"**Meaning:** {entry.meaning}  ",
        f"**Canonical owner:** `{entry.canonical_owner}`  ",
        f"**Input / fact source:** {entry.input_source or '—'}  ",
        f"**Verifier / measurement:** {('`' + entry.verifier_owner + '`') if entry.verifier_owner else '—'}  ",
        "**Codes:** "
        f"verifier {_code_list(entry.verifier_codes)} · "
        f"finding {_code_list(entry.finding_codes)} · "
        f"score {_code_list(entry.score_paths)}  ",
        "**Providers:** "
        f"mutation {_code_list(entry.mutation_providers)} · "
        f"search {_code_list(entry.search_providers)}  ",
        "**Evidence / report:** " + _code_list(entry.evidence_projection) + "  ",
        "**Tests:** " + _code_list(entry.tests) + "  ",
        "**Precedence:** "
        f"precedes {_code_list(entry.precedes)} · "
        f"depends on {_code_list(entry.depends_on)}",
        "",
    ]
    return lines


def render_markdown() -> str:
    """Render the canonical agent-facing ownership table."""
    lines: list[str] = [
        "# Scheduling rule catalog",
        "",
        "> **Generated** from `tournament_scheduler/rules/catalog.py` by",
        "> `python3 scripts/render-rule-catalog.py`. Do not edit by hand.",
        "",
        "This is the one compact, searchable answer to *what scheduling semantics exist, who",
        "owns each one, and which semantics take precedence when they interact*. It is metadata",
        "and navigation only: the deterministic implementations named by each entry remain the",
        "executable truth.",
        "",
        "Search this page for a rule ID (for example `host_representation`,",
        "`hosting_age_group_coverage`, `hosting_proportional_balance` or",
        "`arena_interval_non_overlap`) to find the owning facade, its verifier and its tests",
        "before editing code.",
        "",
        "## Stable identity in deterministic outputs",
        "",
        "The catalog is the one source of rule/objective identity, and the semantic/application",
        "boundaries reuse it instead of re-deriving prose:",
        "",
        "* verifier violations carry `rule_id` (attached by `annotate_violations`);",
        "* `season findings` carry `rule_id` per finding plus a `counts_by_rule_id` summary",
        "  (attached by `annotate_findings`);",
        "* quality/score metrics in `compare_quality_scores` carry `objective_id` for every",
        "  registered score path;",
        "* the Regler/rules model carries `catalog_id` per per-run row.",
        "",
        "`rule_id_for_verifier_code`, `rule_id_for_finding_code` and `rule_id_for_score_path`",
        "own the lookups; callers must not hard-code a second mapping.",
        "",
        "## How to use this catalog",
        "",
        "Before introducing a new rule because of a production failure, determine which case it is:",
        "",
        "1. **Does a catalog entry already describe the intended semantic?** Fix the owning",
        "   implementation/conformance; do not add another rule.",
        "2. **Is the semantic correct but no legal repair is exposed?** Add an action/search",
        "   capability under the existing `local_repair_options` boundary, not another hard rule.",
        "3. **Is the candidate correct but state/resume is wrong?** Route to the stage-3",
        "   session/lifecycle owner (not a hockey rule).",
        "4. **Is the candidate correct but evidence/reporting is wrong?** Fix the evidence/report",
        "   projection.",
        "5. **Is this genuinely new operator policy?** Add/catalog it deliberately with",
        "   classification, source and generic tests.",
        "",
        "Production clubs/dates remain regression fixtures, never rule identities.",
        "",
        "For hosting anomalies specifically, check in this order:",
        "",
        "```text",
        "Is club x age-group coverage satisfied or structurally impossible?",
        "  -> Is assigned responsibility consistent with coverage + proportional target?",
        "     -> Was responsibility preserved through placement/repair/search?",
        "        -> Only then inspect lower-priority balance/placement quality.",
        "```",
        "",
        "## Classification vocabulary",
        "",
        "| Classification | Meaning |",
        "|---|---|",
        "| `hard_constraint` | Legality boundary; a violation blocks validity (waivable only where declared). |",
        "| `operational_obligation` | Must be satisfied or surfaced as unresolved manual work. |",
        "| `soft_objective` | Optimized, but never justifies regressing a higher-priority obligation. |",
        "| `operator_decision` | A deliberate operator/agent choice among valid alternatives. |",
        "| `fact_evidence_semantic` | A classification/evidence fact feeding rules, not a rule itself. |",
        "",
        "## Precedence",
        "",
        "The catalog declares only the precedence that actually matters. For hosting:",
        "",
        "```text",
        "hard legality / host_representation",
        "        -> hosting_age_group_coverage",
        "           -> hosting_proportional_balance",
        "              -> placement convenience / calendar quality",
        "```",
        "",
        "and responsibility preservation applies once a target is assigned:",
        "",
        "```text",
        "hosting target -> hosting_responsibility -> placement / repair / search",
        "```",
        "",
        "An agent must not compare two candidates solely by a lower-priority metric when one",
        "candidate regresses a higher-priority operational obligation.",
        "",
        "| Higher priority | Must precede |",
        "|---|---|",
    ]
    pairs = precedence_pairs()
    if pairs:
        for higher, lower in pairs:
            lines.append(f"| `{higher}` | `{lower}` |")
    else:
        lines.append("| — | — |")

    sections = [
        ("Hard constraints", HARD_CONSTRAINT),
        ("Operational obligations", OPERATIONAL_OBLIGATION),
        ("Soft objectives", SOFT_OBJECTIVE),
        ("Operator decisions", OPERATOR_DECISION),
        ("Fact / evidence semantics", FACT_EVIDENCE_SEMANTIC),
    ]
    for title, classification in sections:
        lines.extend(["", f"## {title}", ""])
        lines.extend(_summary_table(entries_by_classification(classification)))

    lines.extend(["", "## Stable ID map", "", "| Emitted code / score path | Rule / objective ID |", "|---|---|"])
    for entry in RULE_CATALOG:
        for code in entry.verifier_codes:
            lines.append(f"| `{code}` (verifier) | `{entry.id}` |")
    for entry in RULE_CATALOG:
        for code in entry.finding_codes:
            lines.append(f"| `{code}` (finding) | `{entry.id}` |")
    for entry in RULE_CATALOG:
        for path in entry.score_paths:
            lines.append(f"| `{path}` (score) | `{entry.id}` |")
    lines.append("")

    lines.extend(["## Entry details", ""])
    for entry in RULE_CATALOG:
        lines.extend(_entry_detail(entry))

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the generated ownership table; ``--check`` verifies it is current."""
    import argparse

    parser = argparse.ArgumentParser(description="Render docs/architecture/rule-catalog.md")
    parser.add_argument("--check", action="store_true", help="fail if the committed doc is stale")
    parser.add_argument("--root", default=None, help="repository root")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    target = root / CATALOG_DOC_PATH
    rendered = render_markdown()
    if args.check:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if existing != rendered:
            print(f"{CATALOG_DOC_PATH} is stale; run scripts/render-rule-catalog.py")
            return 1
        print(f"{CATALOG_DOC_PATH} is current")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(f"Wrote {CATALOG_DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())  # pragma: no cover
