# Documentation map

The root [`README.md`](../README.md) is the starting point for understanding what RVV Miniputt does, its inputs and outputs, and the normal operator workflow.

This directory contains maintained documentation for the **current** system, accepted architecture decisions, and a small amount of external or prior-season reference material.

## Authority

When documents disagree, follow [`../AGENTS.md`](../AGENTS.md): current code/tests/controlled inputs first, then the shared RVV runbook, then maintained docs, then accepted ADRs. Historical implementation sequence is context only.

## Maintained current documentation

| Document | Purpose |
|---|---|
| [`system-architecture.md`](system-architecture.md) | Current system boundaries, sources of truth and workflows. |
| [`rvv-miniputt-pipeline.md`](rvv-miniputt-pipeline.md) | Four-stage season-planning flow, review and publication workflow. |
| [`rvv-miniputt-input-formats.md`](rvv-miniputt-input-formats.md) | Canonical planner workbook and registration interchange contract. |
| [`rvv-miniputt-rules-report.md`](rvv-miniputt-rules-report.md) | Maintained rules/report wording used by review output. |
| [`application-architecture.md`](application-architecture.md) | Application-layer dependency and adapter boundaries. |
| [`run-manifest-schema.md`](run-manifest-schema.md) | Durable run/decision state contract. |
| [`engineering-principles.md`](engineering-principles.md) | Repository-wide engineering and maintenance principles. |
| [`ci.md`](ci.md) | Verification and CI behavior. |
| [`security.md`](security.md) | Credential/session handling requirements. |
| [`ownership-and-handover.md`](ownership-and-handover.md) | Operational ownership, recovery and maintainer transfer. |
| [`power-automate-github-sync.md`](power-automate-github-sync.md) | Microsoft 365 registration/integration flow. |
| [`rvv-miniputt-deployment-architecture.md`](rvv-miniputt-deployment-architecture.md) | Current runtime and GitHub Pages publication architecture. |
| [`ai-operator-product-direction.md`](ai-operator-product-direction.md) | Short statement of the operator/product boundary. |

Shared agent operating policy lives in [`../.agents/skills/rvv/SKILL.md`](../.agents/skills/rvv/SKILL.md), not in harness-specific command files.

## Architecture decisions

[`adr/`](adr/) contains accepted ADRs. ADRs explain durable decisions and rationale; they are not a backlog or runbook.

## Reference material

- [`kampveileder-for-3-mot-3-spill-revidert-august-25.pdf`](kampveileder-for-3-mot-3-spill-revidert-august-25.pdf) is external NIHF reference material.
- [`2025/`](2025/) is retained prior-season/reference material, not current operational documentation.
- `ai-operator-roadmap.md` is only a small compatibility pointer for older source comments; the roadmap itself is retired to Git history and GitHub issues.

## Maintenance rules

1. Keep one current home for each fact. Update that document instead of adding another review/design note.
2. GitHub issues are the live implementation backlog. Do not add project-local task lists, agent backlogs, or WIP plan files.
3. Git history is the archive. Remove completed roadmaps and superseded investigations once their durable outcome is represented by current code/docs/ADRs.
4. Harness adapters stay thin and may not own independent Stage 1–4 policy.
5. Generated runtime evidence belongs under `.pipeline/`, `export/`, tests, or CI artifacts, not in maintained docs.
6. `rvv-miniputt-rules-report.md` is review wording, not a second business-policy engine; keep it synchronized with the actual workbook contract, planner and verifier.
