# RVV Miniputt: publish

Publish the most recent successful Stage 4 export already on disk. This procedure does **not** run or rerun the planning pipeline.

Read the publication and semantic-audit sections in `.agents/skills/rvv/SKILL.md` first. They are the canonical policy; this file only describes the shared command procedure.

## Preconditions

- `.pipeline/stage4_export.json` must reference a usable export in its `data.output_files` map;
- `data.errors` must be empty;
- the current export must satisfy the repository's semantic-audit gate (`PASS`, or an explicitly operator-approved `REVIEW_REQUIRED` where the repository permits it);
- the current export must satisfy the XLSX/HTML artifact-parity + canonical freshness preflight (`export_parity.json` status `PASS`). A `FAIL` or `NOT_CHECKABLE` pair is refused; regenerate the projection from current canonical state instead of publishing it. Inspect an existing pair read-only with `scripts/rvv-miniputt export-parity --export-dir <dir> [--json]`;
- hard verification and public-bundle sanitization remain mandatory;
- when a promoted canonical season exists, the export being audited/published must represent the intended current canonical season revision/decision state. If `season move`, `season apply`, `season approve`, `season unapprove` or another canonical mutation happened after the current export, regenerate with `scripts/rvv-miniputt season export --season <season>` and audit that fresh projection before publishing.

Do not treat outer checkpoint staleness by itself as proof that the referenced Stage 4 export is unusable; the publication command resolves and validates the persisted export through repository code. Conversely, do not knowingly publish an older derived projection after canonical season/decision state changed merely because an old Stage 4 checkpoint still exists.

If no usable current Stage 4 export exists, stop and tell the user to generate it through the canonical `run` or `season export` workflow. Do not silently generate a new plan as part of publish.

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
- the canonical season/revision represented by that export when applicable;
- the published URL/result returned by the repository;
- relevant warnings that remain non-blocking.

## Rollback

Use the repository rollback/history commands documented in the shared RVV skill. Confirm the target published run with the user before performing a rollback because it is another public-facing change.
