# AI operator run manifest schema

This document specifies the versioned run-manifest and capability-result contract used by agents and operators to understand workspace state, capability outcomes, evidence, confidence, artifacts, blockers, and suggested next actions without parsing human-oriented console logs.

It complements, and does not replace, the per-stage checkpoint files documented in `tournament_scheduler/pipeline/state.py` (`.pipeline/stage1_config.json` … `stage4_export.json`). Those remain the source of truth for stage data; the run manifest is the higher-level operator-facing summary layered on top.

## Where it lives

```text
<work_dir>/run_manifest.json      # default work_dir is .pipeline
```

Written/read via `tournament_scheduler.pipeline.run_manifest.RunManifest`.

## Capability result contract

A capability can report a structured outcome via `tournament_scheduler.pipeline.capability_result.CapabilityResult`:

```json
{
  "schema_version": 1,
  "capability": "scraping",
  "status": "warning",
  "summary": "6 source(s) scraped, 1 blocked",
  "evidence": [],
  "confidence": 1.0,
  "artifacts": [],
  "problems": ["rvv-club-x.no"],
  "suggested_actions": [],
  "requires_human": false
}
```

Core fields:

| Field | Meaning |
|---|---|
| `schema_version` | Capability-result schema version. |
| `capability` | Capability that produced the result. |
| `status` | `ok`, `warning`, `blocked`, or `failed`. |
| `summary` | Concise human-readable outcome. |
| `evidence` | Concrete facts supporting the result. |
| `confidence` | Informational confidence value. |
| `artifacts` | Produced paths/identifiers. |
| `problems` | Concrete findings encountered. |
| `suggested_actions` | Human-readable next-step hints. |
| `requires_human` | Whether the result requires an explicit human decision. |
| `actions` | When present, validated structured actions available to the operator/controller. |

`blocked` indicates the workflow cannot resolve the current state without a human decision/authorization. `warning` means work completed but the finding must remain visible for review. `failed` means the capability itself did not complete.

## Run manifest contract

The manifest is durable operator state for the current workspace. It records, as applicable:

- schema version and run identity;
- objective and current/last/next capability;
- capability results;
- input/source fingerprints and timestamps;
- structured action transitions;
- pending/answered/stale human questions;
- concise persisted decision rationale;
- generated artifacts and final outcome;
- diagnostics when manifest persistence/recovery degrades.

The exact JSON shape is versioned in code and tests. Consumers should tolerate additive fields and should not infer semantics from undocumented private keys.

## Decision state

Human questions and operator decisions are durable workspace state rather than transient terminal prompts. Questions have stable identities and may be scoped to the appropriate lifetime (for example run/input/season/workspace depending on the decision). Superseded or stale decisions remain available for audit rather than being silently reused in a new context.

Use the supported operator commands/application capabilities to list, answer, or promote questions. Do not edit `run_manifest.json` manually as the normal workflow.

## Persistence guarantees

Manifest writes are expected to be atomic and failures must be visible. A corrupted manifest is treated differently from a workspace that has never had one. Approval-required actions must not proceed when the durable audit state cannot be written safely.

The manifest is operational metadata. It is not a substitute for Stage 1–4 checkpoints and does not make generated output authoritative over `input.xlsx`, source evidence, or verifier results.

## Versioning

Schema changes should prefer additive, backward-compatible evolution. If a breaking change is necessary:

1. increment the relevant schema version;
2. keep an explicit compatibility/migration path for persisted workspace state where practical;
3. update readers/tests before writers rely on the new shape;
4. keep adapters dependent on application/contract semantics rather than duplicated JSON parsing rules.

## Security and privacy

The manifest may contain operational findings and concise decision rationale, but must not persist credentials, tokens, MFA/session material, browser storage state, contact data that is not required for the workflow, or private model chain-of-thought.

See [`security.md`](security.md), ADR 0002, and `.agents/skills/rvv/SKILL.md` for the surrounding operational boundaries.
