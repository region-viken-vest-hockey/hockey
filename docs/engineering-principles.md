# Engineering principles

This repository supports a volunteer-maintained hockey planning project with low traffic, limited usage and limited maintainer time. Prefer pragmatic, boring solutions that are easy to understand, operate, debug and hand over.

## Default decision rule

Choose the simplest solution that satisfies the current requirement.

Before introducing a new service, framework, abstraction or workflow, first ask whether the existing repository capabilities can be extended instead.

## Prefer

- Existing repository scripts and command wrappers.
- Small deterministic Python modules.
- Make targets where they simplify repeatable local operations.
- GitHub Actions for validation and suitable headless automation.
- Power Automate for simple Microsoft 365 intake/integration.
- SharePoint, Forms, Excel, CSV and repository files where they are sufficient.
- Incremental improvements over rewrites.
- Explicit typed capability/decision boundaries when several adapters need the same behavior.

## Avoid by default

Do not introduce these unless a demonstrated current requirement makes the simpler approach insufficient:

- microservices;
- message queues;
- custom authentication/token services;
- cloud functions used only to bridge simple integrations;
- Kubernetes/container orchestration;
- databases where files are sufficient;
- speculative extensibility/scalability layers;
- a hosted frontend/worker platform when the repository-operated workflow is enough.

## Quality priorities

Optimize for:

1. correctness;
2. deterministic and reproducible behavior;
3. readability;
4. easy debugging;
5. low operational burden;
6. volunteer maintainability.

Theoretical scale and enterprise completeness are lower priorities unless the repository demonstrates a real need.

## Architecture and ownership

- Facts, hard constraints, verification, reproducible measurement, persistence and safety gates belong in deterministic repository code.
- Contextual soft planning judgment may be delegated to an agent through validated capabilities/actions.
- Behavior used by more than one harness belongs in repository/application code or the shared RVV runbook first; adapters stay thin.
- Generated artifacts are derived data. Correct sources/configuration/code and regenerate rather than maintaining hand-edited generated output.

## Documentation and repository hygiene

Keep the repository's information architecture small:

- root `README.md` explains what the system does, its inputs/outputs and normal operation;
- `docs/system-architecture.md` owns current end-to-end boundaries;
- `docs/rvv-miniputt-pipeline.md` owns Stage 1–4 behavior;
- `docs/rvv-miniputt-input-formats.md` owns the planner input contract;
- ADRs own durable architectural rationale;
- `.agents/skills/rvv/SKILL.md` owns shared agent operating policy;
- GitHub issues own unfinished work.

Do not commit a second task tracker, agent backlog/history, temporary architecture review, completed implementation roadmap, machine-local harness settings, or generic personal agent framework into this project. Git history is the archive for superseded implementation detail.

## Code style

The canonical verification entry point is `scripts/check` / `make check`. Ruff currently enforces the repository's selected error/pyflakes rules, and a custom file-length check acts as a ratchet.

### File size

Keep a Python file under `tournament_scheduler/` at or under 300 lines unless it is already grandfathered by `scripts/file-length-baseline.txt`. Existing over-limit files may not grow beyond their baseline without being split. New/compliant files should stay at or under 300 lines.

Split along responsibility boundaries rather than raising the baseline merely to accommodate growth.

## Expected operating context

Assume, unless the repository shows otherwise:

- a handful of maintainers;
- tens or hundreds of users;
- low traffic;
- infrequent updates;
- no dedicated operations team;
- limited maintenance time.

Explain material trade-offs, but recommend the proportionate solution for this context by default.
