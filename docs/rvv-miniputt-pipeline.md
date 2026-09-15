# RVV Miniputt pipeline guide

## Purpose

The four-stage pipeline turns the controlled planner workbook plus external calendar evidence into a deterministically verified, reviewable season-plan bundle.

Normal human operation should use `make operator-run`. Agent/harness checkpoint review should use the canonical repository CLI rather than calling stage modules directly.

## Pipeline

| Stage | Input | Responsibility | Persistent result |
|---|---|---|---|
| 1 — Config | `input.xlsx` | Parse/validate teams, age groups, dates, sources and planning settings. | normalized config checkpoint |
| 2 — Scraping | configured sources + runtime credentials/session where needed | Collect availability evidence, cache provenance, identify blocked/empty/suspicious sources. | scraping/source checkpoint + cache |
| 3 — Planning | normalized config + trusted source evidence | Build/search/solve candidate plans; run hard verification and reproducible quality measurement. | selected plan/candidate checkpoint |
| 4 — Export | selected Stage 3 plan | Re-verify at serialization boundary and create the review/export bundle. | output file map + export fingerprint |

Checkpoints, logs, cache and run/decision state live under `.pipeline/` and are generated runtime state.

## Canonical input

Root `input.xlsx` is the controlled season-planning input. Reviewed registrations may be imported to rebuild only the `Lag` sheet; controlled planning/admin sheets remain unchanged.

See [`rvv-miniputt-input-formats.md`](rvv-miniputt-input-formats.md) for the workbook/interchange contract.

Registration import:

```bash
scripts/rvv-miniputt registrations validate registrations.csv --input input.xlsx
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx --dry-run
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx
```

Review the generated workbook before promoting it to root `input.xlsx`.

## Stage 1 — configuration

Stage 1 validates and normalizes the controlled workbook. It should fail early on invalid team identities, age-group configuration, season windows or other input that would make later planning unreliable.

The normalized checkpoint is the pipeline's working representation; it does not replace the workbook as the operator-maintained source.

## Stage 2 — calendar/source evidence

Use:

```bash
make sources-status
make calendars
make calendars-refresh
```

For focused troubleshooting:

```bash
scripts/rvv-miniputt scrape --club <name>
scripts/rvv-miniputt scrape-llm --club <name>
scripts/rvv-miniputt recovery-targets
```

Stage 2 owns deterministic source facts: extraction result, event counts/shape, provenance/cache status, and declared hard source gates.

A technically successful fetch is not automatically trustworthy. Suspiciously sparse/empty data and blocked sources must remain visible to the decision layer.

### Browser/session recovery

Some sources require browser control, credentials or MFA. A browser-enabled harness may investigate/recover the source, but recovered event data must return through the repository recovery/merge/validation path before Stage 2 treats it as usable.

Do not put credentials, cookies, session files or MFA artifacts in command text, committed files, logs, docs, or generated public output.

## Stage 3 — planning and quality

Stage 3 separates **combinatorial execution** from **policy judgment**:

- repository code owns the normalized planning problem, hard constraints, candidate contract, solver/search primitives, deterministic verification and quality metrics;
- the agent may choose among exposed valid search/refinement actions and contextual soft trade-offs;
- a human decides explicit policy exceptions/authority questions.

Solvers may include CP-SAT and other search/repair mechanisms. No solver implementation is itself the business-policy source. A candidate becomes acceptable only after deterministic verification.

When the complete Stage-1 registered pool for an age group is exactly the shared effective tournament shape's team count, membership is fixed: every unpinned tournament for that age group uses the full registered pool, roster-size planning cannot shrink it, and participant-subset scoring/CP-SAT membership optimization is bypassed. Tournament volume then follows the cohort's participation target; once the fixed cohort has reached its target, planning stops rather than creating a partial cohort tournament.

Before broad optimization or host-coverage repair consumes a Stage 3 baseline, repository code tries a targeted roster repair for any unpinned tournament whose materialized participant count is below the shared effective shape while the registered pool can supply that shape. The repair considers registered teams in the same exact age group, filters hard-ineligible candidates such as same-date duplicates, teams already at participation maximum, and hard club-cap breaches, ranks legal candidates by team-level participation deficit, adds the best local fill, regenerates games, and records added teams plus rejected-candidate reasons for review.

If independent verification reports `host_team_missing`, Stage 3 interactive review exposes deterministic local repair options before falling back to broader optimizer retries or operator questions. The option set can include participant replacement/addition using an eligible registered team from the physical host club and rehosting to a represented physical club with trusted arena/calendar evidence. The controller selects a repository-generated option id; repository code validates the candidate fingerprint, applies the mutation to a clone, regenerates derived games or host placement fields, reruns full verification, and commits only a hard-feasible result. Rejected local candidates remain visible with explicit reasons.

Club x age-group hosting coverage is checked independently for every age group: every club with a registered team should host at least once in each age group it fields a team in. Before a coverage gap is accepted as an unresolved hosting obligation, repository code first checks whether one of that club's own tournaments in the *same* age group — one it already participates in but does not host — can simply change host to that club (no participant swap needed, since the club is already represented). If not, it then checks whether that same physical club already has a surplus/duplicate hosting assignment in a *different* age group that could legally be repurposed for the missing one (same arena/date, a freshly rebuilt tournament with real eligible participants and a re-verified duration) — never by simply relabeling an existing tournament's age group. A repair is only applied when it resolves the gap without breaking any other hard constraint or creating a new one; otherwise the obligation stays unresolved, with the repair candidates repository code considered and rejected kept as evidence for manual follow-up and the semantic safety-net audit.

The durable ownership decision is in ADR 0002; Stage 3 optimization details are in ADR 0001.

## Structured agent decision flow

For checkpoint-reviewed agent operation:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

A pause returns a structured `DecisionContext` containing decision-relevant facts, hard violations, warnings, available actions, parameter schema and a decision-action template.

The controller must:

1. choose only an action returned in `available_actions`;
2. fill only the declared arguments/template;
3. provide a concise audit rationale, not private chain-of-thought;
4. submit the action through the same canonical command surface;
5. repeat until the pipeline advances, escalates or finishes.

Harness-specific `.claude`, `.chatgpt`, `.codex` and Pi files are transport/UI/browser adapters only. Shared RVV policy lives in repository code and `.agents/skills/rvv/SKILL.md`; shared non-Pi command procedures live under `.agents/commands/rvv-miniputt/`. Harness adapters should lazy-load those neutral files and add only harness-specific transport/UI behavior, never maintain independent copies.

## Goal-oriented human/operator flow

```bash
make operator-run
make status
make logs
```

The operator path resumes from durable state when possible. Force a full rerun only when necessary:

```bash
make operator-run-force
```

If a real human decision is required:

```bash
make questions
make answer ID=<id> ANSWER='<answer>'
make operator-run
```

## Stage 4 — review/export bundle

Stage 4 re-verifies the selected candidate immediately before serialization. Hard verification failure blocks export.

A normal timestamped `export/<timestamp>/` may contain:

- `season_plan.html` — primary season-plan view;
- `season_plan_report.html` — rules/quality/fairness diagnostics;
- `manual_schedule.html` — only when manual arena/hosting/calendar follow-up remains;
- `calendars.html` — collected calendar overview when available;
- `input.html` — public-safe registered-team overview from the workbook;
- `season_plan.xlsx`;
- `season_plan.csv` and `season_plan_overview.csv`;
- `season_plan.ics`;
- `season_plan_spond.xlsx` and `season_plan_spond_games.xlsx`;
- `review_packets/` — per-club review material;
- activity artifacts when the configured activity data is available.

The Stage 4 checkpoint's `output_files` map is the authoritative record of what that run actually produced.

Generated artifacts are derived data. Do not permanently patch them by hand; correct the source/config/code and regenerate.

## Review before publication

Review at least:

- hard verification status;
- manual placement/booking follow-up;
- participation, hosting and temporal distribution;
- opponent diversity/repetition and other reported quality metrics;
- source-health uncertainty;
- rules/report wording against actual planner/verifier behavior;
- the semantic safety-net audit result for the current export (`.pipeline/audit_result.json`);
- privacy/public-bundle findings.

## Publication

Generation and publication are separate operations.

```bash
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

Publication builds a separate allowlisted/privacy-checked public bundle and updates GitHub Pages only after explicit confirmation and a fresh semantic audit. In Pi, `/rvv-miniputt run` runs that harness audit automatically after Stage 4, and `/rvv-miniputt publish` runs it before calling the repository publish command. Review packets and Spond exports are not public by default.

For recovery:

```bash
make publish-history
make rollback RUN_ID=<id> CONFIRM_PUBLIC=1
```

WordPress should link/embed generated Pages output rather than maintain another generated schedule copy.

## Related non-pipeline workflows

The repository also publishes two related views that are not Stage 1–4 planning stages:

```bash
make aktivitetskalender
make registered-teams CSV=<reviewed-registration-export.csv>
```

Their `*-publish` variants stage a complete Pages snapshot and use the same explicit publication safety path.

## Documentation ownership

- root `README.md` — what the system does, inputs/outputs, normal operation;
- this file — Stage 1–4 behavior;
- `rvv-miniputt-input-formats.md` — input contract;
- `system-architecture.md` — end-to-end boundaries/sources of truth;
- ADRs — durable rationale;
- `.agents/skills/rvv/SKILL.md` — shared agent operating policy;
- `.agents/commands/rvv-miniputt/` — shared lazy-loaded non-Pi command procedures;
- GitHub issues — live implementation backlog.
