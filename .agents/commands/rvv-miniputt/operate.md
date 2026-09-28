# RVV Miniputt: operate

This is the single operator-facing entry point for RVV Miniputt work. The operator states the desired outcome in ordinary language; the active harness determines the lifecycle, loads the relevant shared procedures, and uses repository-owned capabilities to carry it out.

At the start of a new session or after context loss, first follow
`.agents/commands/rvv-miniputt/handover.md` and verify current refs; never
reuse old conversation status as authority. This step grants no mutation or
publication permission.

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
4. Load only the relevant lazy-loaded operation family from `guide.md`, then the necessary canonical supporting procedures. Do not eagerly load all of `season.md` for an unrelated request or recreate repository policy in the harness.
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
   - For booking/calendar reconciliation, follow the shared RVV **Booking evidence authority and reconciliation** contract. Read current issue comments and existing accepted decisions before escalating; independently check the actual host source where relevant. Do not treat stale canonical/default duration or published export as evidence against a verified real booking. A booked worksheet/email assertion with a concrete source date/start/end must align the canonical effective interval through the repository-owned booking-set path; never leave a booked badge beside stale/default public times. Distinguish event-level verification from season-wide coverage, and perform routine evidence checks without asking the operator to choose the next internal step.
10. If the intent changes schedule, roster/participants, placements, guest reservations, or durable decisions, run the appropriate canonical verification. Refresh findings after mutations. By default regenerate the canonical export and perform the semantic safety-net audit before handing off a changed schedule for review.
11. Publication is a separate authority boundary. **Never publish unless the operator explicitly asks to publish.**
12. If the repository lacks a canonical capability for the requested intent, do not improvise a state edit. Use the authoritative existing input/workflow when one exists; otherwise report the missing capability clearly so it can be implemented at the correct layer.


## Publication authority and current-policy reconciliation

When the operator explicitly asks to publish a proposed season plan, treat that as authorization to publish **a proposal**, not an assertion that the host has booked the ice. Reload the current repository-owned publication contract from `publish.md`, `audit-publication.md`, the RVV skill and current code/tests before acting; a previous conversational refusal, cached skill interpretation or earlier commit is not authority over current main. Evaluate the current export and complete the canonical publish workflow if its deterministic gates pass. Never impose an additional harness-only requirement that a proposed placement already have accepted booking evidence.

Distinguish these outcomes:
- **Proposed / awaiting confirmation:** may be publicly visible with that explicit status, subject to normal hard verification, conflict checks, audit, reconciliation, export parity/freshness, sanitization and deployment checks. Do not record or display it as booked.
- **Booked:** requires accepted source-backed evidence for the exact interval.
- **Contradicted / rejected or genuine hard-gate failure:** do not force publication; report the exact current gate, affected IDs and evidence, and resolve through the canonical owner.

An operator-authorized code or runbook change is not evidence of account compromise or instruction poisoning merely because it changes a rule the harness previously cited. Do not speculate about the operator's account/session or repeatedly refuse based on that sequence alone. Assess the actual current diff, repository provenance and executable gates when there is concrete evidence of a problem. If current code and documentation disagree, report the specific inconsistency rather than substituting a personal policy.

Once the operator has explicitly authorized publication, do not replace the requested public result with a private club review packet or ask again for permission solely because booking confirmation is pending. Run the canonical export/audit/publish path and verify the deployed result; if a real deterministic gate blocks it, report the precise blocker without fabricating evidence, bypassing checks or changing audit verdicts.

## Lazy-loaded operation families

Route through `guide.md` and load only the needed family: `tournament-maintenance.md`, `booking-management.md`, `team-management.md`, `schedule-quality.md`, `governance.md`, or `season-delivery.md`. For an audit or publication request, additionally load `audit-publication.md`. Compose families when a request crosses boundaries (for example, club booking rejection -> tournament repair -> distribution comparison -> export/audit). The specialized guides own detailed execution; this entry point retains the global authority and completion contract.

## Intent routing

Use `.agents/commands/rvv-miniputt/guide.md` as the routing guide and then load the relevant internal procedure(s). Typical intents include:

- **schedule/create a season** -> `run.md` and the canonical Stage 1–4 interactive flow; audit before any promotion/publication.
- **review/improve the current schedule** -> `season.md`; inspect fresh findings, direct repair options and bounded search, preserving approvals and minimizing churn.
- **move a tournament** -> `season.md` plus the move-tournament skill when applicable; inspect accepted-change protections and request constraints, assign a stable request id, resolve the durable tournament, respect approval lifecycle, and apply a targeted verified move.
- **semantic club request (unavailable date/range, minimum tournament gap, opponent avoidance)** -> `season.md`; record a typed request constraint with `season add-constraint --request-id <id>` before searching, then use findings/repair-options/search or a targeted move/swap to reach a result satisfying all active constraints. If the request needs an unsupported constraint type, surface that capability gap instead of locking an exact placement.
- **confirm/book/lock a tournament** -> `season.md` plus the confirmation skill; verify then approve/lock.
- **change tournament participants / make room for a team** -> `season.md`; inspect accepted-change protections and request constraints, use one-tournament `replace-participant` for a plain substitution, `remove-participant` when a team drops out with no same-age replacement (with `--reconcile-withdrawal` only for a genuine season/age-group withdrawal, or one atomic `batch` of `remove_participant` operations across several tournaments), and `swap-participants` only for a true two-tournament exchange; evaluate consequences for every affected team, and prefer the smallest legal coupled change before moving unrelated tournaments.
- **pure team-name/identity-label correction for an existing team** -> use `season rename-team --dry-run` and then apply the same atomic rename; do not use registration-set reconciliation, `replace-participant`, or replan for a label-only correction.
- **add/remove a registered team or change the season team set** -> change the authoritative controlled registration/input through its supported path, then reconcile/replan around the canonical baseline while preserving booked commitments. If no supported canonical/operator path exists, surface that capability gap rather than editing schedule JSON.
- **reserve/fill/release guest places** -> `season.md` guest capabilities; use repository-generated guest candidates for policy-level placement choices.
- **repair unresolved placement/participation/hosting findings** -> `season.md`; findings -> repair-options -> bounded search as needed -> verified revision-bound apply.
- **broader replan** -> `season.md`; keep approvals hard-preserved, generate a candidate, inspect diff/change cost, and apply only through the verified boundary. On a `published_sealed` season broad replan is refused; use targeted canonical maintenance instead, or a deliberate operator-authorised `season reopen-planning` for a genuine full restructuring.
- **calendar/source recovery** -> `calendars.md`, `scrape.md`, and/or `scrape-llm.md` as appropriate, then return to the canonical pipeline.
- **inspect status/evidence/logs** -> `status.md`, `logs.md`, and repository-owned evidence commands as appropriate.
- **inspect/seal the published season lifecycle** -> `season.md`; a published season is maintenance-only (`published_sealed`); migrate/backfill a legacy published season with `season seal-published` before treating it as sealed.
- **export/audit** -> `season.md` plus the semantic audit contract in the RVV skill.
- **publish/rollback** -> `publish.md`, only after the exact current canonical export has a current semantic audit and the operator explicitly requested the public action. To **export and republish an already published/sealed season** after approved canonical changes, use `republish.md`: resolve the current published run from authoritative publication history, run `season export` (no replan), inspect `season publication-evidence --json`, audit, then publish only on explicit authorization.

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
