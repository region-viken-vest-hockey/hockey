# RVV Miniputt: operate

This is the single operator-facing entry point for RVV Miniputt work. The operator states the desired outcome in ordinary language; the active harness determines the lifecycle, loads the relevant shared procedures, and uses repository-owned capabilities to carry it out.

Read `AGENTS.md`, `.agents/skills/rvv/SKILL.md`, and this repository's internal routing guide `.agents/commands/rvv-miniputt/guide.md` before acting. The other files in this directory are internal shared procedures, not separate commands the operator should need to remember.

## Operator contract

1. Treat the operator's current request/command arguments as **intent**, not as instructions for which CLI/action to use.
2. Establish the current lifecycle and authoritative state before mutating anything:
   - initial/unpromoted pipeline candidate;
   - promoted canonical season;
   - approvals/locks;
   - current findings where relevant;
   - current export/audit/publication revision where relevant.
3. Classify and compose the request. One request may require several internal procedures; do not stop after the first mechanical change if the requested outcome requires repair, verification, export or audit.
   - Preserve any priority, conditionality or dependency the operator expressed between outcomes.
   - Treat confirmed conflicts and explicit requirements as mandatory intent.
   - Treat wording such as "if possible", "investigate", "prefer", "lower priority" or equivalent as conditional intent unless the surrounding request makes it mandatory.
   - Never worsen, undo or block a higher-priority outcome merely to satisfy a lower-priority preference.
   - If all requested outcomes cannot be satisfied acceptably, complete the higher-priority outcomes and report the lower-priority ones as unresolved rather than forcing a materially worse schedule.
4. Load only the relevant internal shared procedures under `.agents/commands/rvv-miniputt/` and follow their canonical boundaries. Do not recreate their policy in the harness.
5. Before changing a promoted schedule because of club/operator feedback, inspect the active accepted-change protections and request constraints, and give the request a stable request id. When the feedback describes intent rather than an exact placement (unavailable date/range, minimum gap between tournaments, opponent avoidance), translate it into the narrowest supported typed request constraint with `season add-constraint` **before** searching or mutating, then choose any legal result satisfying all active constraints. Earlier accepted requests and constraints remain binding unless the newer request explicitly supersedes them; never release one merely because it blocks a convenient candidate.
6. Prefer the smallest hard-valid change that satisfies the intent:
   - preserve approved/locked tournaments;
   - preserve unrelated tournaments;
   - prefer placement-preserving participant repair when placement is already valid;
   - prefer finding-directed repair/search before whole-season replanning;
   - preserve hosting responsibility;
   - use current revision-bound options and refresh them after canonical state changes.
7. Never hand-edit canonical season JSON, checkpoints, decisions, generated exports or public output to work around a missing action or failed verifier.
8. Never treat bounded-search exhaustion as proof of infeasibility.
9. Do not ask the operator to choose an internal command, action id, repair family or resume stage. Ask only for a genuinely missing real-world fact or authority that materially blocks the requested outcome.
10. If the intent changes schedule, roster/participants, placements, guest reservations, or durable decisions, run the appropriate canonical verification. Refresh findings after mutations. By default regenerate the canonical export and perform the semantic safety-net audit before handing off a changed schedule for review.
11. Publication is a separate authority boundary. **Never publish unless the operator explicitly asks to publish.**
12. If the repository lacks a canonical capability for the requested intent, do not improvise a state edit. Use the authoritative existing input/workflow when one exists; otherwise report the missing capability clearly so it can be implemented at the correct layer.

## Intent routing

Use `.agents/commands/rvv-miniputt/guide.md` as the routing guide and then load the relevant internal procedure(s). Typical intents include:

- **schedule/create a season** -> `run.md` and the canonical Stage 1–4 interactive flow; audit before any promotion/publication.
- **review/improve the current schedule** -> `season.md`; inspect fresh findings, direct repair options and bounded search, preserving approvals and minimizing churn.
- **move a tournament** -> `season.md` plus the move-tournament skill when applicable; inspect accepted-change protections and request constraints, assign a stable request id, resolve the durable tournament, respect approval lifecycle, and apply a targeted verified move.
- **semantic club request (unavailable date/range, minimum tournament gap, opponent avoidance)** -> `season.md`; record a typed request constraint with `season add-constraint --request-id <id>` before searching, then use findings/repair-options/search or a targeted move/swap to reach a result satisfying all active constraints. If the request needs an unsupported constraint type, surface that capability gap instead of locking an exact placement.
- **confirm/book/lock a tournament** -> `season.md` plus the confirmation skill; verify then approve/lock.
- **change tournament participants / make room for a team** -> `season.md`; inspect accepted-change protections and request constraints, use one-tournament `replace-participant` for a plain substitution and `swap-participants` only for a true two-tournament exchange, evaluate consequences for every affected team, and prefer the smallest legal coupled change before moving unrelated tournaments.
- **pure team-name/identity-label correction for an existing team** -> use `season rename-team --dry-run` and then apply the same atomic rename; do not use registration-set reconciliation, `replace-participant`, or replan for a label-only correction.
- **add/remove a registered team or change the season team set** -> change the authoritative controlled registration/input through its supported path, then reconcile/replan around the canonical baseline while preserving booked commitments. If no supported canonical/operator path exists, surface that capability gap rather than editing schedule JSON.
- **reserve/fill/release guest places** -> `season.md` guest capabilities; use repository-generated guest candidates for policy-level placement choices.
- **repair unresolved placement/participation/hosting findings** -> `season.md`; findings -> repair-options -> bounded search as needed -> verified revision-bound apply.
- **broader replan** -> `season.md`; keep approvals hard-preserved, generate a candidate, inspect diff/change cost, and apply only through the verified boundary.
- **calendar/source recovery** -> `calendars.md`, `scrape.md`, and/or `scrape-llm.md` as appropriate, then return to the canonical pipeline.
- **inspect status/evidence/logs** -> `status.md`, `logs.md`, and repository-owned evidence commands as appropriate.
- **export/audit** -> `season.md` plus the semantic audit contract in the RVV skill.
- **publish/rollback** -> `publish.md`, only after the exact current canonical export has a current semantic audit and the operator explicitly requested the public action.

## Completion standard

Report the result in operator terms:

- for multi-outcome requests, report each requested outcome separately as `resolved`, `partially resolved`, `unchanged by choice`, or `blocked`, preserving the operator's stated priority;
- what changed and why;
- what was deliberately preserved;
- meaningful before/after schedule-quality facts where available;
- unresolved findings or required host/operator confirmations;
- verification and semantic-audit result;
- resulting canonical/export revision or fingerprint when relevant;
- whether the result is ready for review or publication.

Do not claim global optimality unless the repository actually proves it.
