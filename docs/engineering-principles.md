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

## Defect repair discipline

Treat every behavioral bug as a violated contract with one canonical owner. Diagnose ownership before editing code.

Default ownership categories are:

- facts/normalization -> input or planning-problem construction;
- legality/invariants -> planner-independent domain rule and canonical verifier;
- available legal mutations -> action/repair/search provider;
- identity/revision/fingerprint/persistence/replay -> application session/store/controller;
- transition/resume/finalization/stage handoff -> application lifecycle/controller;
- argument/rendering/process behavior -> CLI transport;
- explanation/audit/report/export projection -> evidence/report/export;
- contextual choice among valid alternatives -> agent through declared repository actions.

Fix the lowest canonical owner. Do not compensate for an upstream defect in a downstream caller simply because that is where the symptom is visible.

Cross-layer identities must not have multiple implementations. Candidate normalization/fingerprint, candidate revision, run identity, durable tournament identity, rule identity and similar values need one canonical implementation or facade; all consumers derive from that source.

For behavioral fixes, prefer this sequence:

```text
reproduce
-> classify owner
-> add an owner-boundary regression
-> fix the owner
-> exercise the originally failing integration path
-> run broader verification appropriate to the change
```

A bug fix that starts accumulating semantic patches in several unrelated modules is a signal to stop and reassess ownership. Prefer one corrected contract with thin callers over synchronized compensating fixes.

Do not change a domain rule to solve lifecycle state, lifecycle state to solve a domain rule, or harness/CLI instructions to mask a repository-code defect.

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

The canonical verification entry point is `scripts/check` / `make check`. Ruff currently enforces the repository's selected error/pyflakes rules.

### Code design

Use Clean Code and SOLID principles as pragmatic design heuristics, not as reasons to add abstraction for its own sake.

- Prefer cohesive functions, classes and modules with one clear responsibility and one main reason to change.
- Keep dependency direction explicit: domain/planning rules should not depend on CLI, harness, rendering or infrastructure details.
- Prefer simple functions and composition over inheritance, framework patterns, factories or interfaces that do not solve a current coupling/testability problem.
- Keep business rules in one canonical implementation. Do not duplicate scheduling, validation, source or export policy across planner paths, adapters or renderers.
- Use explicit typed boundaries when they clarify ownership between subsystems, especially when multiple callers or harnesses share the same capability.
- Keep functions/classes/modules small enough to understand and test in isolation. When a module accumulates unrelated responsibilities, split it along responsibility/domain boundaries.
- Avoid god modules/classes, hidden global coupling, long parameter plumbing that obscures ownership, and helpers that mix domain decisions with I/O or presentation.
- Refactor incrementally around behavior-preserving seams. Prefer extracting a clear responsibility over broad rewrites.
- Do not introduce speculative abstractions, one-class-per-file ceremony, needless indirection, or "clean code" refactors that make the execution path harder to follow.

### File size

There is no hard per-file line limit. Split modules along responsibility boundaries when it genuinely improves clarity, rather than to satisfy an arbitrary length budget.

## Expected operating context

Assume, unless the repository shows otherwise:

- a handful of maintainers;
- tens or hundreds of users;
- low traffic;
- infrequent updates;
- no dedicated operations team;
- limited maintenance time.

Explain material trade-offs, but recommend the proportionate solution for this context by default.
