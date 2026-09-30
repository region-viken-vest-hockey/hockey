# Agent instructions

This is the repository-wide, **harness-neutral** instruction file. Claude, Codex, ChatGPT, Pi and future harnesses must share repository semantics from here and the routed shared skills/procedures. Harness-specific files are thin bootstrap/transport adapters only; they must not redefine scheduling, source, canonical-season, verification, audit or publication policy.

## Start and route

At a new agent conversation or after context loss, use the shared read-only [verified handover](.agents/commands/rvv-miniputt/handover.md) before assuming season/publication state. Handover is evidence, not authority to mutate, plan or publish.

For any RVV Miniputt scraping, planning, canonical maintenance, export, audit or publication task, read [`.agents/skills/rvv/SKILL.md`](.agents/skills/rvv/SKILL.md), then load only the capability guidance and exact command procedure it routes to.

Before architectural changes, read [engineering principles](docs/engineering-principles.md), [system architecture](docs/system-architecture.md), and the active docs index in [docs/README.md](docs/README.md). Load focused ADRs/docs only when relevant.

Do **not** preload every RVV skill, command or architecture document. Progressive context loading is the repository standard.

## Source-of-truth order

When facts/instructions disagree:

1. current code, tests and controlled inputs define executable behavior;
2. this file defines repository-wide agent behavior;
3. the routed shared RVV/capability skill defines operational policy;
4. active docs/README explain current contracts/workflows;
5. accepted ADRs record durable architecture decisions/rationale;
6. Git history, old issues and historical season artifacts are context only.

If active documentation contradicts current code/input, fix the owning document in the same change unless code/input is the defect being corrected.

## Harness-neutral ownership

Repository behavior needed by more than one harness belongs in repository/application code, shared skills, shared command procedures or docs. Harness adapters may expose genuinely harness-specific bootstrap, transport or UI mechanics, but must not copy or fork policy.

The operator-facing RVV harness surface is intentionally `operate`; shared procedures under `.agents/commands/rvv-miniputt/` and `scripts/rvv-miniputt` own execution. Do not add a harness-local scheduler, verifier, decision controller, semantic-audit implementation, source-validity policy or second root orchestration flow.

## Behavioral defect ownership

Before changing a behavioral defect, identify the violated contract and its authoritative owner. Fix the **lowest canonical owner**, not a compensating caller.

| Defect | Canonical owner |
|---|---|
| wrong/missing/ambiguous source fact | input normalization / source / planning-problem construction |
| wrong legality or invariant | planner-independent domain rule / canonical verifier |
| correct rule but no useful legal mutation | validated action / repair / bounded search provider |
| revision, fingerprint, run/candidate identity, persistence/replay | application/session/store/controller |
| resume/transition/finalization/stage handoff | application/session/controller lifecycle |
| CLI parsing/rendering/stdout/exit code | CLI transport |
| correct state but wrong explanation/report/export | evidence/report/export |
| contextual choice among valid alternatives | active agent via declared repository actions |

If the same semantic identity crosses layers—candidate fingerprint, revision, tournament/rule/objective/run identity—it has one canonical implementation/facade. Consumers call it; they do not reconstruct equivalents.

For a behavioral bug:

```text
reproduce exact failure
-> identify contract + owner
-> add smallest regression at owner boundary
-> fix canonical owner
-> retain integration regression for original path
-> run relevant broader verification
```

Parallel semantic fixes in unrelated layers are a warning that ownership is wrong. Do not encode domain/lifecycle fixes in CLI or harness prose when deterministic repository code can own them.

## Scheduling-rule ownership

Before changing a scheduling rule, use the implementation map in [system architecture](docs/system-architecture.md) and [rule catalog](docs/architecture/rule-catalog.md).

Persistent invariants must not live only in a generator, optimizer path, renderer/exporter or prompt. Keep reusable facts/rule math planner-independent, verification independent from candidate generation, repairs as validated repository actions, and reports derived from final authoritative state.

For hosting, keep responsibility separate from automatic placement. Lack of a trustworthy/legal slot does not silently transfer hosting responsibility to an easier club.

## Published-season production architecture

Determine lifecycle from authoritative canonical state (use the shared handover/lifecycle procedure when not already established). **When a season is published/sealed, production maintenance is the default architecture until an explicit operator-authorized planning reopen or the next initial-planning cycle.**

For booking/calendar reconciliation, participant changes, request constraints, scoped moves, cancellations, audit/export and publication:

- start from the smallest stable production capability facade and load focused implementation/tests on demand;
- preserve one revision-bound canonical mutation flow: authoritative baseline -> candidate in memory -> effective baseline/candidate facts -> classify resolved/unchanged/new/worsened findings -> capability policy -> preview -> atomic commit against same revision -> reload/verify/audit;
- converge verification, reconciliation, mutation, audit/export and publication on one revision-bound effective canonical projection; policy gates remain distinct;
- never create caller-local evidence, verification, grandfathering or acceptability shortcuts;
- keep public capability facades small and focused implementation slices cohesive; avoid abstraction chains that add navigation without ownership;
- do not opportunistically refactor `SeasonPlanner`, Stage 3 or initial-planning algorithms while solving published-season maintenance, and never invoke planner search/replanning merely to make a maintenance mutation pass.

If an ordinary booking/calendar/participant/constraint/move/cancellation change needs broad Stage 3 or `SeasonPlanner` context, stop and re-check the architectural boundary.

Production convergence is tracked by the production architecture issue; planner decomposition remains next-season work. Issue numbers belong in tracker/commit context, not permanent code behavior or user-facing output.

## Design for agent locality

Before a feature/change, identify the canonical owner, stable caller-facing boundary, adjacent responsibilities and focused tests.

- Prefer cohesive modules with one reason to change. Extract focused internals behind an existing stable facade when a file mixes unrelated use cases.
- Preserve public CLI/service/schema/adapter contracts where practical.
- Avoid circular dependencies, generic dumping-ground helpers, duplicated policy and unnecessary one-function abstractions.
- For narrow work, inspect entry point -> owner -> direct dependencies/tests first. Use search/targeted reads instead of loading unrelated large files.
- Do not turn a behavioral fix into a broad rewrite. Characterize behavior before structural extraction and separate discovered semantic defects.
- For refactors compare observable behavior, failures, deterministic outputs, fingerprints/revisions, provenance and write ordering where relevant. Never use live season refresh/export/publication as a refactor test or commit incidental generated-season changes.
- Leave a cohesive localized module alone even when long. Optimize for explicit ownership, safety and reviewability, not line counts.

A good capability facade answers: **what can I do, what is the typed contract, what invariants apply, where is it implemented, and where is it tested?**

## Change tracking

Routine non-critical cleanup/refactoring/docs/test hygiene with no intentional semantics change may go directly to `main` when direct-main work is authorized. Do not create issues merely for bookkeeping.

New functionality, intentional rule/behavior changes, new operator workflows, or coordinated work with acceptance criteria should have a GitHub issue. If cleanup reveals a behavior defect/new capability, keep cleanup narrow and track the behavior separately.

## Repository hygiene

- GitHub issues are the only live implementation backlog. Do not add local backlog/history systems.
- Git history is the implementation archive. Do not retain completed roadmaps, dated investigations or temporary reviews in `main`.
- Do not vendor generic personal agent tooling. Repository skills/extensions must be RVV-specific and necessary.
- Machine-specific settings, credentials, cookies, absolute paths, session state and caches stay local.
- Runtime/generated evidence belongs under the existing pipeline/export/test/CI artifact locations, not durable docs.
- Generated artifacts are derived data; fix authoritative state/code and regenerate rather than patching outputs.
- Do not put GitHub issue numbers in user-facing reports, rendered/exported status text or permanent code comments explaining behavior.

## Instruction ownership

Keep the instruction graph small and deterministic:

- `AGENTS.md`: only repository-wide, always-on harness-neutral behavior and routing;
- `.agents/skills/rvv/SKILL.md`: RVV-common invariants and capability router;
- `.agents/skills/rvv/<capability>/SKILL.md`: lazy-loaded capability invariants;
- `.agents/commands/rvv-miniputt/*.md`: exact shared procedures;
- `docs/` + ADRs: explanation, architecture and rationale;
- code/tests: executable contracts;
- GitHub issues: unfinished work.

Do not duplicate detailed procedure/policy across layers. Link to the authoritative owner. If guidance is irrelevant to most tasks entering a file, move it behind a lazy-load boundary or remove it if code/docs/procedures already own it.
