# AI operator roadmap

This roadmap records the current architectural direction for RVV Miniputt. Historical implementation details remain available through Git history and `.ps-next/HISTORY.md`; this file describes the active path forward.

## Current foundation

### 1. Structured operator state

The repository has a durable run manifest, stage checkpoints, structured capability results, logs, fingerprints, and publication history. These are the common evidence surface for humans and agent harnesses.

### 2. Goal-oriented operator entry point

`scripts/rvv-miniputt operator run` is the main executable workflow. It coordinates deterministic validation, calendar collection, planning, evaluation, export, and operator questions without requiring a harness to recreate Stage 1–4.

### 3. Source health and recovery

Source health is explicit: reachability, event count, freshness, cache state, and recovery hints are inspectable. Stage 2 owns the final source-readiness decision.

The active architecture now separates:

- unresolved sources (`blocked` compatibility/recovery signal)
- true planning blockers (`blocking_sources`)
- temporary known exceptions (`temporarily_unresolved_sources`)
- the canonical continue decision (`planning_ready`)

Tønsberg and Sandefjord Penguins are temporary non-blocking BookUp exceptions only while authenticated access remains unreliable from Lima. They remain unresolved sources and recovery targets.

### 4. Reusable BookUp authentication

BookUp Playwright state is reusable under `.pipeline/auth/`. Authentication/MFA establishment is performed in a visible browser on the macOS host when needed; normal headless Lima runs reuse the saved state instead of starting a fresh credential flow.

### 5. Reproducible planning and evaluation

Planner seeds/configuration, fairness checks, validation, and candidate evaluation remain deterministic Python behavior. AI may explain or investigate but does not define validity.

### 6. Durable human escalation

Operator questions/answers can be inspected and promoted with explicit scopes. Publication remains a separate authorized action.

### 7. Shared RVV runbook and thin adapters

`.agents/skills/rvv/` is the shared policy/runbook. Pi, Claude, Codex, ChatGPT, and OpenCode should all consume the same operating contract.

Harness adapters may provide UI, progress, or browser automation. After using such a capability, they return recovered data to the shared implementation and let Python decide the next state. They must not contain independent source exceptions, fairness thresholds, recovery semantics, or Stage 1–4 algorithms.

The previous separate packaged UI surface has been retired. Any future non-technical interface should be built as another thin consumer of the same capabilities, not as a parallel implementation.

## Next priorities

### A. Finish adapter convergence

- Keep remaining harness command files short.
- Move operational details into `.agents/skills/rvv/RUNBOOK.md` when more than one harness needs them.
- Prefer invoking `scripts/rvv-miniputt` over direct internal stage-module calls.
- Add regression tests whenever a harness is found to make its own readiness/policy decision.

### B. Improve source provenance and evidence

- Make it easy to distinguish fresh scrape, cache reuse, recovery injection, and temporary unresolved state.
- Preserve date coverage and last-known-good evidence.
- Surface suspicious event-shape/count changes before approval.
- Keep recovery targets machine-readable.

### C. Strengthen BookUp lifecycle management

- Detect missing/expired Playwright auth state clearly.
- Keep host-side recovery instructions short and deterministic.
- Never expose session state or credentials in logs/artifacts.
- Remove the temporary Tønsberg/Sandefjord readiness exception when authenticated headless access is reliable enough to make it unnecessary.

### D. Continue planner explainability

- Keep fairness/quality gates deterministic.
- Improve explanations of high-impact trade-offs and constraint pressure.
- Keep candidate comparison reproducible.
- Avoid harness-specific retry/score policy.

### E. Keep publication separate and auditable

- Generation/review/publish/rollback remain distinct actions.
- Preserve bundle fingerprints, privacy checks, and public confirmation gates.
- Prefer protected GitHub Actions workflows for trained browser-only operators.

### F. Simplify maintenance and handover

- Keep README and operator docs oriented around one workflow.
- Remove obsolete implementation surfaces instead of carrying them as optional alternatives.
- Keep Microsoft 365, GitHub, WordPress, Spond, and calendar-source ownership club-controlled with backup recovery paths.

## Architectural tests to preserve

The test suite should continue to enforce these boundaries:

- application modules do not import transport/UI layers
- harness run wrappers refer to the shared RVV runbook
- Stage 2 readiness is Python-owned
- recovery normalization uses the same readiness classifier
- BookUp state is stored under the private `.pipeline/auth/` path
- deterministic dependency locking stays fresh
- publication cannot happen as an implicit side effect of a normal run

## Definition of success

The roadmap is working when a maintainer can change a policy such as source readiness or recovery exactly once in the shared Python/runbook layer, and every harness behaves consistently without a second round of Claude/Pi/Codex-specific edits.
