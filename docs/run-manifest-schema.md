# AI operator run manifest schema

This document specifies the versioned run manifest and capability-result
contract introduced to satisfy the first item of the
[AI operator implementation roadmap](ai-operator-roadmap.md): a shared data
contract an agent can inspect to understand workspace state, capability
outcomes, evidence, confidence, artifacts, blockers, and suggested next
actions without parsing human-oriented console logs.

It complements, and does not replace, the existing per-stage checkpoint
files documented in `tournament_scheduler/pipeline/state.py`
(`.pipeline/stage1_config.json` … `stage4_export.json`). Those remain the
source of truth for stage data; the run manifest is a higher-level,
operator-facing summary layered on top.

## Where it lives

```
<work_dir>/run_manifest.json      # default work_dir is .pipeline
```

Written and read via `tournament_scheduler.pipeline.run_manifest.RunManifest`.

## Capability result contract

Every capability (a pipeline stage, a recovery action, a future source-health
check, etc.) can report a structured outcome via
`tournament_scheduler.pipeline.capability_result.CapabilityResult`:

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

Fields:

| Field                | Type                | Meaning                                                                 |
|-----------------------|--------------------|---------------------------------------------------------------------------|
| `schema_version`      | int                | Capability-result schema version (see [Versioning](#versioning)).         |
| `capability`           | string             | Name of the capability that produced this result (e.g. `"config"`).       |
| `status`               | string             | One of `ok`, `warning`, `blocked`, `failed`.                              |
| `summary`              | string             | One or two sentence human-readable description of what happened.         |
| `evidence`             | array              | Concrete facts backing the summary.                                       |
| `confidence`           | float (0.0–1.0)    | How much the result should be trusted. Informational only.                |
| `artifacts`            | array of strings   | Paths/identifiers of files or checkpoints produced.                       |
| `problems`             | array of strings   | Concrete issues encountered, even on `ok`.                                |
| `suggested_actions`    | array of strings   | Concrete next steps for a human or agent.                                 |
| `requires_human`       | bool               | True when this result cannot be resolved without human judgment.          |

`status` values map onto the escalation model described in
[`docs/ai-operator-product-direction.md`](ai-operator-product-direction.md):

- `ok` — completed as expected.
- `warning` — completed, but with something worth surfacing (a blocked
  source, a rough plan verdict, etc).
- `blocked` — cannot proceed without a human decision (`requires_human` is
  forced `True` by `CapabilityResult.blocked(...)`).
- `failed` — the capability did not complete.

## Run manifest contract

```json
{
  "schema_version": 1,
  "run_id": "20260726T091500Z-3f2a9c1d",
  "objective": "Produce the best trustworthy season plan from the current workbook.",
  "active_capability": null,
  "last_completed_capability": "export",
  "next_recommended_capability": null,
  "current_capability": "export",
  "input_fingerprint": {
    "path": "/path/to/input.xlsx",
    "sha256": "…"
  },
  "started_at": "2026-07-26T09:15:00+00:00",
  "updated_at": "2026-07-26T09:18:42+00:00",
  "ended_at": "2026-07-26T09:18:42+00:00",
  "final_outcome": "ok",
  "capabilities": [
    { "...": "one CapabilityResult entry per recorded capability, in order, each with a recorded_at timestamp" }
  ],
  "pending_questions": [
    { "...": "one Question entry per escalation raised in this workspace — see below" }
  ],
  "action_log": [
    { "...": "one entry per observe-decide-act loop step (issue #11) — see below" }
  ],
  "timing": {
    "stage1_seconds": 3.1,
    "stage2_seconds": 12.4,
    "stage3_baseline_seconds": 4.8,
    "stage3_cp_sat_seconds": 9.2,
    "stage3_decision_context_seconds": 0.3,
    "stage4_export_seconds": 1.9
  },
  "cp_sat_invocations": [
    { "...": "one entry per automatic-shadow or explicit-optimize CP-SAT call — see below" }
  ]
}
```

| Field                 | Meaning                                                                                   |
|------------------------|--------------------------------------------------------------------------------------------|
| `schema_version`       | Run-manifest schema version.                                                              |
| `run_id`                | Unique ID for this run (`<UTC timestamp>-<8 hex chars>`).                                  |
| `objective`             | The active objective, in the human's own words if provided, else a sensible default.       |
| `active_capability`     | Capability currently executing, or `null` if none is (issue #15). Set before a capability runs, cleared once it produces a result or the run finalizes — a finalized run always has `active_capability: null`, even after an interrupted or aborted run is read back. |
| `last_completed_capability` | Capability most recently recorded a result for, whatever the outcome (issue #15).       |
| `next_recommended_capability` | What should run next: the following stage in `config → scraping → planning → export` after a clean result, the same capability again after a `blocked`/`failed`/`requires_human` result, or `null` once `export` completes cleanly (issue #15). |
| `current_capability`    | Deprecated alias, mirrored from `active_capability` while a capability is running and from `last_completed_capability` once it finishes. Kept only for older consumers reading this exact key. |
| `input_fingerprint`     | Path + SHA-256 of the input workbook at run start.                                          |
| `started_at` / `updated_at` / `ended_at` | ISO-8601 UTC timestamps. `ended_at` is `null` until `finalize()` is called.  |
| `final_outcome`         | `in_progress` until finalized, then one of `ok`, `warning`, `blocked`, `failed`.            |
| `capabilities`          | Ordered history of every `CapabilityResult` recorded during the run.                        |
| `pending_questions`     | Escalation questions raised in this workspace, answered or not — see below.                 |
| `action_log`            | Ordered history of every operator-loop action taken in this workspace — see below.          |
| `timing`                | Accumulated wall-clock seconds per unit of work (issue #310) — see below.                    |
| `cp_sat_invocations`    | Ordered history of every automatic-shadow/explicit CP-SAT solve or cache reuse (issue #310) — see below. |

### Timing telemetry and CP-SAT invocation history (issue #310)

`timing` is populated by `RunManifest.record_timing(key, seconds)`, called
from each `_run_stageN`/Stage 3 optimize/decision-emit call site with
`perf_counter()`-measured elapsed time. Values accumulate additively across
the separate CLI invocations one interactive run makes (each
`rvv-miniputt run --interactive` invocation executes exactly one stage), so
a key like `stage3_cp_sat_seconds` reflects the running total across every
automatic shadow evaluation and explicit `optimize_plan(engine="cp_sat")`
pass in the run, not just the most recent one. Keys used:
`stage1_seconds`, `stage2_seconds`, `stage3_baseline_seconds`,
`stage3_local_search_seconds`, `stage3_cp_sat_seconds`,
`stage3_verification_seconds`, `stage3_decision_context_seconds`,
`stage4_export_seconds`, `refinement_seconds`. `stage3_total_seconds` and
`run_total_seconds` are not stored directly — they are derived on demand by
`run_manifest.timing_summary(manifest)`, summing the relevant keys, so they
can never drift out of sync with the components.

`cp_sat_invocations` is populated by `RunManifest.record_cp_sat_invocation(record)`
from `stage3_optimize_core._maybe_run_stage3_cp_sat_shadow` (automatic
shadow) and `stage3_optimize_variants._run_stage3_v2_optimize` (explicit
`optimize_plan(engine="cp_sat")`). Each entry carries at least `source`
(`"automatic_shadow"` or `"explicit_optimize_plan"`), `cache_hit` (bool —
whether this call was served from `stage3_cpsat_cache.json` instead of
re-solving), `status`, and `runtime_seconds`; `len(cp_sat_invocations)` is
the total invocation count the issue's acceptance criteria ask for — a
cache hit still appends an entry (with `runtime_seconds` near zero) so the
count reflects every call, not just every solve.

`rvv-miniputt run` starts a manifest at the beginning of every run, records a
capability result after each of the four pipeline stages, and finalizes the
manifest with the run's terminal outcome. Manifest writes are best-effort:
a failure to write the manifest never aborts the pipeline itself.

`start_run()` resets `capabilities` and the run's timestamps/outcome for the
new run, but **carries `pending_questions` forward unchanged** from any
existing manifest. Escalation questions and their human answers are durable
workspace state, not per-run state — a human answers a question in a
separate CLI invocation from the one that raised it, so that answer has to
survive the next `start_run()` call or the escalation protocol below
couldn't work at all.

## Human escalation and approval protocol

Defined in `tournament_scheduler/pipeline/escalation.py`. Any capability that
returns a `CapabilityResult` with `requires_human: true` can be turned into a
`Question` and raised into `pending_questions`:

```json
{
  "id": "abbee2e6f80e4678",
  "type": "credentials",
  "capability": "scraping",
  "summary": "Kongsberg ishall blocked",
  "context": "event_count=0; strategy=browser",
  "alternatives": ["Set credentials via KONGSBERG_USER", "Try scrape-llm"],
  "recommendation": "Set credentials via KONGSBERG_USER",
  "impact": "Timeout loading the page",
  "scope": "workspace",
  "scope_key": "",
  "created_at": "2026-07-26T09:16:00+00:00",
  "answered": false,
  "answer": null,
  "decided_by": null,
  "decided_at": null,
  "stale": false,
  "stale_reason": null,
  "promoted_from": null,
  "promoted_at": null
}
```

`type` is one of six escalation types: `credentials`, `incomplete_data`,
`ambiguous_policy`, `destructive_repair`, `impossible_constraints`,
`external_publication`. A question's `id` is a stable hash of
`(type, capability, summary)` and, for anything narrower than `workspace`
scope, `(scope, scope_key)` too — see "Decision scoping" below. Raising the
*same* question in the *same* scope context again — from a retried or
resumed run — is a no-op rather than a duplicate, and raising a question
that was already answered leaves the recorded answer untouched instead of
reopening it.

`rvv-miniputt operator run` raises a question for every capability result
that came back `requires_human: true` after the pipeline finishes, and
prints any still-unanswered ones as part of its final summary. Inspect and
resolve them with:

```bash
rvv-miniputt operator questions [--json] [--all]
rvv-miniputt operator answer <question-id> "<answer>" [--decided-by NAME]
rvv-miniputt operator promote <question-id> <scope> [--scope-key KEY] [--decided-by NAME]
```

Answering records a durable, auditable decision — it does not itself change
pipeline state. Running `rvv-miniputt operator run` again afterwards picks
up from the earliest stale/pending capability as usual (see item 2); the
human's answer being on record just means the same question won't be raised
a second time **within the scope it was answered in** — see below.

### Decision scoping (issue #12)

A decision's `scope` controls how durable it is — whether an answer given in
one context (a run, a workbook, a season) should be silently reused in a
different one, or should become `stale` and force a fresh escalation. Four
scopes, narrowest to broadest:

| Scope           | `scope_key`                              | Reused when...                                  | Example                                                              |
|------------------|--------------------------------------------|--------------------------------------------------|------------------------------------------------------------------------|
| `run`            | the raising run's `run_id`                 | the exact same run raises it again               | "one fewer weekend this run — a rink is closed for maintenance"       |
| `input_version`  | the workbook's `input_fingerprint.sha256`  | the exact same workbook is used again            | "U12 has too few teams to fill its bracket" (specific to this file)   |
| `season`         | a caller-supplied season identifier        | the same season, across any run or workbook      | "skip the Christmas week for every tournament this season"            |
| `workspace`      | always `""` (ignored)                      | any context — this is the pre-#12 default        | "Kongsberg ishall always needs LLM-based scraping"                    |

A scope narrower than `workspace` bakes `(scope, scope_key)` into the
question's `id`, so a context change (a new run, a re-uploaded workbook, a
new season) naturally produces a *different* id — the old entry is never
silently reinterpreted as an answer for the new context. Instead, when the
new occurrence is raised, the older entry sharing the same `(type,
capability, summary, scope)` is marked `stale: true` with a `stale_reason`,
while its `answer` and `decided_by` stay on record — the audit trail is
never deleted, only superseded. `workspace`-scoped questions never go
stale this way, since their id doesn't vary with context at all — that's
the durable, "policy-level" tier, matching every question raised before
issue #12 existed.

`rvv-miniputt operator questions` defaults to unanswered questions only
(pre-#12 behavior); `--all` also lists answered and stale ones, so a human
can see the full history including what stopped applying and why.

**Promoting a decision** copies an answered question into a new entry under
a strictly broader scope (`run < input_version < season < workspace`),
without touching the original — useful when a decision that started as
"true for this run" turns out to actually be a standing policy:

```bash
rvv-miniputt operator promote <question-id> workspace
rvv-miniputt operator promote <question-id> season --scope-key 2026-2027
```

The source entry gains a `promoted_to` pointer to the new entry; the new
entry gets `promoted_from` pointing back. `season` scope has no generic key
to derive automatically (there's no single "season identifier" elsewhere in
the pipeline), so promoting to it — or raising a `season`-scoped question
directly — always requires an explicit `--scope-key`.

By default, `_raise_escalation_questions` (the scan that runs after every
`rvv-miniputt operator run`) scopes capability escalations to
`input_version` when the manifest has an input fingerprint, since a blocked
capability's cause is almost always a fact about *that* workbook's data —
falling back to `workspace` when no fingerprint is available. Escalations
raised directly by capability code (e.g. the observe-decide-act loop's
`request_credentials` action, issue #11) keep the `workspace` default,
since a missing credential is a standing fact about a source, not something
tied to one run or workbook.

## The observe-decide-act operator loop (issue #11)

`rvv-miniputt operator run` starts every invocation with a bounded recovery
pass over calendar source health, implemented in
`tournament_scheduler/pipeline/operator_loop.py`:

```
observe (source_health, issue #3)
    -> decide (a pure policy function, no LLM required)
        -> act (dispatch through the #10 OperatorAction registry)
            -> evaluate (re-observe)
                -> continue, retry, escalate, or finish
```

The policy (`decide_action_for_source`) is a plain, synchronous function —
no network or LLM call — so it runs and is fully unit-tested without any LLM
provider configured. An LLM harness may *propose* an action instead of the
default policy, but a proposal that isn't a known registry action ID is
rejected and the loop falls back to the deterministic policy: the harness can
influence *which* registered action runs, never invent a new one.

The loop is bounded on two axes so it can never run forever: at most
`max_retries_per_source` attempts per source (default 2) before escalating,
and at most `max_actions` actions in total across all sources (default 10).
It also detects "no progress" — an action that produced the same result as
the previous attempt on that source — and escalates immediately instead of
burning through the remaining retry budget. None of the loop's own
policy-selected actions require human approval; if a custom action proposer
names one that does, the loop escalates instead of executing it.

Every step is appended to `action_log` via `RunManifest.record_action_transition()`:

```json
{
  "target": "Jar",
  "action_id": "retry_source",
  "arguments": { "work_dir": "...", "source_name": "Jar" },
  "rationale": "status=blocked attempt=0",
  "policy_rule": "blocked+attempt_0->retry_source",
  "result": { "...": "the CapabilityResult the action produced" },
  "transition": "resolved",
  "recorded_at": "2026-07-26T09:16:05+00:00"
}
```

| Field         | Meaning                                                                                          |
|----------------|----------------------------------------------------------------------------------------------------|
| `target`       | What the action operated on — a source name.                                                      |
| `action_id` / `arguments` | The exact registered `OperatorAction` invoked (empty `action_id` when the loop escalated without acting). |
| `rationale`    | Free-text explanation of why this action was chosen.                                              |
| `policy_rule`  | Stable identifier for the deterministic rule that selected it, independent of prose.               |
| `result`       | The `CapabilityResult` the action produced.                                                       |
| `transition`   | What the loop did next: `resolved`, `retry`, `escalate`, or `no_progress_stop`.                    |

Because the loop reasons from on-disk state (the Stage 2 checkpoint and the
unified scrape cache) rather than in-process counters, it resumes safely
across separate `operator run` invocations — a source that was mid-retry
when the process was interrupted picks back up from its actual observed
health, not from a lost in-memory attempt count.

When the loop repairs at least one source (`actions_taken > 0`),
`_cmd_operator_run` forces the pipeline to resume from at least Stage 2 even
if auto-detection would otherwise have found "nothing pending" or a later
stage — only an actual Stage 2 rerun rewrites the checkpoint the rest of the
pipeline reads. An explicit `--resume-from` always wins over this
adjustment, and a failure inside the recovery pass itself degrades silently
to a normal run rather than crashing `operator run`.

## Inspecting a run

```bash
rvv-miniputt status --json
# or
python3 -m tournament_scheduler.cli.rvv_cli status --json
```

prints the current run manifest as JSON. The existing human-readable
`rvv-miniputt status` output is unchanged.

## Backward compatibility

Work directories populated before this schema existed have per-stage
checkpoint files but no `run_manifest.json`. `RunManifest.read()` handles
this transparently: when no manifest file exists, a manifest-shaped view is
synthesized from the legacy `stage*.json` checkpoints, with
`"synthesized_from_legacy_checkpoints": true` set so callers can tell the
difference. Every field the manifest normally promises is still populated —
derived from data that was already on disk — so callers do not need a
special code path for old work directories.

A manifest file that *does* exist but predates `active_capability` /
`last_completed_capability` / `next_recommended_capability` (issue #15) is
backfilled the same way on read, without discarding anything: `read()` fills
in the missing keys from the legacy `current_capability` and `capabilities`
history. Notably, `active_capability` is only backfilled from
`current_capability` while `final_outcome` is still `in_progress` — a
pre-#15 *finalized* manifest whose `current_capability` was never cleared
(exactly the bug #15 fixes) correctly gets `active_capability: null` once
read again, rather than perpetuating the stale value.

## Manifest persistence reliability (issue #14)

Manifest operations are still best-effort — a persistence problem never
aborts the scheduling pipeline itself — but a failure is never silently
invisible the way it used to be:

**Atomic writes.** `RunManifest._write()` writes to a temp file next to
`run_manifest.json` and swaps it into place with `os.replace()`, which is
atomic on the same filesystem. A write failure (permissions, a full disk)
raises `ManifestPersistenceError` (a `RuntimeError` subclass) and leaves the
previous valid manifest completely untouched — there is no window where a
half-written file could be read back.

**Corrupted-manifest recovery.** If `run_manifest.json` exists but fails to
parse (invalid JSON, or valid JSON that isn't an object), `read()` no longer
treats that identically to "no manifest ever existed": the corrupted file is
first copied aside to `run_manifest.json.corrupted-<id>` (best-effort, next
to the original), and the returned manifest — still synthesized from the
legacy checkpoints, so callers get a usable result either way — carries a
`manifest_recovery` field describing what happened:

```json
{
  "manifest_recovery": {
    "recovered": true,
    "reason": "invalid_json",
    "detail": "Expecting value: line 1 column 1 (char 0)",
    "backup_path": "/path/to/.pipeline/run_manifest.json.corrupted-20260726T121500Z-a1b2c3d4",
    "detected_at": "2026-07-26T12:15:00+00:00"
  }
}
```

`manifest_recovery` is `null` on every healthy read (present as a field
either way, for a stable shape). `rvv-miniputt status --json` surfaces this
automatically, since it just prints `RunManifest(work_dir).read()` — a
corrupted manifest produces a visible diagnostic there without any extra
plumbing. A corruption recovery also caps a would-be `ok` synthesized
outcome at `warning`, since a workspace that just lost its manifest is not
the same thing as a clean one.

**Operator-state health check.** `RunManifest.check_health()` (and the
module-level `is_durable(work_dir)` convenience wrapper) performs a real
read-then-write round trip and reports `{"healthy", "writable",
"manifest_recovery", "detail"}` without raising. Run it directly with:

```bash
rvv-miniputt operator health [--json]
```

**Visible warnings instead of silent swallowing.** The manifest wrapper
functions in `cli/pipeline_orchestrator.py` (`_manifest_start_run`,
`_manifest_record`, `_manifest_finalize`, `_raise_escalation_questions`)
used to catch every exception with a bare `pass`. They now call
`_warn_manifest_failure()`, which prints a visible `⚠` warning to the
console, appends a line to `<work_dir>/logs/manifest_warnings.log`, and
marks that work dir "degraded" for the rest of the process. `_manifest_finalize`
checks that marker and caps an `ok` outcome at `warning` — a scheduling run
that completed cleanly but couldn't reliably record having done so must
never look identical to a genuinely clean run.

**Approval-gated actions require durable persistence.** `ActionRegistry.execute()`
(issue #10) now checks `is_durable(work_dir)` before running an
*approved* action that itself `requires_approval` (destructive/external risk).
If the manifest isn't writable, it raises `PersistenceUnavailableError`
instead of executing — an approved destructive or external action must not
run without a reliable way to record that it happened.

## Versioning

`CAPABILITY_RESULT_SCHEMA_VERSION` and `RUN_MANIFEST_SCHEMA_VERSION`
(both currently `1`) are bumped only on a breaking change — removing or
renaming a field, or changing a field's meaning. Adding a new optional field
with a safe default is not a breaking change and does not require a bump.

`CapabilityResult.from_dict()` ignores unknown keys, so code built against an
older schema version can still read a manifest written by a newer one as
long as no field it depends on was removed or repurposed. A future breaking
change should be accompanied by an explicit migration path (e.g. a
`schema_version`-keyed reader) rather than assuming all readers upgrade in
lockstep.
