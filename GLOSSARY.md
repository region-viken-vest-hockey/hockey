# RVV Miniputt Glossary

This glossary defines the canonical terminology for the RVV Miniputt scheduling domain. Use these terms exactly in code, skills, commands, docs, and conversation. When multiple words exist for the same concept, the term below is canonical; alternatives are listed under `_Avoid_`.

## Core Domain

**Canonical Tournament**
A tournament that has been promoted into the authoritative season state (`season/<season>/schedule.json`) and assigned a durable ID. It exists in the canonical schedule and is subject to approval, placement locking, and reconciliation.
_Avoid_: promoted tournament, approved tournament, locked tournament, scheduled tournament, confirmed tournament (unless referring to the approval action)

**Durable Tournament ID**
A stable, unique identifier for a canonical tournament that persists across revisions, moves, and approvals. Format: `t-<hash>` or similar. Used as the primary key in all canonical state, decisions, approvals, and audit trails.
_Avoid_: tournament id, schedule id, booking id (when referring to the canonical tournament's identity)

**Canonical Revision**
A monotonically increasing counter or fingerprint that identifies a specific version of the canonical season state. Every mutation (approval, move, booking change, reconciliation) produces a new revision. Exports and audits are bound to a specific revision.
_Avoid_: pipeline revision, schedule revision, version, iteration

**Canonical Season**
A season directory (`season/2024-2025/`) containing the authoritative schedule, decisions, approvals, and pipeline checkpoints. The single source of truth for that season's operational state.
_Avoid_: published season, sealed season, active season (use lifecycle terms below)

**Placement**
The assigned date, arena, host club, and time slot for a canonical tournament. Placement is distinct from participant roster.
_Avoid_: booking, slot, ice slot, schedule entry (when referring to the tournament's assigned place)

**Placement Lock**
A boolean flag on a tournament's approval record indicating its placement (date/arena/host/time) cannot be changed by subsequent planning or maintenance operations. Set via `season approve --placement-lock` (default). Does not lock participants.
_Avoid_: booking lock, schedule lock, confirmation lock

**Participant Lock**
A boolean flag on a tournament's approval record indicating its participant roster (clubs/teams) cannot be changed. Set explicitly via `season approve --participants-lock`. Independent of placement lock.
_Avoid_: roster lock, team lock (when referring to the approval flag)

**Booking Assertion**
A record in `decisions.json` asserting that a host club has a specific ice booking (date, arena, time range). Created by operators when ice is secured. Distinct from calendar events which are scraped from external sources.
_Avoid_: booking, ice booking, calendar booking (when referring to the canonical assertion)

**Calendar Event**
An event scraped from a host club's external calendar (Google Calendar, team website, etc.). May or may not correspond to a booking assertion. Reconciliation compares calendar events against booking assertions and canonical placements.
_Avoid_: scraped event, external booking, host calendar entry

**Reconciliation**
The process of comparing canonical placements, booking assertions, and calendar events to classify findings: `matched`, `unmatched_calendar`, `unmatched_booking`, `missing_calendar`, `duration_mismatch`, etc. Records reality; does not plan, search, repair, or mutate canonical state.
_Avoid_: matching, sync, calendar sync, booking verification

## Lifecycle States

**Initial Planning**
The lifecycle phase before a season is promoted. Characterized by: `SeasonPlanner` runs, Stage 3 optimization, candidate generation, no canonical schedule yet.
_Avoid_: pre-season, draft season, planning phase

**Promoted Season**
A season that has been promoted via `rvv-review-baseline` (or equivalent). Has a canonical `schedule.json` and enters the operational baseline. Not yet published/sealed.
_Avoid_: reviewed season, baseline season

**Published Season**
A promoted season that has been published to GitHub Pages via the publication workflow. Publicly visible. Still mutable through production maintenance (booking, moves, cancellations) but not through replanning.
_Avoid_: live season, public season

**Sealed Season**
A published season that has been explicitly sealed (no further mutations allowed except publication rollback). The final authoritative state for historical record.
_Avoid_: closed season, final season, archived season

**Reopened Planning**
An explicit operator-authorized transition from published/sealed back to planning lifecycle. Requires founder approval. Not a routine maintenance operation.
_Avoid_: re-planning, season reopen

## Operations

**Canonical Mutation**
Any operation that changes the authoritative season state (`schedule.json`, `decisions.json`, `approvals.json`) through the revision-bound contract: authoritative revision → proposed change in memory → effective facts → classify findings → capability policy → exact preview → atomic commit against same revision → reload/verify/audit.
_Avoid_: schedule update, season edit, data mutation

**Approval**
The act of recording an operator's confirmation of a tournament's placement (and optionally participants) via `season approve`. Produces an approval record with fingerprint, lock flags, and actor. Not a waiver — re-verifies against canonical verification problem.
_Avoid_: confirmation, booking confirmation, ice confirmation

**Waiver**
An explicit operator decision to accept a tournament placement that fails hard verification (e.g., arena conflict, duration mismatch). Distinct from approval. Recorded in `decisions.json` with rationale.
_Avoid_: exception, override, approval with issues

**Export**
Generating a derived artifact (JSON, CSV, HTML) from canonical state at a specific revision. Exports are derived data; fix canonical state and regenerate rather than patching exports.
_Avoid_: report, dump, publication artifact (when not yet published)

**Publication**
The workflow that takes a canonical export and deploys it to GitHub Pages (or other public surface). Requires explicit operator authority. Distinct from export and from sealing.
_Avoid_: deploy, release, publish (when referring to export generation)

## Technical

**Fingerprint**
A hash of the protected fields of a canonical entity (tournament placement, participant roster, booking assertion) used to detect staleness. An approval is stale if the current fingerprint differs from the approved fingerprint.
_Avoid_: hash, checksum, signature

**Pipeline Checkpoint**
An intermediate artifact produced during initial planning (Stage 1, Stage 2, Stage 3 outputs). Stored under `.pipeline/` and bound to a planning run. Not canonical season state.
_Avoid_: stage output, planner artifact, intermediate result

**Source Validity**
The assessed freshness and trustworthiness of a calendar source. Determines whether calendar events can be used as evidence in reconciliation. Managed by `sources/SKILL.md` capability.
_Avoid_: source freshness, calendar trust, source quality

**Club Profile**
A validated structured profile for a host club containing: arena mappings, calendar source configuration, booking authority contacts, constraints, and preferences. Repository fact; absence preserves generic behavior.
_Avoid_: club config, host config, club settings

## Verification & Evidence

**Hard Validity**
A constraint that must hold for a canonical tournament placement (e.g., arena availability, no overlapping tournaments, duration matches booking). Violations block approval unless waived. Verified by repository code, not prompts or approvals.
_Avoid_: validation, invariant, business rule

**Semantic Audit**
An automated check that verifies canonical state against domain rules (scheduling rules, booking authority, participant eligibility). Produces findings classified as `resolved`, `unchanged`, `new`, `worsened`. Distinct from reconciliation.
_Avoid_: validation, audit, rule check

**Finding**
A single result from semantic audit or reconciliation. Has a code, severity, affected entity, and classification. Findings are revision-bound and do not persist across mutations unless re-identified.
_Avoid_: issue, problem, violation, error