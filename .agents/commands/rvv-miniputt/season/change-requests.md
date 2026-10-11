# RVV Miniputt: season — Club and operator change requests

Load from `../season.md` when routed here. Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` first; the lifecycle boundary and inspection path in `../season.md` still apply.

## Process incoming club/operator change requests

Treat each accepted feedback item as durable intent that later maintenance must preserve.

Before a manual placement or participant change:

```bash
scripts/rvv-miniputt season status --season <season>
scripts/rvv-miniputt season approvals --season <season> --json
scripts/rvv-miniputt season protections --season <season> --json
scripts/rvv-miniputt season constraints --season <season> --json
```

Give the incoming request a stable `request_id`. Prefer an existing external/message/request id when one exists. Otherwise synthesize a deterministic local id from the source and intent (for example `club-feedback:<club>:<date>:<short-purpose>`) and check `season protections` so it does not collide. The operator should not need to invent this internal id.

Classify the requested outcome before choosing a command:

- **global date unusable for every tournament** (ice hall closed, a holiday/weekend nobody can host or play) -> record a canonical **banned date** (`season ban-date`), not a per-team request constraint;
- **semantic constraint, not an exact placement** (a team unavailable on a date/range, a minimum gap between a team's tournaments, an opponent to avoid within a date range) -> record a typed request constraint first (see **Record semantic request constraints** below), then search for any legal result satisfying all active constraints;
- **specific date/arena/host/time change** -> use the targeted `season move --request-id <id>` flow below;
- **pure team-name/identity-label correction across the canonical season** (same club, same age group, same underlying team) -> evaluate `season rename-team --dry-run --request-id <id>` and apply it atomically; do not use registration-set reconciliation, `replace-participant`, or a replan;
- **specific one-tournament participant substitution** ("replace team A with team B here") -> evaluate `season replace-participant --dry-run --request-id <id>`; an existing finding is not required;
- **specific participant/roster exchange between two tournaments** -> evaluate `season swap-participants --dry-run --request-id <id>`; an existing finding is not required;
- **one participant drops out with no same-age replacement** -> evaluate `season remove-participant --dry-run --request-id <id>` for one affected tournament, or an atomic `batch` of `remove_participant` operations when several tournaments lose the same team; pass `--reconcile-withdrawal` only for a genuine season/age-group withdrawal, never merely to make an underfilled candidate legal;
- **general request to improve participation/placement** -> use current findings/repair-options/search first, then the smallest verified change;
- **broader rebalance** -> only then escalate to baseline-aware `season replan` / `diff` / verified `apply`; on a `published_sealed` season these are refused, so complete the outcome through targeted canonical operations or a deliberate, operator-authorised `season reopen-planning`.

For a participant replacement or swap, preview alternatives before applying. The repository reports before/after consequences for **all** affected teams (spacing, temporal coverage, opponent repetition/diversity and travel), and applying a materially regressive change is refused. Do not optimize the requesting club by treating the displaced team as free capacity.

Accepted swaps and moves create granular protections in canonical `decisions.json`. A later candidate that would undo one is a conflict with prior accepted intent, not permission to discard it. When a protection blocks a candidate:

1. prefer another legal candidate that preserves all accepted requests;
2. if the new request **explicitly supersedes or reverses** the earlier request, release only the relevant earlier protection(s), recording the newer request in the note;
3. if the relationship is ambiguous and no safe alternative exists, ask for the real-world clarification rather than silently releasing the earlier request.

Keep evaluating the operator's *original outcome* across intermediate mutations. A finding disappearing after a temporary/partial change does not prove the real problem is resolved (a clustered team moved onto unusable ice can clear a `temporal_clustering` finding while failing "produce a usable, better-spaced schedule"). Prefer `current canonical state -> repository search -> final coupled candidate -> one atomic apply` over chains of temporary canonical moves used only to unlock a later search. If a temporary mutation is genuinely necessary, treat it as temporary and release only the protections that operation created -- never unrelated accepted protections.

Explicit supersession:

```bash
scripts/rvv-miniputt season release-protection \
  --season <season> \
  --request-id <old-request-id> \
  --note "superseded by <new-request-id>"
```

Then apply the newer change with `--request-id <new-request-id>`. Never release a protection merely to make an optimizer or convenient swap candidate fit. Generic `season apply` is also protection-aware, so a broader replan cannot quietly reverse accepted feedback.

After any accepted mutation, re-run `season protections --json` and `season constraints --json`, and report which prior requests remain protected and which new protections/constraints were added.

