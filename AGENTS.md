# Agent instructions

## Source-of-truth order

Use the repository in this order when facts or instructions disagree:

1. **Current code, tests, and controlled inputs** (`input.xlsx` and the relevant source files) define what the system actually accepts and executes.
2. **`.agents/skills/rvv/SKILL.md`** is the canonical shared RVV operational/planning runbook for agents.
3. **`README.md` and active docs listed in `docs/README.md`** explain the current system and operator workflow.
4. **Accepted ADRs** record durable architecture decisions and rationale. They are not step-by-step runbooks.
5. Git history, old issue text and historical season material are context only; never treat them as current instructions.

If an active document contradicts current code or the controlled workbook, fix the active documentation in the same change unless the code/input itself is the bug being corrected.

Read these before proposing architecture changes:

- [`docs/engineering-principles.md`](docs/engineering-principles.md)
- [`docs/system-architecture.md`](docs/system-architecture.md)
- [`docs/README.md`](docs/README.md)

## RVV Miniputt command surface

`.agents/skills/rvv/SKILL.md` owns shared RVV policy. Harness command files are deliberately thin adapters and must not copy Stage 1–4 policy, source-validity rules, scheduling semantics, or publication policy.

In Pi, `/rvv-miniputt ...` is provided by the RVV Pi extension and should be executed directly there. Pi slash commands are not shell binaries.

Outside Pi, use a harness-local adapter when available or the repository entrypoints:

- `scripts/rvv-miniputt ...`
- `python3 -m tournament_scheduler.cli.rvv_cli ...`

For the checkpoint-reviewed agent flow, use `scripts/rvv-miniputt run --interactive` and make decisions only from the returned `DecisionContext`, its `available_actions`, action parameter schema, and decision-action template.

Do not invoke `tournament_scheduler.pipeline.stageN_*` modules directly when that bypasses checkpointing, resumption, structured decisions, verification, or run logging.

## Change ownership

When changing scheduling behavior, input semantics, source validity, export content, or publication rules, update the smallest active document that owns that behavior in the same change.

Shared behavior needed by more than one harness belongs in repository/application code or the shared RVV runbook first. Harness adapters should only expose that capability.

## Repository hygiene

- **GitHub issues are the only live implementation backlog.** Do not add project-local task/backlog/history files such as `.ps-next/`, agent scratch plans, or review notes as a second tracker.
- **Git history is the implementation archive.** Do not keep completed roadmaps, dated investigations, generated architecture reviews, or temporary design notes in `main` once their durable outcome is represented by current code/docs/ADRs.
- **Do not vendor generic personal agent tooling into this repo.** Project-local skills/extensions must be RVV-specific and necessary to operate or maintain this repository.
- **Local settings stay local.** Do not commit machine-specific harness settings, absolute workstation paths, session state, credentials, cookies, or generated caches.
- Keep generated runtime evidence under `.pipeline/`, `export/`, test fixtures, or CI artifacts rather than `docs/`.

## Issue references

Do not put GitHub issue numbers into user-facing reports, rendered HTML, exported files, rules/status text, CLI messages intended for operators, or code comments explaining permanent behavior. Issue numbers age badly and are meaningless without tracker context. Explain the reason or invariant in plain language instead.

Issue/PR numbers are fine in commit messages, PR/issue discussion, and temporary tracker context.
