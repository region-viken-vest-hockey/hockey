---
name: rvv
description: Canonical shared runbook for RVV Miniputt season planning, canonical season maintenance, calendar-source recovery, review, export, and publication. Use for work on the hockey repo's planning pipeline and promoted season state.
---

# RVV Miniputt shared runbook

This is the canonical agent-facing operating policy for RVV Miniputt. It is harness-neutral: Claude, Codex, ChatGPT, Pi and future agent harnesses should all consume this same policy and the shared procedures under `.agents/commands/rvv-miniputt/`.

Use repository code for facts, hard constraints, validation, search/solver mechanics, persistence, export and publication safeguards. Use the active agent only for contextual soft judgment among actions the repository exposes. Do not create a second harness-local scheduler, decision controller, audit judge, or scraper.

Read `AGENTS.md` first for repository-wide precedence and hygiene rules.

## Operating lifecycle

There are two operating phases.

### 1. Initial season creation

Create and review the season through the canonical Stage 1–4 pipeline. For checkpoint-reviewed agent operation:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

Inspect the repository-owned `DecisionContext` after each pause and continue only through declared actions. Stage checkpoints under `.pipeline/` are transient run state.

When a verified schedule is deliberately accepted as the operational baseline for club review/ice booking, promote it explicitly through the shared `season` procedure. Promotion is not an automatic side effect of an ordinary planner run.

### 2. Promoted-season maintenance

After promotion, `season/<season>/schedule.json` plus `season/<season>/decisions.json` are the durable operational truth. Use canonical `season` operations for approvals, targeted moves, bounded replanning and re-export rather than regenerating the season from scratch:

```bash
scripts/rvv-miniputt season status --season 2026-2027
scripts/rvv-miniputt season approvals --season 2026-2027
scripts/rvv-miniputt season findings --season 2026-2027
scripts/rvv-miniputt season repair-options --season 2026-2027 --finding <finding-id>
scripts/rvv-miniputt season search --season 2026-2027 --finding <finding-id>
scripts/rvv-miniputt season apply-repair --season 2026-2027 --option-id <id> --expected-revision <rev>
scripts/rvv-miniputt season accept-deviation --season 2026-2027 --finding <finding-id> --note "ice unavailable"
scripts/rvv-miniputt season revoke-acceptance --season 2026-2027 --finding <finding-id>
scripts/rvv-miniputt season approve --season 2026-2027 --tournament-id <id> --note "ice booked"
scripts/rvv-miniputt season unapprove --season 2026-2027 --tournament-id <id> --note "booking changed"
scripts/rvv-miniputt season move --season 2026-2027 --tournament-id <id> --date 2026-10-18
scripts/rvv-miniputt season normalize-placements --season 2026-2027
scripts/rvv-miniputt season replan --season 2026-2027 --iterations 4000
scripts/rvv-miniputt season diff --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season apply --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season export --season 2026-2027
```

For a localized defect (an unresolved hosting obligation, a manual placement, a host-controlled movable-ice opportunity, a participation strong-goal deviation), prefer the finding-directed loop over whole-season replanning:

```text
season findings            -> fresh, revision-bound facts (never the promoted snapshot)
season repair-options       -> direct/coupled options for ONE selected finding
season search               -> bounded neighborhood search when cheaper options are insufficient
season apply-repair         -> atomic, full-season-verified, revision-bound apply + delta
```

`season_plan` means the actual schedule: `season normalize-placements` upgrades an already-generated canonical plan to the placed/provisional/unplaced model in place (removing genuine slot-search failures and fixed-external-conflict placements into stable unresolved obligations while preserving approved/provisional placements) without rerunning Stage 1-3. The same normalization runs automatically before export, so an unplaced obligation never appears as scheduled hockey.

Findings are independent facts, not a mandatory queue: select whichever finding matters next. Options and findings are bound to the canonical revision they came from; applying against a changed revision is rejected as stale and leaves canonical state unchanged. A `bounded_search_exhausted` participation deviation is not proof of infeasibility -- request another bounded search rather than recording it as `proven_infeasible`. The same epistemic rule applies to any bounded repair/search family: **zero options from the configured bounded search means only that this search found none**. Do not summarize it as "genuinely exhausted", "no solution exists" or equivalent unless a deterministic capability explicitly returns `proven_infeasible`/an exhaustive proof. A remaining deviation an operator deliberately decides to live with may be persisted with `season accept-deviation` as an explicit `operator_accepted` decision: it never changes the target or the schedule, becomes stale (and re-surfaces the finding) if the target changes or the deviation gets worse, and is reopened with `season revoke-acceptance`. Repairs must never transfer hosting responsibility to a club that does not owe it, and must never make another club's hosting deficit worse. A genuine unplaced obligation (no `Tournament` exists yet) is repaired through the same loop: `unplaced_placement_repair` materializes it into a real verified tournament along the escalating ladder (same-date start time, same-host date, bounded participant reselection, capacity release, coupled cross-age exchange), and its finding reports `search_coverage` so `search_incomplete` (untried supported dimensions remain), `option_available` and `bounded_search_exhausted` are never conflated. Respect the repository's repair-cost order: when a tournament's host/date/arena/time are already legal and only the selected roster conflicts, the first options are placement-preserving roster substitutions, so do not move the slot or escalate to a broader search while a verified substitution exists. Every returned option is measured on one canonical objective vector (verifier defect counts plus change cost and host-confirmation dependencies, merged with the shared Stage-3 soft-quality objectives and canonical travel) and the report marks the non-dominated `pareto` set; each option also carries `quality_vs_current`/`travel` and the apply delta returns the same quality/travel before/after evidence. Prefer a non-dominated option over a dominated one when its trade-off is acceptable.

Never hand-edit canonical season JSON to work around a lock or verifier.

## Command boundary

Shared procedures live under `.agents/commands/rvv-miniputt/` and are used by every harness. The repository-local transport is:

```bash
scripts/rvv-miniputt ...
```

Harness adapters may add genuinely necessary UI/transport integration, but must not redefine shared pipeline policy or create an independent reasoning loop. The active harness itself should read the shared procedure, inspect repository output, choose a declared action and invoke the next canonical command.

## Inputs

The four-stage planner uses:

- root `input.xlsx` as the controlled planner workbook;
- reviewed registration exports only through the controlled import path when `Lag` needs rebuilding;
- external calendar/source evidence collected in Stage 2;
- browser/session access only when a configured source genuinely requires interactive recovery.

`Årshjul for aktiviteter.xlsx` and the public registered-team workflow are related repository workflows but are not Stage 1–4 planner policy inputs.

See `docs/rvv-miniputt-input-formats.md` for the workbook contract.

## Four-stage pipeline

### Stage 1 — configuration

Repository code validates and normalizes the controlled workbook.

Agent policy:

- do not invent missing teams, clubs, age groups or settings;
- treat invalid/reversed season windows and invalid identities as input problems, not soft preferences;
- choose only from actions declared by the returned context.

### Stage 2 — source/calendar evidence

Repository code owns extraction results, cache/provenance, source status and validation.

Agent policy:

- inspect blocked, empty and suspiciously sparse sources before trusting the plan;
- prefer bounded recovery/retry actions exposed by the repository;
- do not declare a source healthy merely because a request technically succeeded;
- browser-assisted recovery is optional and harness-neutral. If needed, perform only browser/navigation extraction and return recovered data through the canonical `recovery-inject` + `scrape-merge` path described in `.agents/commands/rvv-miniputt/scrape-llm.md`;
- do not maintain a dedicated browser scraper inside a harness adapter.

Useful commands:

```bash
make sources-status
make calendars
scripts/rvv-miniputt scrape --club <name>
scripts/rvv-miniputt recovery-targets
```

### Stage 3 — planning

Repository code owns the normalized planning problem, hard constraints, candidate schema, solver/search primitives, deterministic verification and reproducible metrics.

Agent policy:

- never accept a candidate with hard verification failures;
- use exposed optimize/refine/apply/keep/request actions rather than editing the season plan in prose;
- compare candidates using returned metrics/findings rather than intuition alone;
- prefer Pareto/multi-objective evidence when several valid trade-offs exist;
- do not turn a one-run preference into a new hard rule. If RVV wants a preference to become mandatory, implement/test it in deterministic code/configuration.

Typical soft dimensions include participation balance, hosting distribution, temporal spacing, opponent diversity/repetition, travel and source uncertainty.

Participation targets are a **strong operational goal with bounded, evidenced relaxation**, not an ordinary low-priority soft preference and not a hard legality boundary:

- Python resolves the configured per-half/season targets, minimizes deviation, and classifies every material deviation as `avoidable`, `proven_infeasible`, `bounded_search_exhausted` or `operator_accepted` (see `participation_targets.py`);
- a known-avoidable participation regression must not be selected merely to improve a lower-priority quality metric (travel, opponent diversity, start time, ...);
- `bounded_search_exhausted` is **not** proof of unavoidability: request another bounded search or escalate before accepting it, and never call it `proven_infeasible`;
- `operator_accepted` is an explicit, durable operator decision (persisted during promoted-season maintenance), never a planner outcome; it is scope/target/magnitude-bound and never edits the configured target;
- cross-half compensation (`2 + 4` for a `3 + 3` target) is legal and is recognized as season-total-complete with an explicit half-distribution deviation;
- an explicit `participation_hard_max` is a separate, genuinely hard rule and is never inferred from the target. Ordinary bounded target deviation never requires an operator waiver.

Before switching between pipeline work and promoted-season maintenance, keep their state scopes explicit. An unpromoted Stage 3/Stage 4 candidate in `.pipeline` may differ from `season/<season>/schedule.json`. `season findings` / `season repair-options` / `season search` always describe the promoted canonical season, not the just-exported candidate. Until promotion, analyze the candidate through its DecisionContext/Stage3Session, verifier/evidence bundle, audit context and export artifacts; never blend counts/findings from the two fingerprints.

Once a season has been promoted, Stage 3 is baseline-aware by default. A normal planning run whose window matches canonical state adopts that schedule as its baseline instead of regenerating it:

- approved/placement-locked tournaments are hard-preserve constraints;
- unapproved tournaments remain optimizable, but weighted change cost is part of the search objective so prefer the smallest justified change;
- use `season replan` around the canonical baseline, inspect `season diff`, and persist only through verified `season apply`;
- a rejected candidate must leave `schedule.json` and `decisions.json` unchanged.

Interactive Stage 3 is one explicit persisted session with candidate revisions. Inspect it with:

```bash
scripts/rvv-miniputt stage3 session --work-dir .pipeline --json
```

The view is authoritative for the active run's status, current candidate revision/fingerprint, pending decision, resolved run-scoped decisions, finalized revision/fingerprint, `search_history` and legal next transitions. Use it to debug a resume problem instead of reconciling several JSON files.

A pending candidate-scoped decision (arena/time conflict, local repair, attempt comparison) refers to the current attempt revision, so answer it against that exact fingerprint; the adopted plan is retained separately as the session baseline that `keep_baseline` restores.

Escalating a candidate-scoped decision with `request_operator` (which requires a `question`) pauses the session on that exact current revision/fingerprint: it never restores the baseline, rewrites the checkpoint or finalizes Stage 3. The pause and question are inspectable from `stage3 session`. After the operator answers, continue from the same revision. When a hard-valid optimized attempt is the current candidate and `keep_baseline` would restore the previous attempt, retain the current attempt explicitly with `apply_candidate` against the context's `candidate_ref`; do not use `keep_baseline` to mean "accept the work I just saw".

Stage 3 continuation is evidence- and strategy-driven, not attempt-count-driven. A raw number of attempts never removes `optimize_plan`, and the returned `facts.search_history` summarizes what has already been tried (actions used, unique action signatures, candidate revisions, hard violations before/now, repeated no-progress actions, last actions) so you can choose a genuinely different repair family, search scope, roster/date hypothesis or evidence investigation, or accept/escalate/abort when continuing has no useful direction. Deterministic loop safety still applies: a stale candidate action is rejected, and repeating the exact same action against the exact same candidate after it made no progress is rejected as `repeated_no_progress_action`. The only attempt-count bound is a generous emergency circuit breaker surfaced as a technical safety failure (runaway/broken orchestration), never as proof that the season is unsolvable.

Approval/lock state lives in `decisions.json`, separate from schedule facts. `season approve` re-verifies the current placement; approval is never a waiver of hard rules. `season unapprove` restores editability. `season approvals` lists status. If protected scheduling facts change, the stored fingerprint becomes a deterministic `stale_approval`, its lock is dropped, and explicit reapproval is required.

### Stage 4 — export/review

Stage 4 re-verifies the selected candidate before serialization.

Agent policy:

- hard verification failure blocks export/publication;
- review manual arena/hosting/calendar follow-up separately from plan-quality warnings;
- generated output is derived data: correct source/config/code/canonical state and regenerate rather than permanently patching HTML/CSV/Excel/iCal;
- use the Stage 4 `output_files` map to know what the run actually produced;
- after canonical season state changes through move/apply/approve/unapprove, regenerate the exact current projection with `season export` before audit/publication.

## Stage gating policy

The repository's `DecisionContext` is authoritative. A hard violation always blocks `proceed`.

### Stage 1

- `proceed` when at least one calendar source is configured and the date range is a realistic hockey season window;
- `abort` when no sources are configured or the date range is clearly wrong.

### Stage 2

- `proceed` when enough configured sources have usable evidence for meaningful planning;
- prefer repository-exposed retry/recovery actions for suspicious blocked sources;
- `abort` when missing/blocked source evidence makes planning meaningless.

### Stage 3

- `proceed` when the plan is structurally plausible and hard-valid;
- `abort` when the plan is empty/obviously wrong because of upstream/configuration failure;
- planning-quality tradeoffs remain contextual soft judgment once hard validity is satisfied.

Do not introduce a fixed threshold or magic weight in the runbook to solve a one-off tradeoff.

## Structured decision protocol

`run --interactive` returns a `DecisionContext` containing facts, hard violations, warnings, metrics, available actions and argument/template information.

For each pause:

1. read the current context;
2. if a hard violation exists, do not bypass it;
3. choose exactly one returned available action;
4. use only the action's declared argument shape;
5. submit a concise operational rationale;
6. invoke the next canonical command and reassess the new context.

Transport rule: fill the returned `decision_action_template` and pass the **entire resulting JSON object** to `--decision-action` (or `--decision-action-file`). `rationale` is inside that JSON object, and every action-specific parameter is nested under `arguments`; never invent separate flags such as `--rationale` or action-specific CLI options.

Use `.agents/commands/rvv-miniputt/run.md` for the explicit resume contract. Do not infer `--resume-from` from intuition. Do not persist or request hidden/private reasoning; durable records need only the action, relevant facts/outcome and concise rationale.

## Semantic safety-net audit

After every Stage 4/canonical export and before publication, an interactive harness must perform the semantic safety-net audit itself using the active conversation/model. Do not implement a second harness-local model call or audit engine.

Start with the bounded overview:

```bash
scripts/rvv-miniputt operator audit-context
```

The default context contains counts, distributions, worst/top-N examples, fingerprints and an evidence index, not the whole evidence universe. Pull exact detail only where needed through the canonical query capability:

```bash
scripts/rvv-miniputt operator audit-evidence --item 2
scripts/rvv-miniputt operator audit-evidence --tournament <durable-id>
scripts/rvv-miniputt operator audit-evidence --club Kongsberg
scripts/rvv-miniputt operator audit-evidence --category participation_shortfalls
scripts/rvv-miniputt operator audit-evidence --unresolved
```

Query results are bound to the same run/export fingerprint as the overview. Never combine stale evidence from another export.

Audit checklist:

1. Antall cuper pr lag?
2. Antall hjemmeturneringer pr lag?
3. Lengde på turneringer?
4. Er det faktisk ledig tid på is?
5. Deltar vertsklubben i samme turnering?
6. Deltar hvert lag maksimalt én gang per dag?
7. Er det normalt maks 2 lag fra samme klubb, med 3 kun som synlig unntak?
8. Er eksportformatene konsistente?
9. Ser harnesset andre materielle problemer eller manglende regler vi ikke allerede har tenkt på?

Submit the harness verdict through `operator audit-submit`. When no interactive harness is active, the documented headless `operator audit-run --backend <name>` path may call an LLM backend instead.

A harness `FAIL` or `REVIEW_REQUIRED` may occur even when deterministic checks pass. It can never override a deterministic hard `FAIL`; an incomplete audit is never `PASS`.

## Operator waivers for hard planning rules

Structural invariants such as corrupt serialization, invalid tournament identity or malformed data are never waivable. Explicitly classified hard planning rules may only be waived by an authorized operator for a precise scope.

The planner/optimizer/agent may suggest a waiver, but may never create, broaden or silently infer one. Canonical operator capability:

```bash
scripts/rvv-miniputt waiver list [--all]
scripts/rvv-miniputt waiver create --rule participation_hard_max_exceeded \
  --club "Frisk Asker" --team "Frisk Asker 4" --age-group U11 \
  --tournament <id> \
  --allowed-value 8 --reason "operator-approved exception"
scripts/rvv-miniputt waiver revoke <waiver-id> --reason "withdrawn"
```

A waiver is only relevant to a genuinely hard, explicit ceiling such as `participation_hard_max`. An ordinary bounded participation-target deviation is strong-goal evidence, not a hard rule, and does **not** require a waiver. A matching waiver is narrowly scoped and becomes invalid when relevant tournament/team/date/value facts leave that scope. Waived violations remain visible and downgrade publication readiness; do not use `--non-strict` or global cap changes as substitutes.

## Human escalation

Escalate when the repository actually requires human authority/information, for example:

- a real policy exception/change;
- credentials/MFA or an unavailable interactive access step;
- an impossible hard-constraint situation requiring organizer action;
- public publication or rollback approval.

Do not escalate merely because a safe repository action can be retried/refined automatically.

Human decision queue:

```bash
make questions
make answer ID=<id> ANSWER='<answer>'
make operator-run
```

## Publication

Planning/export does not imply publication. The export being published must represent the current canonical revision. If canonical schedule/decision state changed since the current export, run `season export` first and perform a fresh semantic audit.

Useful commands:

```bash
make audit-context
make audit-evidence ARGS='--item 2'
make audit-run BACKEND=<claude|openai|llm_bridge>
make audit-submit RESULT_FILE=<path>
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

Publication creates a separate allowlisted public bundle. Review packets and Spond exports are private/review artifacts by default. Rollback is explicit:

```bash
make publish-history
make rollback RUN_ID=<id> CONFIRM_PUBLIC=1
```

## Related public workflows

The repository also manages:

```bash
make aktivitetskalender
make registered-teams CSV=<reviewed-registration-export.csv>
```

Their publishing variants use the same publication machinery but are not Stage 1–4 planner stages.

## Documentation ownership

Use these rather than creating overlapping notes:

- `README.md` — what the system does, inputs/outputs, normal operation;
- `docs/system-architecture.md` — current end-to-end boundaries;
- `docs/rvv-miniputt-pipeline.md` — Stage 1–4 and canonical-season workflow;
- `docs/rvv-miniputt-input-formats.md` — workbook/input contract;
- `docs/adr/` — durable architectural rationale;
- GitHub issues — unfinished implementation work.
