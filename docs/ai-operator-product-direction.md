# AI operator product direction

## Decision

RVV Miniputt is designed primarily as an **AI-operated season-planning system** with a deterministic Python core.

The human supervisor works through an LLM harness such as Pi, Claude, Codex, ChatGPT, OpenCode, or a normal terminal. The human supplies goals, domain judgment, authorization, and credentials when genuinely necessary. The operator owns routine mechanical work.

The repository has one operational implementation, not one per interface:

- shared policy/runbook: `.agents/skills/rvv/`
- executable workflow: `scripts/rvv-miniputt` + `tournament_scheduler/`
- harness adapters: thin UI/browser integrations only

For account ownership, volunteer handover, access review, and emergency recovery, see [`ownership-and-handover.md`](ownership-and-handover.md).

## Product promise

> Give RVV Miniputt the season inputs and ask it to produce the best trustworthy season plan. The operator validates inputs, gathers calendar data, resolves recoverable problems, generates and evaluates plans, exports the result, and involves the human only for genuine judgment or authorization.

## Product model

```text
Human supervisor
      |
      v
Harness / CLI adapter
      |
      v
Shared RVV runbook + repository CLI
      |
      v
Deterministic Python capabilities
      |
      +-- input/workbook
      +-- source health and scraping
      +-- recovery/readiness
      +-- scheduling/fairness
      +-- export/publication
      +-- audit/explanation
```

## Operator responsibility

The operator should:

1. understand the requested outcome
2. inspect the workspace and previous run state
3. validate and, where safe, repair inputs
4. collect calendar data and assess source health
5. recover routine source failures using available capabilities
6. rerun the canonical Python gate after recovery
7. generate/evaluate candidate plans
8. surface consequential compromises and remaining uncertainty
9. export requested artifacts
10. request narrow human decisions only when necessary

The human should not need to manually coordinate Stage 1–4 during normal operation.

## Interaction principles

### Goal-oriented rather than command-oriented

The normal user intent is an objective such as “produce the best trustworthy season plan.” Commands remain tools and escape hatches.

### Deterministic core, optional AI assistance

Hard constraints, source readiness, fairness gates, checkpoints, and publication safeguards live in deterministic repository code. AI assistance may investigate, navigate a browser, explain, compare, and suggest; it must not become a second authority for validity.

### Evidence before confidence

Every significant capability should make it possible to answer what happened, what evidence supports it, what remains uncertain, and what should happen next.

### Reproducible runs

A completed run should retain the input fingerprint, source provenance, planner/configuration metadata, candidate scores/seeds where applicable, manual decisions, selected result, and generated artifacts.

## Capability contract

Capabilities should return structured outcomes rather than only console prose. A useful common shape is:

```json
{
  "status": "ok | warning | blocked | failed",
  "summary": "What happened",
  "evidence": [],
  "artifacts": [],
  "problems": [],
  "suggested_actions": [],
  "requires_human": false
}
```

The exact representation may differ between commands, but CLI JSON, checkpoints, agent tools, and logs should converge on the same semantic contract.

## Calendar and recovery boundary

Source readiness is Python policy. Harnesses must not maintain their own lists of acceptable missing calendars.

Browser-capable harnesses may recover data that deterministic scraping cannot reach. Recovered events are returned to the shared cache/recovery path and Python Stage 2 is rerun. BookUp session establishment belongs on a visible macOS host when MFA is required; headless Lima runs reuse Playwright state under `.pipeline/auth/`.

See `.agents/skills/rvv/RUNBOOK.md` for the canonical operational rules.

## Human escalation policy

Escalate when:

- required authorization/credentials are unavailable
- unexpected source data is incomplete enough to block planning
- two materially different policy choices are both valid
- a repair would discard meaningful human data
- no valid plan exists under current hard constraints
- a public/external write requires approval

Escalations should be narrow, evidence-backed, and actionable.

## Interface boundary

There is no separate application implementation in the repository. If a richer non-technical interface is introduced later, it must consume the same application/CLI capabilities and checkpoints rather than duplicating scheduling, scraping, recovery, or policy logic.

## Documentation hierarchy

1. **Shared RVV skill/runbook** — operational policy used by every harness.
2. **Portable CLI/application layer** — canonical executable behavior.
3. **Harness adapters** — only integration unique to that environment.
4. **Detailed engine docs/tests** — implementation and maintenance guidance.

## Near-term roadmap

1. Keep the shared runbook and Python readiness logic authoritative.
2. Continue converting command output into structured capability evidence where useful.
3. Improve source provenance, freshness, and recovery observability.
4. Keep planning/fairness reproducible and explainable.
5. Keep human questions durable and narrowly scoped.
6. Reduce harness-specific prose and logic whenever duplication appears.
7. Treat any future interface as another thin adapter over the same capabilities.

## Non-goals

- replacing deterministic rules with LLM decisions
- creating separate business logic for each harness
- weakening public publication approval boundaries
- treating temporary BookUp availability exceptions as permanent manual calendars
- supporting every organization/calendar vendor without a demonstrated need

## Success criteria

A human can request a trustworthy season plan and the operator completes routine work, recovers expected failures, asks only focused questions, and delivers an auditable result without requiring the human to know which harness-specific implementation to use—because there is only one operational implementation.
