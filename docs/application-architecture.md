# RVV Miniputt application architecture

This document defines the current application-layer dependency rules. It is intentionally structural; operational policy belongs in `.agents/skills/rvv/SKILL.md`, while the higher-level ownership boundary is documented in ADR 0002.

## Dependency rules

The dependency direction is:

```text
interfaces/adapters (CLI, harnesses, GitHub Actions, HTTP/UI)
        ↓
application use cases and DTOs
        ↓
domain policy + injectable ports
        ↓
infrastructure implementations (filesystem, git/Pages, network, keyring)
```

Rules:

1. Adapters own parsing, rendering, environment integration, and exit-code mapping. They do not duplicate orchestration or business rules.
2. Application modules expose typed use cases/results rather than terminal output, HTTP responses, subprocess behavior, or harness-specific objects.
3. **Application modules must not import** transport/rendering modules such as `tournament_scheduler.cli`, desktop/UI adapters, Rich rendering, or harness code.
4. Deterministic business rules and validation live below the adapter layer. Contextual soft judgment is exposed through decision contexts/actions rather than embedded independently in each adapter.
5. Filesystem, git, network, browser, and secret-store operations should sit behind explicit infrastructure boundaries where practical so application behavior remains testable.
6. New behavior needed by more than one adapter should first become a repository/application capability; adapters then expose that capability rather than shelling out to each other.

Architecture tests enforce the important forbidden-import boundaries. Extend those tests when a new adapter or application package creates another dependency edge that could accidentally reverse the direction above.

## Current application surface

The application layer includes typed decision/operator capabilities used by the CLI and harnesses, including durable operator-state operations and the `DecisionContext` / `DecisionAction` boundary. The exact set of functions will evolve; this document defines the dependency rule rather than maintaining a duplicate function inventory.

The repository CLI remains a supported adapter and command surface. It may render human-readable Norwegian text or JSON, but policy shared with other harnesses should live in the application/domain layer or shared RVV runbook as appropriate.

## Example: adding a cross-adapter capability

Suppose operators need a new “show publication status” capability:

1. Define a typed application request/result such as `PublicationStatusRequest` / `PublicationStatus`.
2. Implement an application use case that returns the typed result and keeps external I/O behind explicit infrastructure calls/ports.
3. Add application tests for the behavior without depending on a terminal, browser, or real Git remote.
4. Wire the CLI, harnesses, or UI adapters to the same capability.
5. Keep adapter-specific rendering and argument parsing in the adapter only.

This keeps one implementation of the behavior while allowing multiple operator surfaces.
