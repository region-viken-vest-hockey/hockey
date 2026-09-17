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

## Inspect state

```bash
scripts/rvv-miniputt season status --season <season>
scripts/rvv-miniputt season approvals --season <season>
```

Treat `season/<season>/schedule.json` and `decisions.json` as the current operational truth. `.pipeline` remains transient run/search/evidence state.

## Approve / lock booked ice

When the operator says a tournament placement is confirmed/booked, use:

```bash
scripts/rvv-miniputt season approve \
  --season <season> \
  --tournament-id <durable-id> \
  --note "<concise operator note>"
```

Approval is never a hard-rule waiver. The repository re-verifies the current placement before approval. Approved/placement-locked tournaments are hard-preserve constraints for later planning.

If an approval is reported as `stale_approval`, do not silently keep or recreate it. Inspect why the protected fields changed and require explicit reapproval after the intended current state is confirmed.

## Change an approved tournament

Do not bypass approval protection. First revoke the approval/locks explicitly:

```bash
scripts/rvv-miniputt season unapprove \
  --season <season> \
  --tournament-id <durable-id> \
  --note "<reason>"
```

Then apply the requested canonical move:

```bash
scripts/rvv-miniputt season move \
  --season <season> \
  --tournament-id <durable-id> \
  [--date YYYY-MM-DD] \
  [--arena "..."] \
  [--host-club "..."] \
  [--start-time HH:MM]
```

Do not alter participants or unrelated tournaments to make a targeted move fit unless the operator instead asked for replanning. A rejected move must leave canonical state unchanged.

Reapprove only when the new placement is actually confirmed/booked.

## Repair a localized finding against the promoted season

Do not re-run Stage 1–4 merely to reach the repair capabilities when the canonical season already has a valid factual baseline. Ask the repository what is actually wrong on the current revision:

```bash
scripts/rvv-miniputt season findings --season <season>
```

Findings are fresh, revision-bound facts (recomputed from the canonical plan, never the snapshot promoted with the season). They include unresolved hosting obligations, hosting-balance deficits, manual placements, hard violations and participation strong-goal deviations with their `avoidability`. Findings are independent: address whichever one the operator cares about next; there is no repository-imposed queue.

For one selected finding, enumerate the repository's verified alternatives (direct/cheap options first, then coupled options):

```bash
scripts/rvv-miniputt season repair-options --season <season> --finding <finding-id>
```

When the cheap options are insufficient, request a bounded, finding-directed search that preserves unrelated tournaments and approvals:

```bash
scripts/rvv-miniputt season search --season <season> --finding <finding-id>
```

Apply exactly one verified option from the current canonical revision:

```bash
scripts/rvv-miniputt season apply-repair --season <season> \
  --option-id <id> \
  --expected-revision <revision> \
  [--finding <finding-id>]
```

`--expected-revision` is the `revision` reported by `season findings`/`repair-options`. An apply against a changed revision is rejected as stale and leaves canonical state byte-unchanged. A successful apply is atomic, full-season verified, returns the new revision and a deterministic before/after delta, and re-derives findings from the new revision. Hosting responsibility stays authoritative: a repair may draw down a deficit only from a surplus, never by relocating the shortfall or transferring burden to a club that does not owe it. A `bounded_search_exhausted` participation deviation is never proof of infeasibility.

## Replan around the published baseline

Whole-season replanning is an escalation, not the default response to every localized defect. For broader unresolved/quality problems, keep approved/locked commitments fixed and search around the current canonical schedule:

```bash
scripts/rvv-miniputt season replan --season <season> --iterations <n>
```

Inspect the resulting candidate/change cost before applying it:

```bash
scripts/rvv-miniputt season diff --season <season> --candidate <candidate.json>
```

Prefer the smallest hard-valid change that resolves the problem. Published-but-unapproved tournaments are mutable but carry change cost; approved/locked fields are not negotiable.

Persist only through the verified apply boundary:

```bash
scripts/rvv-miniputt season apply --season <season> --candidate <candidate.json>
```

Never hand-edit canonical JSON or select a candidate that fails hard verification.

## Export after canonical changes

When canonical schedule **or decision state** changes and the result is meant for review/publication, regenerate the derived export from canonical state:

```bash
scripts/rvv-miniputt season export --season <season>
```

Then run the semantic safety-net audit from `.agents/skills/rvv/SKILL.md` and only afterwards use the shared `publish.md` procedure.

Do not knowingly audit or publish an older Stage 4 projection after the canonical season/approval state has changed. The export carries the canonical season revision/fingerprint; treat a mismatched or stale projection as requiring `season export`, not as permission to publish anyway.

## Normal promoted-season flow

```text
canonical season
  -> season findings (fresh, revision-bound)
  -> season repair-options / season search for ONE selected finding
  -> season apply-repair (atomic, revision-bound) when a local fix is enough
  -> OR approve/unapprove, targeted season move, or baseline-aware season replan
  -> diff + verified apply when replanning
  -> season export
  -> semantic audit
  -> publish
```

Use the ordinary `run --interactive` procedure again only when the user's goal genuinely requires a full pipeline run (for example refreshed upstream input/calendar evidence). Even then, Stage 3 adopts the canonical baseline for the matching promoted season rather than regenerating from scratch.
