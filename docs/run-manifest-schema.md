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

## Controller decision trace

The manifest `decision_log` and the Stage 3 attempt log are summaries; they do not reconstruct why the bounded convergence controller chose a direction/candidate or which verified improvement a later epoch replaced. Repository code therefore appends a compact, structured, append-only controller trace per run:

```text
<work_dir>/logs/<run_id>/controller_trace.jsonl
```

Each JSONL event records only explicit decision inputs/outputs and measurable effects: event family, epoch, direction/finding, candidate-before/after fingerprints, selected option/ref, objective/verification deltas, Pareto-frontier add/prune and terminal/pause reason, plus a concise explicit rationale. It never contains hidden chain-of-thought or raw model reasoning. The file is keyed by `run_id` and sequence number and is appended across process restarts/resume rather than truncated. Read/summarize it with `tournament_scheduler.pipeline.controller_trace.read_controller_trace` / `summarize_controller_trace` / `candidate_lineage`.

Event families cover the whole controller path, not only the convergence epochs:

- `stage_gate` / `stage4_materialization` / `promotion` -- pipeline gate and handoff transitions;
- `stage_decision` -- every ordinary Stage 1/2/3 gate and Stage 3 optimize/keep/apply decision outside the convergence sub-loop, written by `application.decisions.record_llm_decision` with stage/capability, chosen action, candidate/baseline refs and counted verification evidence;
- `epoch_start` / `direction_selected` / `options_enumerated` / `frontier_mutation` / `candidate_mutation` / `epoch_end` -- bounded-convergence epochs;
- `operator_question` / `operator_answer` -- the escalation boundary, linking the question id, answer/actor and the run/candidate/export identity it was asked against;
- `audit_verdict` / `review_selection` / `terminal` / `pause` -- audit and review decisions, plus terminal and resumable-pause reasons;
- `publication` -- the Pages boundary, recording a blocked/dry-run/rejected/completed publication with the source export fingerprint, sanitized bundle/target fingerprints and the resulting Pages commit/branch/verify status.

A decision boundary that holds no explicit run id (a standalone operator answer/publication) resolves the trace scope from the run manifest's active `run_id` instead of creating a second unscoped file.

The detailed JSONL stays in the run workspace, never in the Stage 4 export tree or the public GitHub Pages bundle. The sanitized `evidence_bundle.json` carries a stable run/path reference plus a compact `controller_trace` summary (event counts, directions, candidate transitions, frontier mutations, terminal/pause reason), so a reviewer can locate and analyze the detail without copying unbounded decision evidence into a publishable artifact.

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
