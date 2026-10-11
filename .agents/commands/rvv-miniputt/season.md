# RVV Miniputt: season

Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` before changing canonical season state.

Use this procedure once a verified schedule is becoming or has become the durable operational baseline for club review/ice booking. Repository code owns validation, locks, change cost, verification, persistence and export. The harness chooses the operator-intended action and reports the result; it must not hand-edit `season/<season>/schedule.json`, `decisions.json`, checkpoints or generated exports.

## Lifecycle boundary

Before promotion, create/review the season through the canonical interactive Stage 1–4 flow in `run.md`.

When the operator deliberately accepts that verified schedule as the operational baseline, promote it once:

```bash
scripts/rvv-miniputt season promote --work-dir .pipeline --season <season>
```

Promotion is bound to the reviewed Stage 4 handoff. It verifies the exact reviewed candidate against the verification context that accepted that Stage 4 export, then records the source run/fingerprint and context provenance in `promoted_from`. It refuses with an explicit stale/missing-provenance error if the Stage 4 export is incomplete/stale, the Stage 3 candidate no longer matches the reviewed export, the source run has changed, or the verification context is missing. It never silently rebuilds the problem from newer Stage 1/2 state or falls back to context-free verification.

Promotion is deliberate and exclusive. Do not use `--force` merely because a canonical season already exists; a normal later Stage 3 run is baseline-aware and must optimize around that canonical state instead of replacing it. `--force` only replaces existing canonical state; it is not a verification bypass.

After promotion, prefer the canonical season commands for normal club feedback and stabilization work.

## Published-season lifecycle

Publication is what makes the season operational. The **first successful publication seals the season automatically**; a season published before this feature existed is backfilled explicitly:

```bash
scripts/rvv-miniputt season lifecycle --season <season> --json
scripts/rvv-miniputt season seal-published --season <season>
```

`season seal-published` locates the real published baseline through authoritative publication history (never a supplied export directory), records the immutable baseline, derives publication omissions from the canonical plan at the publication revision, and requires explicit provenance for any post-publication materialization it must accept (`--attest-materialization <id>=<provenance>`). It fails closed if `published baseline + attested additions + recorded canonical mutations` does not exactly equal the current canonical projection. Do not work around a failure by snapshotting current state.

Once `published_sealed`, the season is maintenance-only. `season replan`, planner-generated `season apply`, mutating `season normalize-placements`, `season promote --force`, and a full/new pipeline run for the same window are refused by the service layer. Continue through the targeted canonical operations below. If a deliberate full restructuring is ever required, use the operator-only emergency escape hatch and record why:

```bash
scripts/rvv-miniputt season reopen-planning --season <season> \
  --reason "<operator reason>" --confirm-break-published-baseline
```

Never infer or auto-select reopening because a repair/replan path is convenient.

## Inspect state

```bash
scripts/rvv-miniputt season status --season <season>
scripts/rvv-miniputt season approvals --season <season>
scripts/rvv-miniputt season inspect tournament --season <season> --tournament-id <id>
scripts/rvv-miniputt season inspect constraints --season <season> --team "<club-or-label>"
scripts/rvv-miniputt season inspect constraints --season <season> --tournament-id <id>
scripts/rvv-miniputt season inspect candidates --season <season> --tournament-id <id>
scripts/rvv-miniputt season inspect candidates --season <season> --tournament-id <id> \
  --replace "<participant-label>" --legal-only
```

`season inspect` is the read-only domain interface for routine investigation: one tournament with its roster, approval/booking state, relevant request constraints and decision history; request constraints filtered by team/tournament/date; and registered same-age replacement candidates for a tournament. With `--replace`, candidate discovery also validates each candidate through the same replacement gates as `season replace-participant --dry-run` and returns an explicit per-candidate `verdict` (`safe_to_apply`/`blocked` plus `blockers`); use `--legal-only` to keep only candidates that pass. This is the supported way to find a legal replacement, so do not loop `replace-participant` as a probe. Every command supports `--json` for exact evidence, and none of them mutate canonical state.

A replacement dry-run/apply response carries a stable top-level `verdict` object (`status`, `applicable`, `checks`, `blockers`). Consume that domain verdict instead of re-deriving applicability from individual gate fields. Replacement and batch dry-runs are valid preview commands even when blocked: by default they exit 0 and report `verdict.applicable=false`; pass `--fail-on-blocked` when automation needs blocked previews to exit 3. Invalid CLI usage or non-preview apply refusal still exits non-zero and writes nothing.

A batch dry-run that produces material per-team consequences also carries `verdict.review` (`required`, the exact domain-level `consequences`, and a `token` identifying that reviewed candidate: baseline revision + candidate fingerprint + the **complete** material consequence set, independent of any `--accept-team-regression` already supplied). To apply the exact reviewed plan without reconstructing one `--accept-team-regression` per team/code, repeat the same batch with `--accept-reviewed-consequences <token> --accept-regression-reason "<reason>"`. The token must match the recomputed candidate; a stale/different plan, a combined `--accept-team-regression`, or a token supplied when nothing remains to accept is refused. This is explicit reviewed acceptance, not a blanket force: it waives only those reviewed consequences, and every other gate still applies.

Use these repository-owned projections (and the other documented `season`/`status`/`findings` commands) instead of parsing `season/<season>/schedule.json` or `decisions.json` directly with `python -c`, `jq`, shell pipelines or temporary scripts. Raw parsing bypasses domain resolution (approval staleness, derived constraint satisfaction, registered-team validation) and duplicates it outside tests. If an operational question has no supported projection, treat it as a tooling gap: report it and add the projection at its canonical owner with tests rather than scripting around it. Generic developer debugging on non-operational artifacts is not a substitute for an operational answer.

Treat `season/<season>/schedule.json` and `decisions.json` as the current operational truth. `.pipeline` remains transient run/search/evidence state.

## Operation routes

Load only the focused procedure for the operation at hand. Each file owns its
commands; this router owns the lifecycle boundary and inspection path above.

- Club/operator change requests and supersession -> `season/change-requests.md`
- Semantic request constraints and coupled violation repair -> `season/request-constraints.md`
- Approval, locks, manual booking and host-confirmed ice -> `season/approvals-and-booking.md`
- Changing an approved tournament, replacing one participant, or retiring a team -> `season/tournament-changes.md`
- Removing a participant with no replacement or swapping participants -> `season/participant-changes.md`
- Publication blockers and localized findings repair -> `season/blockers-and-repair.md`
- Replanning around the published baseline, export and the normal promoted flow -> `season/replan-and-export.md`
