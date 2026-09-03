# AI operator product direction

## Decision

RVV Miniputt will be designed primarily as an **AI-operated season-planning system**.

The primary user is a human supervisor working through an LLM harness such as Codex, Claude Code, OpenCode, Pi, or another capable agent environment. The human provides goals, domain judgment, credentials when necessary, and final approval. The AI operator owns the mechanical workflow. Routine operation should use club-controlled ownership and recovery paths rather than personal accounts; see the [ownership and handover guide](ownership-and-handover.md).

This does not rule out a future non-technical supervisor interface. It means the near-term architecture and product decisions should optimize for reliable agent operation first.

## Product promise

> Give RVV Miniputt the season inputs and ask it to produce the best possible season plan. The AI operator validates inputs, gathers calendar data, resolves recoverable problems, generates and evaluates plans, and exports the result while involving the human only when genuine judgment or authorization is required.

## Product model

```text
Human supervisor
      |
      v
AI operator
      |
      +-- input and workbook capability
      +-- calendar and scraping capability
      +-- scheduling capability
      +-- plan evaluation capability
      +-- repair and recovery capability
      +-- export capability
      +-- audit and explanation capability
```

The scheduling engine remains deterministic infrastructure. It should expose clear capabilities and evidence to the operator rather than forcing the human to manually coordinate pipeline stages.

## Operator responsibility

The AI operator should:

1. understand the requested outcome
2. inspect the current workspace and previous run state
3. validate and, where safe, repair inputs
4. collect calendar data and assess source health
5. recover from routine source failures
6. generate multiple candidate plans when useful
7. evaluate candidates against hard constraints and soft objectives
8. select or recommend a plan with an explanation
9. export all requested artifacts
10. report what changed, remaining uncertainty, and decisions requiring human input

The human should not need to think in terms of Stage 1 through Stage 4 unless debugging.

## Interaction principles

### Goal-oriented rather than command-oriented

The normal entry point should be an objective such as:

> Produce the best possible season plan from the current workbook.

Commands remain available as tools and escape hatches, but should not define the main user experience.

### Autonomous within explicit boundaries

The operator may perform reversible and local actions without asking repeatedly. It must ask before actions that require missing credentials, change external systems, discard meaningful human edits, or resolve ambiguous policy choices.

### Evidence before confidence

Every significant capability should make it possible to answer:

- What happened?
- What evidence supports the result?
- How confident is the system?
- What should happen next?

### Deterministic core, optional AI judgment

Hard constraints and baseline scoring must remain deterministic and testable. AI assistance may interpret results, investigate failures, suggest changes, and compare trade-offs, but must not be required to guarantee validity.

### Reproducible runs

A completed run should record enough information to reproduce and audit it, including:

- input fingerprint
- source snapshots and provenance
- planner version
- configuration and penalty weights
- random seeds
- candidate scores
- manual or AI-proposed adjustments
- final selection rationale

## Capability contract

Capabilities should return structured outcomes rather than only console text or success/failure.

A useful common shape is:

```json
{
  "status": "ok | warning | blocked | failed",
  "summary": "What happened",
  "evidence": [],
  "confidence": 0.0,
  "artifacts": [],
  "problems": [],
  "suggested_actions": [],
  "requires_human": false
}
```

This does not need to become one universal Python class immediately. It is the direction for CLI JSON output, checkpoints, agent tools, logs, and desktop-backend responses.

## Initial capabilities

### Workspace and input

- inspect workspace
- locate the active workbook
- validate workbook structure and values
- explain validation failures
- propose or apply safe repairs
- show changes before and after repair

### Calendar sources

- list configured sources
- test authentication and reachability
- scrape or fetch events
- report source health and date coverage
- compare fresh data with cache
- identify suspiciously sparse or changed sources
- recover or accept manually supplied events
- preserve provenance for every event

### Planning

- generate one or more candidate plans
- enforce hard constraints
- score soft constraints and fairness
- explain score components and violations
- retain seed and configuration metadata
- honor locked assignments and human decisions

### Evaluation and refinement

- compare candidates
- identify the most consequential compromises
- suggest targeted adjustments
- rerun only the necessary work
- stop when further retries are unlikely to provide meaningful improvement

### Export

- validate the selected plan before export
- produce all configured formats
- summarize generated artifacts
- show warnings that affect downstream use

## Human escalation policy

The operator should escalate when:

- required credentials or permissions are unavailable
- source data is too incomplete to trust
- two policy choices are both valid but materially different
- a repair would discard meaningful user data
- no valid plan exists under the current hard constraints
- an external write or publication requires authorization

Escalations should be narrow and actionable. The operator should provide context, alternatives, and a recommended choice rather than returning a raw exception.

## Role of the desktop app

The desktop app is not the primary near-term interface. It may evolve into a supervisor console that displays:

- current objective and progress
- evidence and source health
- questions awaiting human input
- candidate plan comparison
- approvals and locked decisions
- generated artifacts and audit history

It should call the same capabilities as other harnesses rather than duplicate planning logic.

## Documentation hierarchy

The main documentation should distinguish three layers:

1. **Use the AI operator** — the preferred goal-oriented workflow
2. **Use the portable CLI** — automation, recovery, and debugging
3. **Develop the engine and adapters** — internal architecture and harness integrations

Harness-specific adapters should remain thin. Business logic belongs in the Python package and should be usable without any particular LLM provider.

## Near-term roadmap

1. Define an operator run manifest and structured capability result format.
2. Add a single goal-oriented operator entry point.
3. Make source health, provenance, and recovery agent-friendly.
4. Make candidate generation and score comparison reproducible and explainable.
5. Add a human escalation and approval mechanism.
6. Simplify the README around the AI-operator workflow.
7. Treat any future non-technical UI as an optional supervisor surface over the same APIs.

## Non-goals for the first iteration

- building a complete drag-and-drop scheduling application
- replacing deterministic rules with LLM decisions
- supporting every hockey organization or calendar vendor
- making the operator fully unattended for credentials, policy decisions, or external publication
- creating separate business logic for each LLM harness

## Success criteria

The direction is working when a human can start with a request such as:

> Inspect the current inputs and produce the best trustworthy season plan.

The operator should then complete all routine work, recover from expected failures, ask only focused domain questions, and deliver an auditable result without the human manually coordinating pipeline stages or specialized recovery commands.
