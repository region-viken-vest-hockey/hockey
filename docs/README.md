# Documentation map

This directory contains current operational documentation, durable architectural decisions, external reference material, and historical context. They do not all have equal authority.

## Active documentation

These documents should describe the system **as it works now** and should be updated when the corresponding behavior changes.

| Document | Purpose |
|---|---|
| [`system-architecture.md`](system-architecture.md) | Current high-level system boundaries and source-of-truth model |
| [`engineering-principles.md`](engineering-principles.md) | Engineering rules that apply across the repository |
| [`rvv-miniputt-pipeline.md`](rvv-miniputt-pipeline.md) | Operator/pipeline behavior and supported command surface |
| [`rvv-miniputt-input-formats.md`](rvv-miniputt-input-formats.md) | Canonical `input.xlsx` and registration interchange formats |
| [`rvv-miniputt-rules-report.md`](rvv-miniputt-rules-report.md) | How scheduling rules are represented in reports |
| [`application-architecture.md`](application-architecture.md) | Application-layer dependency rules used by architecture tests |
| [`run-manifest-schema.md`](run-manifest-schema.md) | Durable run/decision state contract |
| [`ci.md`](ci.md) | Verification and CI behavior |
| [`security.md`](security.md) | Security and secret-handling requirements |
| [`ownership-and-handover.md`](ownership-and-handover.md) | Operational ownership and transfer to another maintainer |
| [`power-automate-github-sync.md`](power-automate-github-sync.md) | Microsoft 365 → repository integration |
| [`rvv-miniputt-deployment-architecture.md`](rvv-miniputt-deployment-architecture.md) | Deployment/publication architecture |

The root [`README.md`](../README.md) is the main human entry point. Shared agent policy lives in [`../.agents/skills/rvv/SKILL.md`](../.agents/skills/rvv/SKILL.md), not in harness-specific command files.

## Architectural decisions

[`adr/`](adr/) contains Architecture Decision Records. ADRs explain *why* a durable direction was chosen and the precedence between decisions. They are not a live implementation backlog and should not be copied into adapter prompts.

## Reference material

- [`kampveileder-for-3-mot-3-spill-revidert-august-25.pdf`](kampveileder-for-3-mot-3-spill-revidert-august-25.pdf) is external NIHF rule/reference material.
- [`ai-operator-product-direction.md`](ai-operator-product-direction.md) is product rationale/background. Current operational semantics are defined by code, the active docs above, the RVV skill, and accepted ADRs.

## Historical material

- [`2025/`](2025/) is retained as historical season/reference material.
- `ai-operator-roadmap.md`, while retained for implementation history, is **not the current backlog**. GitHub issues are authoritative for unfinished work.
- Dated architecture reviews, generated decision logs, and generated architecture visualizations should not live in the active documentation tree. Git history preserves old snapshots when they are useful for archaeology.

## Maintenance rules

1. Do not create a second implementation backlog in `docs/`; use GitHub issues.
2. Prefer one canonical explanation over near-identical copies in Claude/ChatGPT/Codex/OpenCode adapters.
3. When behavior changes, update the smallest active document that owns that behavior instead of appending another review document.
4. Keep generated evidence in `.pipeline/`, `export/`, test fixtures, or CI artifacts—not beside maintained documentation.
5. If a dated investigation leads to a durable decision, capture the decision in an ADR and remove the temporary review document once it has served its purpose.
