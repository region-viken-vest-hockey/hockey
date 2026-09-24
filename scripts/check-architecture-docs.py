#!/usr/bin/env python3
"""Check semantic architecture drift and extract Mermaid diagrams for CI rendering.

No network or diagram tool is needed for the local check; CI performs a real
Mermaid CLI render on the emitted sources. Rule categories come from the
metadata-only catalog rather than a hand-maintained second classification list.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tournament_scheduler.rule_catalog import CLASSIFICATIONS  # noqa: E402

DOC = ROOT / "docs/architecture/planning-model.md"
README = ROOT / "docs/architecture/README.md"
SYSTEM = ROOT / "docs/system-architecture.md"
APPLICATION = ROOT / "docs/application-architecture.md"
AGENTS = ROOT / "AGENTS.md"

DIAGRAM_HEADINGS = (
    "## System and data flow",
    "## Planning and decision model",
    "## Season lifecycle and authority",
)
DIAGRAM_NAMES = ("01-system-data-flow", "02-planning-decision", "03-season-lifecycle")

# Canonical owners whose deletion/renaming must require an architecture update.
# Paths and classification vocabulary are checked, not replicated business logic.
REQUIRED_OWNERS = (
    "tournament_scheduler/rule_catalog.py",
    "tournament_scheduler/planning_contract.py",
    "tournament_scheduler/final_verification.py",
    "tournament_scheduler/season_planner.py",
    "tournament_scheduler/stage3_optimizer.py",
    "tournament_scheduler/stage3_cpsat.py",
    "tournament_scheduler/quality_objectives.py",
    "tournament_scheduler/pareto.py",
    "tournament_scheduler/application/pareto_convergence.py",
    "tournament_scheduler/application/convergence_refinement.py",
    "tournament_scheduler/application/stage3_session.py",
    "tournament_scheduler/application/stage3_controller.py",
    "tournament_scheduler/application/audit_lifecycle.py",
    "tournament_scheduler/application/canonical_season_service.py",
    "tournament_scheduler/application/canonical_season/lifecycle.py",
    "tournament_scheduler/infrastructure/canonical_season_store.py",
    "tournament_scheduler/season_baseline.py",
    "tournament_scheduler/published_baseline.py",
    "tournament_scheduler/published_mutation_history.py",
    "tournament_scheduler/pipeline/pages_publish.py",
)
MERMAID = re.compile(r"(?ms)^```mermaid[ 	]*\n(.*?)^```[ 	]*$")
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def local_link_errors(source: Path, content: str) -> list[str]:
    """Check checked-in relative link targets without chasing external URLs."""
    errors: list[str] = []
    for target in LINK.findall(content):
        if target.startswith(("https://", "http://", "mailto:", "#")):
            continue
        path = unquote(target.split("#", 1)[0])
        if not path:
            continue
        resolved = (source.parent / path).resolve()
        if not resolved.is_relative_to(ROOT) or not resolved.exists():
            errors.append(f"{source.relative_to(ROOT)}: missing local link {target}")
    return errors


def check_architecture() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    content = DOC.read_text(encoding="utf-8")
    diagrams = MERMAID.findall(content)
    if len(diagrams) != len(DIAGRAM_HEADINGS):
        errors.append(f"expected {len(DIAGRAM_HEADINGS)} Mermaid diagrams, got {len(diagrams)}")
    for heading in DIAGRAM_HEADINGS:
        if heading not in content:
            errors.append(f"missing diagram heading: {heading}")
    for number, diagram in enumerate(diagrams, 1):
        if not re.match(r"\s*flowchart\s+(TB|LR|BT|RL)\b", diagram):
            errors.append(f"diagram {number}: expected a Mermaid flowchart")
        if not diagram.strip():
            errors.append(f"diagram {number}: empty Mermaid source")
    for kind in CLASSIFICATIONS:
        if f"`{kind}`" not in content:
            errors.append(f"missing catalog classification: {kind}")
    for path in REQUIRED_OWNERS:
        owner = ROOT / path
        relative = owner.relative_to(ROOT)
        if not owner.is_file():
            errors.append(f"missing canonical owner: {relative}")
        link = Path("../../") / relative
        if f"({link.as_posix()})" not in content:
            errors.append(f"missing canonical owner link: {relative}")
    for source in (DOC, README, SYSTEM, APPLICATION, AGENTS):
        errors.extend(local_link_errors(source, source.read_text(encoding="utf-8")))
    if "(architecture/planning-model.md)" not in SYSTEM.read_text(encoding="utf-8"):
        errors.append("system architecture must link the semantic planning model")
    if "(planning-model.md)" not in README.read_text(encoding="utf-8"):
        errors.append("architecture README must link the semantic model")
    if "(docs/architecture/planning-model.md)" not in AGENTS.read_text(encoding="utf-8"):
        errors.append("shared agent instructions must link the semantic model")
    return errors, diagrams


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--extract-mermaid", type=Path, metavar="DIRECTORY",
        help="write the validated Mermaid blocks as .mmd files for a real renderer",
    )
    args = parser.parse_args(argv)
    errors, diagrams = check_architecture()
    if errors:
        for error in errors:
            print(f"architecture: {error}", file=sys.stderr)
        return 1
    if args.extract_mermaid:
        args.extract_mermaid.mkdir(parents=True, exist_ok=True)
        for name, source in zip(DIAGRAM_NAMES, diagrams):
            (args.extract_mermaid / f"{name}.mmd").write_text(source.strip() + "\n", encoding="utf-8")
    print(f"Architecture docs OK ({len(diagrams)} Mermaid diagrams; {len(REQUIRED_OWNERS)} owner links)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
