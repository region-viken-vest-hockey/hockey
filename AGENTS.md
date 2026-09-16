# Agent instructions

This is the shared, harness-neutral instruction file. Keep always-on repository rules here and keep harness-specific bootstrap files (`CLAUDE.md`, `CODEX.md`, command adapters, etc.) thin. Do not copy shared policy into each harness.

## Instruction loading

Load task-specific guidance only when it is relevant:

- For RVV Miniputt scraping, calendar collection/recovery, season planning, canonical-season maintenance, pipeline operation/debugging, export, review, or publication, read [`.agents/skills/rvv/SKILL.md`](.agents/skills/rvv/SKILL.md).
- When an already generated schedule/export is being handed off as the real club-review or ice-booking baseline and future experiments must not silently replace it, also read [`.agents/skills/rvv-review-baseline/SKILL.md`](.agents/skills/rvv-review-baseline/SKILL.md).
- When the operator says one canonical tournament/ice slot is confirmed, approved, or booked and should be locked, also read [`.agents/skills/rvv-confirm-tournament/SKILL.md`](.agents/skills/rvv-confirm-tournament/SKILL.md).
- When the operator asks to move one canonical tournament to a specific new date/arena/host/time, also read [`.agents/skills/rvv-move-tournament/SKILL.md`](.agents/skills/rvv-move-tournament/SKILL.md).
- For a supported RVV command workflow, load the matching shared procedure under [`.agents/commands/rvv-miniputt/`](.agents/commands/rvv-miniputt/) rather than keeping another copy in a harness adapter.
- Before proposing or making architectural changes, read [`docs/engineering-principles.md`](docs/engineering-principles.md), [`docs/system-architecture.md`](docs/system-architecture.md), and [`docs/README.md`](docs/README.md).
- Use the focused active document linked from `docs/README.md` for task-specific contracts such as workbook/input formats or pipeline behavior.

Harness adapters may point to these files, but must not duplicate their policy. If guidance is only useful for a particular workflow, put it in the relevant shared skill/doc and reference it instead of expanding an always-loaded harness file.

## Source-of-truth order

Use the repository in this order when facts or instructions disagree:

1. **Current code, tests, and controlled inputs** (`input.xlsx` and the relevant source files) define what the system actually accepts and executes.
2. **`.agents/skills/rvv/SKILL.md`** is the canonical shared RVV operational/planning runbook for agents.
3. **`README.md` and active docs listed in `docs/README.md`** explain the current system and operator workflow.
4. **Accepted ADRs** record durable architecture decisions and rationale. They are not step-by-step runbooks.
5. Git history, old issue text and historical season material are context only; never treat them as current instructions.

If an active document contradicts current code or the controlled workbook, fix the active documentation in the same change unless the code/input itself is the bug being corrected.

## RVV Miniputt command surface

`.agents/skills/rvv/SKILL.md` owns shared RVV policy. `.agents/commands/rvv-miniputt/` owns shared command procedures. Claude, Codex, ChatGPT, Pi and future agent harnesses should consume the same files and execute the same repository-local command transport.

The canonical launcher for agent operation is:

```bash
scripts/rvv-miniputt ...
```

It selects the repository environment and forwards to `tournament_scheduler.cli.rvv_cli`. The installed `rvv-miniputt` console script and `python3 -m tournament_scheduler.cli.rvv_cli ...` target the same Python transport; the latter is a low-level developer/test fallback rather than a separate operator interface.

For the checkpoint-reviewed agent flow, use the shared `run` procedure, which calls `scripts/rvv-miniputt run --interactive`, and make decisions only from the returned `DecisionContext`, its `available_actions`, action parameter schema, and decision-action template.

The active harness itself should reason over the returned context. Do not add a harness-local second model call, duplicated decision controller, scheduler loop, semantic-audit implementation, or source-validity policy.

If browser-assisted source recovery is ever needed, any browser-capable harness may perform only the browser/navigation extraction and then return the recovered evidence through the repository-owned `recovery-inject` / `scrape-merge` validation path. No harness-specific scraper implementation is required in this repository.

Do not invoke `tournament_scheduler.pipeline.stageN_*` modules directly from a harness when that bypasses checkpointing, resumption, structured decisions, verification, or run logging. Do not add another root scheduler CLI, interactive wizard, or harness-local orchestration implementation.

## Change ownership

When changing scheduling behavior, input semantics, source validity, export content, or publication rules, update the smallest active document that owns that behavior in the same change.

Shared behavior needed by more than one harness belongs in repository/application code or the shared RVV runbook first. Harness adapters should only expose transport/UI that is genuinely unavailable through the shared command surface.

### Scheduling-rule changes

Before implementing or modifying a scheduling rule, read the **Scheduling-rule implementation map** in [`docs/system-architecture.md`](docs/system-architecture.md) and identify the rule's authoritative owner.

A persistent scheduling invariant must not live only in a baseline generator, one optimizer/search path, a renderer/exporter, or an agent/harness prompt. Put reusable facts/rule math in planner-independent deterministic code, make verification/measurement independent of the generator that proposed the candidate, expose repairs as validated repository actions, and derive reports/HTML from final authoritative state.

For obligations such as hosting, keep **responsibility** separate from **automatic placement**. If the fair/intended host has no trustworthy/legal slot, preserve that host responsibility and surface manual placement; do not silently move the burden to another club merely because its calendar is easier.

### Direct cleanup vs tracked feature work

- Routine non-critical cleanup, refactoring, documentation/test hygiene, and planner-internal cleanup that **does not intentionally change planning semantics** may be implemented directly on `main` when direct-main work is authorized. Do not create a GitHub issue merely as bookkeeping for that cleanup.
- New functionality, intentional planner/rule/behavior changes, new operator workflows, or work that benefits from explicit acceptance criteria/coordination should be tracked in a GitHub issue before implementation.
- If cleanup discovers a real behavior defect or a desired new capability, keep the cleanup small and file a focused issue for the behavior change instead of silently broadening the refactor.

## Repository hygiene

- **GitHub issues are the only live implementation backlog.** Do not add project-local task/backlog/history files such as `.ps-next/`, agent scratch plans, or review notes as a second tracker.
- **Git history is the implementation archive.** Do not keep completed roadmaps, dated investigations, generated architecture reviews, or temporary design notes in `main` once their durable outcome is represented by current code/docs/ADRs.
- **Do not vendor generic personal agent tooling into this repo.** Project-local skills/extensions must be RVV-specific and necessary to operate or maintain this repository.
- **Local settings stay local.** Do not commit machine-specific harness settings, absolute workstation paths, session state, credentials, cookies, or generated caches.
- Keep generated runtime evidence under `.pipeline/`, `export/`, test fixtures, or CI artifacts rather than `docs/`.

## Issue references

Do not put GitHub issue numbers into user-facing reports, rendered HTML, exported files, rules/status text, CLI messages intended for operators, or code comments explaining permanent behavior. Issue numbers age badly and are meaningless without tracker context. Explain the reason or invariant in plain language instead.

Issue/PR numbers are fine in commit messages, PR/issue discussion, and temporary tracker context.
