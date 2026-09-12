# AI operator product direction

## Decision

RVV Miniputt is designed primarily as an **AI-operated season-planning system over deterministic repository capabilities**.

The human supervisor provides goals, credentials/authorization when required, explicit policy decisions, and final publication approval. The agent/operator coordinates the mechanical workflow and contextual soft trade-offs. Repository code remains authoritative for facts, hard constraints, reproducible measurements, persistence, validation, and publication safety.

This direction does not require a particular model or harness. Pi, Claude Code, ChatGPT, Codex, OpenCode, or future interfaces are transports/adapters over the same repository capabilities and shared RVV runbook.

## Product promise

> Give RVV Miniputt controlled season inputs and ask it to produce the best trustworthy season plan. The agent validates inputs, gathers source evidence, recovers routine failures, generates/evaluates candidates, and exports a reviewable result while involving the human only when authorization or real policy judgment is required.

## Operating model

```text
Human supervisor
      ↓
AI/agent controller
      ↓
validated repository capabilities
      ├─ input/registration
      ├─ source/calendar collection and recovery
      ├─ planning/search/solver
      ├─ deterministic verification and metrics
      ├─ export/privacy/publication preparation
      └─ durable operator state and audit
```

## Responsibility boundary

### Repository code owns

- controlled input parsing and normalization;
- team/club/source/arena/calendar facts and provenance;
- hard scheduling constraints and candidate validation;
- reproducible quality/fairness measurements;
- generic search/solver mechanics;
- checkpoints, manifests, fingerprints, and action validation;
- export/privacy/publication safety gates.

### Agent/controller owns

- deciding which soft warning/quality dimension to prioritize;
- choosing among exposed valid recovery/search/refinement actions;
- interpreting contextual organizer guidance;
- comparing valid soft trade-offs and recommending a candidate;
- presenting uncertainty and focused escalation to the human.

### Human owns

- credentials and MFA;
- explicit business-policy changes/exceptions that require authority;
- destructive/external approval where required;
- final publication/rollback approval.

ADR 0002 is the durable architecture decision for this boundary. `.agents/skills/rvv/SKILL.md` is the shared operational policy/runbook for agents.

## Interaction principles

### Goal-oriented

Normal operation should start from an objective such as “produce the best trustworthy season plan from the current workbook” rather than requiring the human to coordinate Stage 1–4 manually. Stage commands remain debugging/escape hatches.

### Evidence before confidence

Capabilities should expose what happened, concrete supporting evidence, uncertainty/problems, artifacts, and valid next actions. Source success is not equivalent to source trustworthiness; candidate score is not a substitute for hard verification.

### Deterministic guardrails

Agent judgment may choose among valid actions and soft trade-offs but cannot bypass hard constraints, schema validation, privacy rules, or publication gates.

### Reproducible/auditable operation

Runs retain the input/source fingerprints, relevant candidate/metric data, structured action outcomes, and concise decision rationale needed for audit/handover. Private model chain-of-thought and sensitive authentication/session material are not persistence requirements.

### Thin adapters

Harness-specific command files may provide UI, browser integration, progress/cancellation, or launch details. They must not maintain an independent copy of Stage 1–4 policy, scheduling semantics, source-validity rules, or publication policy.

## Human escalation

Escalate when work genuinely needs human authority or missing information, for example credentials/MFA, ambiguous policy, an explicit exception, destructive recovery, impossible hard constraints, or external publication approval.

Do not escalate simply because a routine deterministic capability needs another bounded retry/recovery action that is already available and safe.

## Ownership and handover

Routine operation should use club-controlled accounts/assets and documented recovery paths rather than depend on one maintainer's personal account. See [`ownership-and-handover.md`](ownership-and-handover.md).

## Documentation/backlog boundary

This document records product direction, not implementation status. Current operational details belong in maintained docs/code, durable architecture choices belong in ADRs, shared agent policy belongs in `.agents/skills/rvv/SKILL.md`, and unfinished implementation work belongs in GitHub issues.
