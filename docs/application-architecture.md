# Application architecture

This document defines the dependency boundary between the RVV Miniputt application layer and its command/harness adapters.

## Layers

```text
Humans / agent harnesses / GitHub Actions
                |
                v
transport adapters (CLI, Pi, Claude, Codex, ChatGPT, OpenCode)
                |
                v
tournament_scheduler.application
                |
                v
domain + pipeline capabilities
                |
                v
infrastructure implementations (filesystem, git/Pages, network, browser)
```

The repository CLI and harness integrations should be consumers of application/domain capabilities, not alternate owners of business policy.

## Dependency rules

1. **Application modules must not import transport layers.** `tournament_scheduler.application` must not import CLI modules, terminal rendering libraries, subprocess orchestration, or harness code.
2. Transport adapters may depend on the application layer and format its results for their environment.
3. Shared RVV policy belongs in `.agents/skills/rvv/`; executable policy belongs in Python. Harness command files should not contain independent Stage 1–4 algorithms, source-readiness exceptions, fairness thresholds, or recovery rules.
4. Browser-capable adapters may perform navigation that the core cannot, but recovered data must flow back into the shared cache/recovery path and Python must make the final readiness decision.
5. External/public mutations stay behind explicit application/CLI authorization gates.

The application layer can use domain and pipeline modules while the migration remains incremental, but new use cases should prefer explicit application services/results over transport-specific control flow.

## Why this boundary exists

Without it, every interface gradually becomes a second implementation: one command decides whether a calendar is acceptable, another copies fairness policy, and a third invents different recovery semantics. That makes correctness depend on which harness the operator happened to use.

The intended model is the opposite: one deterministic implementation with multiple thin interfaces.

## Shared RVV operating layer

For season planning and scraping, `.agents/skills/rvv/SKILL.md` and `.agents/skills/rvv/RUNBOOK.md` are the shared agent-facing contract. `scripts/rvv-miniputt` is the portable executable entry point.

Pi can add live progress and browser recovery; other harnesses can add capabilities unique to their environment. Those integrations are adapters only.

## Structured results

Application operations should expose enough structured state for adapters to render without reparsing human console prose. Depending on the use case this may include:

- status/summary
- evidence/provenance
- generated artifacts
- warnings/problems
- suggested actions
- whether a human decision is required

Stage checkpoints and the operator run manifest are part of this evidence surface.

## Example: adding a new command/use case

Suppose we want to add a command that summarizes current operator health.

1. Put the domain/application behavior in `tournament_scheduler.application` or the appropriate canonical Python capability.
2. Return structured data without terminal styling.
3. Add a CLI adapter that parses arguments, calls the application behavior, and renders the result.
4. If Pi/Claude/Codex need the operation, have their adapters invoke the same repository command/capability instead of reimplementing it.
5. Add tests at the application boundary and thin adapter tests where useful.

The existing family `rvv-miniputt operator questions|answer|promote|health` illustrates the intended direction: the command surface delegates to shared operator state and capabilities rather than owning a separate workflow.

## Browser recovery example

A harness discovers events for a browser-only source:

1. recover the events with the harness-specific browser capability
2. inject them through the shared recovery/cache mechanism
3. normalize/merge shared state where needed
4. rerun Python Stage 2
5. continue only if Python reports the checkpoint as planning-ready

The adapter never decides that a particular missing club is acceptable on its own.

## Testing the boundary

`tests/test_application_architecture.py` statically checks that application modules do not import known transport dependencies. RVV-specific tests separately enforce the shared runbook/thin-adapter and Stage 2 readiness contracts.
