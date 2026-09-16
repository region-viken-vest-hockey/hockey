# RVV Miniputt: publish

Publish the most recent successful Stage 4 export already on disk. This procedure does **not** run or rerun the planning pipeline.

Read the publication and semantic-audit sections in `.agents/skills/rvv/SKILL.md` first. They are the canonical policy; this file only describes the shared command procedure.

## Preconditions

- `.pipeline/stage4_export.json` must reference a usable export in its `data.output_files` map;
- `data.errors` must be empty;
- the current export must satisfy the repository's semantic-audit gate (`PASS`, or an explicitly operator-approved `REVIEW_REQUIRED` where the repository permits it);
- hard verification and public-bundle sanitization remain mandatory.

Do not treat outer checkpoint staleness by itself as proof that the referenced Stage 4 export is unusable; the publication command resolves and validates the persisted export through repository code.

If no usable Stage 4 export exists, stop and tell the user to run the canonical planning flow first. Do not silently generate a new plan as part of publish.

## Publish

When the user has explicitly requested public publication, invoke:

```bash
python3 -m tournament_scheduler.cli.rvv_cli operator publish --confirm-public
```

`--confirm-public` confirms the public-publication action only. It must not bypass hard verification, audit requirements, sanitization, or reachability checks.

If publication fails because the semantic audit is missing/stale, use the canonical audit flow from the shared RVV skill (`operator audit-context` for the bounded overview, `operator audit-evidence --item/--tournament/--club/--category` to pull exact supporting detail, then harness judgment + `operator audit-submit`, or the documented headless `operator audit-run` path when appropriate) and retry only after the gate is satisfied.

On failure, report the repository error and any pending operator questions. Do not improvise around a blocking gate.

On success, report:

- which export/run was published;
- the published URL/result returned by the repository;
- relevant warnings that remain non-blocking.

## Rollback

Use the repository rollback/history commands documented in the shared RVV skill. Confirm the target published run with the user before performing a rollback because it is another public-facing change.
