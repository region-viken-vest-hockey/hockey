---
name: rvv
description: Canonical shared runbook for RVV Miniputt season planning, calendar-source recovery, plan review, export, and publication. Use for work on the hockey repo's planning pipeline.
---

# RVV Miniputt shared runbook

This is the canonical agent-facing operating policy for the RVV Miniputt repository.

Use repository code for facts, hard constraints, validation, search/solver mechanics, persistence, export and publication safeguards. Use agent judgment only for contextual soft decisions among actions the repository exposes.

Treat this as a stage-by-stage pipeline, not a black box: review the checkpoint (`stage1_config.json`, `stage2_scraping.json`, `stage3_planning.json`, `stage4_export.json`) after each stage before continuing.

Read `AGENTS.md` first for repository-wide precedence and hygiene rules.

## Command boundary

Shared non-Pi command procedures live under `.agents/commands/rvv-miniputt/`. Claude, Codex, ChatGPT, and future harness adapters should load the matching shared procedure and add only harness-specific metadata/transport/UI behavior. Do not copy command procedure text or RVV policy into every harness.

### Pi

Pi provides the RVV-specific `/rvv-miniputt ...` command/tool integration. Use the Pi command directly there; it is not a shell binary.

Agent-callable tools mirror the slash commands 1:1:

| Tool | Equivalent slash command |
|---|---|
| `rvv_miniputt_run` | `/rvv-miniputt run` |
| `rvv_miniputt_publish` | `/rvv-miniputt publish` |
| `rvv_miniputt_status` | `/rvv-miniputt status` |
| `rvv_miniputt_logs` | `/rvv-miniputt logs` |
| `rvv_miniputt_calendars` | `/rvv-miniputt calendars` |
| `rvv_miniputt_scrape` | `/rvv-miniputt scrape` |
| `rvv_miniputt_scrape_llm` | `/rvv-miniputt scrape-llm` |

Use `rvv_miniputt_scrape` for single-club troubleshooting and `rvv_miniputt_scrape_llm` (backed by a Playwright worker) for blocked SPA/calendar sources.

Pi's `/rvv-miniputt run` / `rvv_miniputt_run` adapter runs the semantic safety-net audit automatically after a successful Stage 4 export by reading the repository `operator audit-context`, asking the active Pi model for the harness judgment, and persisting the verdict through `operator audit-submit`. Pi's publish adapter runs the same audit before invoking `operator publish --confirm-public`; it must not use the headless `operator audit-run` path while `PI_SESSION_ID` is active.

### Non-Pi / cross-harness usage

Use the repository-local entrypoints instead of Pi slash commands:

```bash
scripts/rvv-miniputt ...
python3 -m tournament_scheduler.cli.rvv_cli ...
```

Human-friendly operation is exposed through `make help` and the Makefile.

Harness adapters may add UI/browser/progress integration but must not redefine shared pipeline policy. For supported non-Pi command workflows, load the corresponding `.agents/commands/rvv-miniputt/<command>.md` procedure instead of duplicating it in the harness directory.

A plain terminal/CI session cannot drive a browser for `scrape-llm --club <name>`. When that source needs LLM-guided recovery and no browser-enabled harness is available, use `scripts/rvv-miniputt recovery-targets` to list blocked sources, recover the events out-of-band, then `python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source "<name>"` (or `scripts/rvv-miniputt scrape-merge` to rebuild the Stage 2 checkpoint from recovered cache data) to rehydrate the cache through the same validation/merge path as any other source.

### Pi-only boundary

The following remain Pi-specific and have no cross-harness equivalent:

- `/rvv-miniputt ...` slash-command dispatch itself;
- `rvv_miniputt_*` agent-callable tool registration;
- `/rvv-miniputt guide` interactive wizard UX;
- live Pi notifications/status updates during a run.

## Normal operation

For a human/operator goal-oriented run:

```bash
make operator-run
make status
make logs
```

For checkpoint-reviewed agent operation:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

Do not call individual `stageN_*` modules when doing so bypasses the normal checkpoint/decision/verification path.

## Inputs

The four-stage season planner uses:

- root `input.xlsx` as the controlled planner workbook;
- reviewed registration exports only through the controlled import path when `Lag` needs rebuilding;
- external calendar/source evidence collected in Stage 2;
- local browser/session access only when a configured source requires interactive recovery.

`Årshjul for aktiviteter.xlsx` and the public registered-team CSV workflow are related repository workflows but are not Stage 1–4 planner policy inputs.

See `docs/rvv-miniputt-input-formats.md` for the workbook contract.

## Four-stage pipeline

### Stage 1 — configuration

Repository code validates and normalizes the controlled workbook.

Agent policy:

- do not invent missing teams, clubs, age groups or settings;
- treat invalid/reversed season windows and invalid identities as input problems to fix, not as soft preferences;
- when Stage 1 returns an explicit decision context, choose only from its available actions.

### Stage 2 — source/calendar evidence

Repository code owns extraction results, cache/provenance, source status and validation.

Agent policy:

- inspect blocked, empty and suspiciously sparse sources before trusting the plan;
- prefer a bounded recovery/retry action when the context exposes one;
- browser-assisted recovery is investigation/extraction only—the recovered result must return through repository validation/merge before it is trusted;
- do not declare a source healthy merely because a request technically succeeded.

Useful commands:

```bash
make sources-status
make calendars
scripts/rvv-miniputt scrape --club <name>
scripts/rvv-miniputt scrape-llm --club <name>
scripts/rvv-miniputt recovery-targets
```

### Stage 3 — planning

Repository code owns:

- normalized planning problem;
- hard constraints;
- candidate schema;
- solver/search primitives;
- deterministic candidate verification;
- reproducible quality metrics.

Agent policy:

- never accept a candidate with hard verification failures;
- use available optimize/refine/apply/keep/request actions rather than hand-editing the complete season plan in prose;
- compare candidates using the returned metrics/findings, not intuition alone;
- prefer Pareto/multi-objective evidence when several valid trade-offs exist instead of pretending one global score is absolute policy;
- do not turn a one-run preference into a new hard rule. If RVV wants a preference to become mandatory, implement/test it explicitly in deterministic code/configuration.

Typical soft dimensions include participation balance, hosting distribution, temporal spacing, opponent diversity/repetition, travel and source uncertainty. The repository measures them; the agent decides contextual priority only when no hard rule decides the outcome.

Once a season has been promoted to canonical state (`season/<season>/`), planning is baseline-aware by default — a normal Stage 3 run resolves the canonical files for its planning window (independent of `.pipeline`) and adopts the published schedule as its baseline, preserving durable IDs and placements instead of regenerating a season. Do not ask for a from-scratch season or hand-edit canonical files to work around a lock:

- approved/placement-locked tournaments are hard-preserve constraints — a candidate that moves or drops one fails verification with `canonical_placement_locked`/`canonical_participants_locked`/`canonical_locked_tournament_missing`, and the search will not even propose moving one;
- unapproved tournaments stay optimizable, but weighted change cost is folded into the search objective, so prefer the smallest change that resolves the problem;
- use `season replan` to search around the canonical baseline, `season diff` to see the weighted change cost before committing, and `season apply` to persist a verified candidate atomically. A rejected candidate must leave `schedule.json`/`decisions.json` unchanged;
- approval/lock state lives in `decisions.json`, separate from schedule facts, and an approval whose facts changed loses its approval instead of silently staying "approved".

### Stage 4 — export/review

Stage 4 re-verifies the selected candidate before serialization.

Agent policy:

- hard verification failure blocks export/publication;
- review manual arena/hosting/calendar follow-up separately from plan-quality warnings;
- generated output is derived data: correct input/config/code and regenerate rather than permanently patching HTML/CSV/Excel/iCal;
- use the Stage 4 `output_files` map to know what the run actually produced;
- after export, before publication, a harness-led semantic safety-net audit must run and
  produce PASS/REVIEW_REQUIRED/FAIL against the operator checklist below; publication is
  gated on it (see "Semantic safety-net audit" and "Publication").

Common outputs include the season-plan HTML/report, optional manual follow-up view, calendar/input views, Excel/CSV/iCal downloads, Spond workbooks and per-club review packets.

## Semantic safety-net audit (post-export, pre-publication)

This is the canonical policy source for the harness-led semantic safety-net audit — the independent, adversarial review an agent or the headless judge (`tournament_scheduler.llm_judge.audit`) performs after every Stage 4 export and before publication. Interactive harnesses and the headless path must use this checklist rather than defining their own criteria; harness adapters must not redefine or duplicate it.

Deterministic verification can only catch defects already encoded as rules. This audit's purpose is to catch the ones that aren't: simulate a careful human review of the exported season plan, assume the scheduler and its deterministic verifier may share a logic defect, or may simply be missing a rule, and look for inconsistent output, suspicious operational patterns, likely planner/export bugs, and concrete candidates for new planner rules. Do not conclude the schedule is correct merely because deterministic verification passed. Reconstruct important facts from the export, cross-check outputs against each other, inspect patterns across the whole season, look for counterexamples and suspicious outliers, and explain anything that does not make operational sense.

Operator checklist (answer every item; item 9 is open-ended and the most important):

1. Antall cuper pr lag?
2. Antall hjemmeturneringer pr lag?
3. Lengde på turneringer?
4. Er det faktisk ledig tid på is?
5. Deltar vertsklubben i samme turnering?
6. Deltar hvert lag maksimalt én gang per dag?
7. Er det normalt maks 2 lag fra samme klubb, med 3 kun som synlig unntak?
8. Er eksportformatene konsistente?
9. Ser harnesset andre materielle problemer eller manglende regler vi ikke allerede har tenkt på?

Execution model: when an interactive harness (Claude Code/Pi) is driving the run, the harness itself performs this audit in-session by reading `operator audit-context` output and reasoning adversarially against the checklist and evidence, then submitting its verdict via `operator audit-submit`. Pi's project adapter performs this automatically after each successful `/rvv-miniputt run` export and before `/rvv-miniputt publish`. When no interactive harness is active (`RVV_HARNESS`, `CLAUDE_CODE_SESSION_ID`, and `PI_SESSION_ID` all unset), the headless path (`operator audit-run --backend <name>`, cron/CI) calls a real LLM judge backend automatically instead.

Non-goals: this audit must not reimplement deterministic rule logic, must not become a second Python rules engine, must not duplicate SeasonPlanner policy, and must not perform fresh live calendar scraping — it cross-checks the *persisted* evidence bundle and source summary only.

A harness `FAIL` or `REVIEW_REQUIRED` can occur even when deterministic checks all pass — that is the intended purpose of the audit. It can never override a deterministic hard `FAIL`. An incomplete or failed audit run is never equivalent to `PASS`.

## Stage gating policy (soft judgment)

This is the canonical soft-policy source for the proceed/abort decision an agent or the headless judge (`tournament_scheduler.llm_judge`) makes after each stage (see ADR 0002 — `docs/adr/0002-llm-directed-decision-ownership-and-thin-adapters.md`). Interactive harnesses and the headless judge path must use this policy rather than defining their own criteria. A hard violation in the `DecisionContext` always blocks `proceed`, regardless of this policy.

### Stage 1 — Configuration

- `proceed` when at least one calendar source is configured and the date range is a realistic hockey season window;
- `abort` when no sources are configured, or the date range is clearly wrong (e.g. zero-length, reversed, or outside a plausible season).

### Stage 2 — Scraping

- `proceed` when most configured sources were scraped successfully;
- `abort` when so many sources are blocked or empty that planning would be meaningless — as a starting heuristic, fewer than half the sources have usable data. Prefer `recover_source`/`retry_stage` over an outright `abort` when a blocked source looks recoverable before concluding the run cannot continue.

### Stage 3 — Planning

- `proceed` when the draft plan contains at least a handful of tournaments covering the configured clubs/age groups;
- `abort` when the plan is empty or clearly wrong (e.g. zero tournaments planned despite configured sources/registrations) — that usually indicates a configuration or upstream data error, not a planning-quality judgment call.

Planning-quality tradeoffs (which warning to address first, whether a small regression is worth a larger gain, whether to keep the baseline) are the agent's soft judgment to make once past this proceed/abort gate. Do not encode a new fixed threshold or magic weight here to answer one of those tradeoffs; expose the underlying facts/metrics instead.

## Structured decision protocol

`run --interactive` returns a `DecisionContext` with facts, hard violations, warnings, metrics (when relevant), available actions and action argument/template information.

For each pause:

1. read the current context;
2. if a hard violation exists, do not bypass it;
3. choose exactly one returned available action;
4. use only the action's declared argument shape;
5. submit a concise operational rationale;
6. run the next canonical command and reassess the new context.

Do not persist or request hidden/private reasoning. The durable record only needs the action, relevant facts/outcome and concise rationale.

## Operator waivers for hard planning rules

A hard planning rule has two distinct meanings that must not be conflated:

- **structural invariants** (unknown team ids, corrupt/inconsistent
  serialization, invalid tournament identity, malformed data) are never
  waivable by anyone, including an operator;
- **hard planning rules** (for example a participation maximum) may never be
  crossed autonomously by the planner/optimizer/agent, but an authorized
  operator may explicitly waive one for a precise scope.

The planner/optimizer/agent may *suggest* an operator waiver, but may never
create, broaden or silently infer one: there is no decision action that does
this, and no agent path writes the waiver store. Only an explicit operator
action authorizes an exception. Without a matching active waiver, hard
verification behavior is unchanged.

Canonical operator capability (same CLI from every harness):

```bash
scripts/rvv-miniputt waiver list [--all]
scripts/rvv-miniputt waiver create --rule participation_target_exceeded \
  --club "Frisk Asker" --team "Frisk Asker 4" --age-group U11 \
  --tournament e7276974 --half before_christmas \
  --allowed-value 6 --reason "operator chose Frisk Asker 4 for host placement"
scripts/rvv-miniputt waiver revoke <waiver-id> --reason "withdrawn"
```

Rules:

- a waiver is tied to its exact scope (rule, team identity, half, tournament,
  configured target, accepted actual); if the team/tournament/date/value
  changes outside that scope it stops matching and the normal hard failure
  returns -- never re-create it silently;
- an operator-waived violation is reported as waived (not silently dropped)
  and downgrades publication readiness to `REVIEW_REQUIRED`, so a plan that
  only passes because of an exception is never indistinguishable from a
  clean pass;
- do not use `--non-strict` or a global participation-cap change as a
  substitute for an operator waiver;
- only explicitly classified waivable rules are accepted. Structural
  invariants remain blocking for everyone.

When the plan currently violates a hard rule with no matching waiver, use the
normal `request_operator` action to ask the operator whether to author one;
do not hand-edit checkpoints.

## Human escalation

Escalate when the repository explicitly requires human authority or information, for example:

- a real policy exception/change;
- an interactive access step that cannot be completed by the active environment;
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

Planning/export does not imply publication.

Use:

```bash
make audit-context
make audit-run BACKEND=<claude|openai|llm_bridge>
make audit-submit RESULT_FILE=<path>
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

`make publish` refuses without a fresh `PASS` (or an operator-approved `REVIEW_REQUIRED`) semantic audit result for the current export — see "Semantic safety-net audit" above.

Publication creates a separate allowlisted public bundle. Review packets and Spond exports are private/review artifacts by default and should not be assumed public.

Rollback is also explicit:

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

Their publishing variants use the same explicit Pages publication machinery but are not Stage 1–4 planner stages.

## Documentation ownership

Use these rather than creating new overlapping notes:

- `README.md` — what the system does, inputs/outputs, normal operation;
- `docs/system-architecture.md` — current end-to-end boundaries;
- `docs/rvv-miniputt-pipeline.md` — Stage 1–4 workflow;
- `docs/rvv-miniputt-input-formats.md` — workbook/input contract;
- `docs/adr/` — durable architectural rationale;
- GitHub issues — unfinished implementation work.
