# ADR 0001: Stage 3 uses generic optimization with deterministic verification; BookUp recovery remains portable

- **Status:** Accepted
- **Date:** 2026-09-04
- **Decision owner:** RVV hockey project

## Context

Stage 3 historically accumulated scheduling judgment inside bespoke planner heuristics. At the same time, calendar recovery for BookUp-style sources became dependent on browser/session details that vary between local shells and agent environments.

The durable architecture needs to keep hard correctness reproducible while allowing planning/search implementations and agent transports to evolve.

## Decision

### Stage 3 contract

Stage 3 is organized around this boundary:

```text
controlled workbook + trusted calendar evidence
                 ↓
       normalized planning problem
                 ↓
      generic solver/search capability
                 ↓
             candidate plan
                 ↓
       deterministic verification
          + quality metrics
                 ↓
      valid candidate / findings
                 ↓
      agent/operator soft judgment
```

The repository owns the normalized problem, candidate schema, hard constraints, solver/search primitives, deterministic verifier, reproducible metrics, persistence and Stage 4 export boundary.

The agent may choose which exposed search/refinement action to use and how to trade off soft quality dimensions when no hard rule determines the answer.

A solver implementation is not itself the business-policy source. CP-SAT, local search, repair, enumeration or another mechanism may be used or replaced as long as they operate through the same deterministic problem/verification contract.

### Hard rules vs soft preferences

Hard invariants belong in deterministic verification with tests. Examples include valid team identities, impossible duplicate participation, arena/time conflicts that the project treats as blocking, explicit locks/exclusions, season-window bounds and other rules the project has deliberately made mandatory.

Soft preferences remain measurable facts plus contextual judgment unless the project explicitly promotes them to a rule. Do not turn one successful benchmark weighting into permanent policy merely because it improved one sample schedule.

### Preserve measurable trade-offs

The optimizer/search layer should expose candidate differences in reproducible metrics rather than hide all quality behind one opaque global score. Where practical, candidates may expose Pareto-style trade-offs so the decision layer can choose among valid alternatives.

### Legacy/baseline planners

Legacy planner implementations may remain as baselines, fallbacks or migration aids while useful, but they are not the architecture boundary. The stable boundary is normalized problem → candidate → deterministic verification/measurement.

## BookUp/browser recovery boundary

Browser authentication/session handling is an adapter/runtime concern; source validity remains a repository concern.

```text
browser/session-capable environment
            ↓
      recover source data
            ↓
repository recovery/merge validation
            ↓
   Stage 2 trusted evidence/cache
```

A harness may provide browser control or an authenticated session, but recovered events are not trusted until they pass the repository's normal validation/merge path. The same Stage 2 semantics therefore apply whether recovery originates from Pi, another browser-capable harness, or a separate local recovery process.

## Consequences

### Positive

- hard correctness remains deterministic and testable;
- solver technology can evolve without moving business-policy ownership;
- agents can reason about current soft trade-offs from measured evidence;
- browser/session recovery does not create a second Stage 2 implementation;
- harness adapters can remain thin.

### Costs

- candidate contracts and metrics must remain stable enough to support multiple search implementations;
- agent decisions may differ, so deterministic verification and audit summaries are required;
- browser/session recovery still needs environment-specific integration when a source requires interaction.

## Relationship to ADR 0002

ADR 0002 generalizes this ownership boundary repository-wide: deterministic facts, hard rules, measurement and safe execution belong in code; contextual soft judgment belongs to the agent; adapters remain thin.
