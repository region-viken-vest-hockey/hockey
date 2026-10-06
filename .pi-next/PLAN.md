# Plan: HTML ↔ Spond artifact parity

**Goal:** Enforce independent byte-readback parity between review HTML and Spond import workbooks locally and on the published gh-pages bundle.

**GitHub-Issue:** 625

## Tasks

- [x] Add an independent Spond workbook reader and HTML ↔ Spond comparator.
  - Files: `tournament_scheduler/pipeline/export_parity/spond_reader.py`, `tournament_scheduler/pipeline/export_parity/comparator.py`, `tournament_scheduler/pipeline/export_parity/records.py`, `tournament_scheduler/pipeline/export_parity/verify.py`
  - Approach: Parse the written Spond workbook, group rows by stable tournament/team identity, retain duplicate-row diagnostics, compare active operational fields, and model cancellation exclusion explicitly.
- [x] Integrate Spond parity and revision metadata into export/publication readiness.
  - Files: `tournament_scheduler/spond/spond_exporter.py`, `tournament_scheduler/pipeline/stage4_export.py`, `tournament_scheduler/pipeline/export_parity/gate.py`
  - Approach: Embed export revision provenance in the workbook, require the artifact in canonical Stage 4 and publication gates, and fail closed on missing, unreadable, stale, or divergent bytes.
- [x] Add read-only published gh-pages verification and operator CLI reporting.
  - Files: `tournament_scheduler/pipeline/export_parity/published.py`, `tournament_scheduler/cli/args_export_parity.py`, `tournament_scheduler/cli/export_parity_command.py`
  - Approach: Resolve the authoritative branch commit, materialize only published artifact bytes into temporary storage, reuse the same verifier, and report artifact parity independently from canonical freshness/provenance without publishing or regenerating.
- [x] Add real-export and published-branch regression coverage and update focused docs.
  - Files: `tests/test_export_parity.py`, `tests/test_spond_export_parity.py`, `tests/test_published_export_parity.py`, `docs/rvv-miniputt-deployment-architecture.md`
  - Approach: Mutate generated XLSX bytes for placement, participants, duplicates, extras, stale exports, and cancellation cases; use a controlled git fixture to prove published bytes are read.
- [ ] Verify, commit, and open a PR.
  - Files: all changed implementation, tests, and documentation files
  - Approach: Run focused and broader quality gates, deterministic diff/safety review, evidence-backed issue verification, commit explicit paths, push the feature branch, and create a PR linked to issue 625.
- [ ] [Fix] Implement first-class team retirement capability for published-season maintenance
  - Files: tournament_scheduler/application/canonical_season/retirement.py, tournament_scheduler/application/canonical_season/__init__.py, tournament_scheduler/application/canonical_season_service.py, tournament_scheduler/cli/rvv_cli.py, .agents/commands/rvv-miniputt/season.md, tests/test_team_retirement.py
  - Approach: 1. Create new retirement.py module in canonical_season package with core retirement logic
2. Wire it into CanonicalSeasonService
4. Add CLI command for retire-team
5. Update agent documentation (season.md) to route retirement requests
6. Add regression test for Kongsberg/Tønsberg Ju12 shape

## Acceptance Criteria

- [ ] `season_plan_spond.xlsx` has an independent artifact reader in the parity/publication path.
- [ ] `season_plan.html` (+ cancellation companion where applicable) and `season_plan_spond.xlsx` are normalized to comparable operational projections from written bytes.
- [ ] Strict semantic parity is enforced for active tournaments, placement fields and participants.
- [ ] Missing, extra and duplicate Spond rows are detected.
- [ ] Intentional cancellation representation differences are explicit and tested.
- [ ] A stale Spond workbook cannot pass because HTML/canonical metadata is fresh.
- [ ] Missing/unparseable required artifacts fail closed.
- [ ] Structured diagnostics identify the exact tournament/participant/field mismatch.
- [ ] The parity gate is mandatory for review/publication readiness of canonical season exports.
- [ ] A read-only command can validate the **currently published `gh-pages` artifacts** using the same HTML ↔ Spond parity implementation.
- [ ] Published verification resolves authoritative publication/`gh-pages` state without requiring an operator-supplied export directory.
- [ ] Published verification reports artifact parity separately from canonical/publication freshness/provenance.
- [ ] Published verification never regenerates or republishes artifacts.
- [ ] Tests prove the published path reads published artifact bytes and detects divergence there.
- [ ] Real-export byte-readback regression tests cover representative divergence cases.
- [ ] Existing `season_plan.xlsx` ↔ HTML parity remains intact and preferably shares the same normalized parity architecture.

## Log





### 2026-10-06 — Add real-export and published-branch regression coverage and update focused docs.
**Done:** Added real exporter byte-corruption regressions for placement, participant, row multiplicity, tournament presence, revision staleness, cancellation mapping, artifact readability, and controlled published-branch divergence; documented the operator command.
**Rationale:** Tests mutate emitted XLSX bytes after export and a committed gh-pages fixture, preventing shared in-memory exporter inputs from masking errors.
**Findings:** Focused suite passed 150 tests; canonical make check passed 3733 fast tests plus all operator, reproducibility, CLI, rules, architecture, and secret-scan gates.
**Files:** tests/test_export_parity.py, tests/test_spond_exporter.py, docs/rvv-miniputt-deployment-architecture.md
**Commit:** not committed
### 2026-10-06 — Add read-only published gh-pages verification and operator CLI reporting.
**Done:** Added authoritative remote gh-pages commit resolution, byte materialization in temporary storage, shared parity verification, and separate artifact-parity versus canonical-freshness reporting through the existing export-parity CLI.
**Rationale:** The path reads exact latest/ blobs at the remote branch SHA and never regenerates, checks out, commits, pushes, or republishes artifacts.
**Findings:** A live read-only diagnostic resolved origin/gh-pages at 201d2996c55c6d828397e0b1847f75b7a27968d9 and reported artifact parity PASS separately from canonical freshness STALE, demonstrating the intended independent dimensions.
**Files:** tournament_scheduler/pipeline/export_parity/published.py, tournament_scheduler/pipeline/export_parity/__init__.py, tournament_scheduler/cli/args_export_parity.py, tournament_scheduler/cli/export_parity_command.py
**Commit:** not committed
### 2026-10-06 — Integrate Spond parity and revision metadata into export/publication readiness.
**Done:** Embedded canonical revision metadata in generated Spond workbooks and made Stage 4 plus canonical publication preflight require Spond parity.
**Rationale:** Canonical exports now fail closed for missing, unreadable, stale, duplicate, or semantically divergent Spond bytes before review/publication; sanitized public bundles are rechecked through the same gate.
**Findings:** none
**Files:** tournament_scheduler/spond/spond_exporter.py, tournament_scheduler/pipeline/stage4_export.py, tournament_scheduler/pipeline/export_parity/gate.py, tournament_scheduler/pipeline/export_parity/verify.py
**Commit:** not committed
### 2026-10-06 — Add an independent Spond workbook reader and HTML ↔ Spond comparator.
**Done:** Implemented independent byte reader and normalized HTML ↔ Spond comparator with per-row diagnostics and explicit cancellation exclusion.
**Rationale:** The reader parses the written Spond import worksheet, groups by stable RVV-ID, preserves participant multiplicity, and emits structured duplicate/unmatched/inconsistent row evidence; the comparator checks all operational fields without row-order coupling.
**Findings:** HTML companion presence and revision consistency are also fail-closed so published cancellation bytes cannot be silently omitted or stale.
**Files:** tournament_scheduler/pipeline/export_parity/spond_reader.py, tournament_scheduler/pipeline/export_parity/comparator.py, tournament_scheduler/pipeline/export_parity/records.py, tournament_scheduler/pipeline/export_parity/html_reader.py, tournament_scheduler/pipeline/export_parity/verify.py
**Commit:** not committed
- 2026-10-02: Verified issue #625, current main/CI, published-sealed lifecycle, and existing export-parity/publication architecture; created feature branch.
