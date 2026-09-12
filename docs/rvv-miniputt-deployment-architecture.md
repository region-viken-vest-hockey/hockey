# RVV Miniputt runtime and publication architecture

This document describes how RVV Miniputt is actually run and published today. It is not a future hosting proposal.

## Current deployment model

RVV Miniputt does **not** require an always-on backend, database, queue, worker service, or dedicated web application.

The supported operating model is:

```text
maintainer/agent/CI checkout
        ↓
repository CLI / Make targets
        ↓
local generated state (.pipeline/)
        ↓
review/export snapshot (export/<timestamp>/)
        ↓
public-bundle preparation + privacy checks
        ↓
explicit approval
        ↓
GitHub Pages (gh-pages branch)
        ↓
WordPress links/embeds
```

The season planner is therefore deployed as **versioned repository code plus static published output**, not as a continuously running service.

## Execution environments

The same repository capabilities can be invoked from:

- a normal local shell;
- Pi, which adds RVV-specific slash commands/browser integration;
- Claude/ChatGPT/Codex adapters;
- GitHub Actions where the workflow is suitable for headless execution.

Environment-specific adapters must call the same repository capabilities and must not become independent policy engines.

Browser/MFA-dependent source recovery may require a local interactive session. Recovered data returns through repository validation before it is trusted.

## Local state

`.pipeline/` is generated runtime state and may contain:

- stage checkpoints;
- scraped source cache/provenance;
- run manifest and structured capability results;
- operator questions/answers;
- logs and fingerprints.

It is intentionally not committed as documentation or as the authoritative planning input.

## Export state

Stage 4 writes a timestamped review/export bundle under `export/`. The bundle may include HTML views, Excel/CSV/iCal downloads, Spond material, per-club review packets, and supporting generated views.

The export bundle is review material. It is not automatically public.

## Public bundle

Publication creates a separate public snapshot from the export directory. The public-bundle step:

- copies only explicitly allowed public files/directories;
- excludes private/review-only artifacts such as per-club review packets and Spond exports by default;
- rejects unknown/unapproved file types;
- checks included text for likely sensitive material;
- redacts local paths/contact data where supported;
- records a privacy report;
- blocks publication when a finding requires human review.

This separation is deliberate: **raw export != public site**.

## GitHub Pages layout

The publication code manages static Pages snapshots on the `gh-pages` branch, including the current `latest/` view and retained run/history material needed for verification/rollback.

WordPress should point to or embed these generated views. Do not maintain a second manually edited copy of the schedule in WordPress.

The registered-team and activity-calendar workflows also stage a complete Pages snapshot before publishing so updating one view does not remove unrelated published content.

## Publication safety

Public writes are explicit operations. Normal planning commands do not publish.

Typical flow:

```bash
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

Rollback is also explicit:

```bash
make publish-history
make rollback RUN_ID=<id> CONFIRM_PUBLIC=1
```

## Ownership and recovery

The technical runtime is intentionally simple, but access ownership still matters. GitHub, Pages, Microsoft 365, WordPress, Spond, calendar-source accounts and recovery paths should not depend on one maintainer's undocumented personal access.

See [`ownership-and-handover.md`](ownership-and-handover.md) for the operational ownership and emergency-recovery procedure.

## When a hosted service would be justified

A dedicated frontend/worker/storage architecture should only be introduced if the operating model changes materially—for example concurrent users/runs, routine unattended scheduling, or a requirement for a web upload/status application. That is not required by the current repository and should be treated as a new architectural decision rather than assumed future state.
