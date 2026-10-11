# RVV Miniputt: season — Semantic request constraints

Load from `../season.md` when routed here. Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` first; the lifecycle boundary and inspection path in `../season.md` still apply.

## Record semantic request constraints

### Date scope first: global ban vs. team constraint

Choose the narrowest canonical representation that matches the real-world fact:

```text
Global date unavailable for all tournaments
    -> canonical banned date (`season ban-date`)

Specific club/team cannot participate
    -> typed request constraint (`season add-constraint`)
```

A **banned date** is a global planning restriction: no tournament may be scheduled on it, regardless of host or participants. It reuses the existing `banned_dates` rule the planner, verifier, candidate-weekend enumeration, repair/search, optimizer and the `season batch` boundary all read -- there is no separate date-policy engine. Record it through:

```bash
scripts/rvv-miniputt season ban-date \
  --season <season> \
  --date 2027-02-27 \
  --request-id operator:vinterferie-2027 \
  --note "winter break - no ice"
```

`season ban-date` is a **policy/decision-only write**. It is deliberately allowed even when tournaments are already scheduled on that date, exactly like `season add-constraint`: the ban is persisted, the command reports the currently affected tournament ids, and the canonical-state revision advances so every stale maintenance/search/batch option is invalidated. Policy mutation and schedule repair stay separate operations.

```text
New banned date already contains tournaments
    -> record the ban first (`season ban-date`)
    -> derive the affected tournament ids (`season banned-dates --json`)
    -> repair them with one scoped atomic batch (`season batch`)
```

Inspect and remove bans with:

```bash
scripts/rvv-miniputt season banned-dates --season <season> --json
scripts/rvv-miniputt season unban-date --season <season> --date 2027-02-27 --note "winter break over"
```

`season banned-dates --json` returns each active date, its source/request id, whether it is currently satisfied, and the exact `affected_tournament_ids`, so the repair scope comes from one authoritative read path. Do not duplicate the ban as a per-team request constraint, do not store it only in plan/decisions free text, and do not hand-edit JSON. A banned date blocks `season move`, `season batch` moves, generated repair/search options, candidate-weekend recommendations, optimizer/replan moves and final canonical verification until it is repaired or explicitly unbanned.

### Record semantic request constraints

Club feedback frequently states intent rather than an exact replacement schedule. Do not collapse it into an exact placement lock. Translate it into the narrowest supported canonical typed constraint and record it **before** searching or mutating:

```bash
# team cannot play a date or inclusive range
scripts/rvv-miniputt season add-constraint --season <season> \
  --type team_unavailable \
  --team-club <club> --team-label <label> --team-age-group <age> \
  --date-from <YYYY-MM-DD> [--date-to <YYYY-MM-DD>] \
  --request-id <request-id> --note "<club wording>"

# at least N days between a team's tournaments
scripts/rvv-miniputt season add-constraint --season <season> \
  --type minimum_gap \
  --team-club <club> --team-label <label> --team-age-group <age> \
  --min-days <N> \
  --request-id <request-id>

# two teams must not meet within a date range
scripts/rvv-miniputt season add-constraint --season <season> \
  --type opponent_avoidance \
  --team-club <club> --team-label <label> --team-age-group <age> \
  --team2-club <club2> --team2-label <label2> --team2-age-group <age2> \
  --date-from <YYYY-MM-DD> [--date-to <YYYY-MM-DD>] \
  --request-id <request-id>
```

Scopes use stable team identity (club + label + age group). Constraint ids are deterministic from the semantic payload plus `--request-id`, so retrying the same request is idempotent. Malformed/ambiguous definitions (unknown or ambiguous team identity, inverted date range, non-positive gap, a team avoiding itself, unsupported type) are rejected at creation time.

Persist a typed constraint only from a *concrete durable semantic requirement*: "Kongsberg U9 must have at least 7 days between tournaments" -> `minimum_gap`; "Kongsberg cannot play 21 February" -> `team_unavailable`. Do **not** invent a numeric threshold from vague wording such as "three tournaments in eight days is a problem": preserve that as the operator's intent and address it through findings/search unless the operator states the actual policy threshold.

`season add-constraint` is a decision-only write and is **allowed even when the current schedule violates the new constraint**. It persists the validated constraint, reports its current structured violation(s), and advances the canonical-state revision (invalidating previously generated repair/search options). Confirm the recorded status with:

```bash
scripts/rvv-miniputt season constraints --season <season> --json
```

Each active constraint reports `satisfied` plus any `violations`. A single isolated violation is repaired through the ordinary capabilities (`season move`, `season swap-participants`, `repair-options`/`search`/`apply-repair`, `season replan`/`apply`). The repository enforces the active constraint set at the canonical apply boundary with baseline-aware semantics: a violation that already exists unchanged in the promoted plan stays visible as debt but does not veto an unrelated change, while a newly introduced or worsened violation is refused. Previews/`repair-options` report both the pre-existing and the candidate-introduced sets instead of hiding them. Search for a result that does not regress the active constraints -- never auto-release a constraint because it blocks an easy candidate. Schedule-changing commits are additionally checked for operational acceptability (see **Repair a localized finding**): a candidate must not newly place a tournament on `fixed_busy`/untrusted/external-conflict ice or introduce manual/host-confirmation work merely because it satisfies the typed constraints.

### Repair coupled violations atomically

An unchanged pre-existing violation no longer blocks an unrelated change, so one independent violation is repaired with the ordinary `season move` / `season swap-participants` commands. Use the atomic scoped batch boundary when several operations genuinely must land together (a coupled placement/roster repair, or several tournaments that must change in one revision): it applies the operations to one in-memory copy of the current canonical plan, runs the complete authoritative gates once on the final candidate, and commits exactly once. Nothing is written unless the entire batch is valid. Do **not** release valid constraints to unlock a repair, do **not** hand-edit canonical JSON, and do **not** use approvals or a broad `season replan` to fence the work.

```bash
cat > /tmp/batch.json <<'JSON'
[
  {"op": "move", "tournament_id": "rvv-0147", "date": "2027-02-27"},
  {"op": "swap_participants",
   "tournament_a": "rvv-0158", "team_a": "K9",
   "tournament_b": "rvv-0162", "team_b": "J9"}
]
JSON
scripts/rvv-miniputt season batch \
  --season <season> \
  --operations /tmp/batch.json \
  --scope rvv-0147 --scope rvv-0158 --scope rvv-0162 \
  --request-id <request-id> \
  --dry-run --json
```

`--scope` declares the affected tournament ids; every operation must reference only in-scope ids, and any tournament that changes outside the scope refuses the whole batch. Supported operations are `move` (any placement fields, `allow_cross_half` as needed), `swap_participants` (same-age roster exchange using the ordinary safe roster semantics), `cancel` (mark a tournament cancelled) and `remove_participant` (drop one participant from a tournament with no replacement; set `reconcile_withdrawal: true` per operation for a genuine season/age-group withdrawal, exactly like the single `season remove-participant` command). The dry-run report returns the declared scope, the ids actually changed, any changed ids outside scope (must be empty), the requested operations, the remaining pre-existing request-constraint violations, the introduced/worsened regressions, the hard-verification and operational-acceptability verdicts, protection/approval/lock conflicts, guest-reservation integrity, the hosting-responsibility verdict, before/after canonical fingerprints/revisions, and per-team consequences where participants change. The dry-run report also carries a stable top-level `verdict` object (`status`, `applicable`, `checks`, `blockers`) so automation can consume one domain verdict instead of re-deriving applicability from individual fields; a reviewed consequence set additionally appears under `verdict.review` (see the dry-run contract above). Repeat the same command without `--dry-run` to commit once. A batch that introduces or worsens a request-constraint violation is refused without writing anything; unchanged pre-existing violations remain visible as debt and do not themselves refuse the batch.

Use this path when several operations genuinely must be committed together; an isolated violation still goes through the ordinary `season move` / `season swap-participants` commands.

Release only when a newer request explicitly supersedes/revokes it:

```bash
scripts/rvv-miniputt season release-constraint --season <season> \
  --constraint-id <id> \
  --note "superseded by <new-request-id>"
# or release every active constraint created by an earlier request:
scripts/rvv-miniputt season release-constraint --season <season> \
  --request-id <old-request-id> \
  --note "superseded by <new-request-id>"
```

If the semantic request cannot be represented by a supported type, surface that capability gap instead of silently reducing it to an exact placement lock. A request constraint stays authoritative after a successful repair: the granular change protections may still guard the exact accepted result, but they never replace the higher-level request.

