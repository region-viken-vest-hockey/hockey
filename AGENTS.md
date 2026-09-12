# Agent instructions

## Source-of-truth order

Use the repository in this order when facts or instructions disagree:

1. **Current code, tests, and controlled inputs** (`input.xlsx`) define what the system actually accepts and executes.
2. **`.agents/skills/rvv/SKILL.md`** is the canonical shared operational/planning runbook for agents.
3. **`README.md` and active docs listed in `docs/README.md`** explain the current system and operator workflow.
4. **ADRs** record durable architecture decisions and rationale. They are not step-by-step runbooks.
5. Dated reviews, generated evidence, old roadmaps, issue text, and git history are context only; never treat them as current instructions.

If an active document contradicts current code or the controlled workbook, fix the active documentation in the same change unless the code/input itself is the bug being corrected.

Read these before proposing architecture changes:

- [`docs/engineering-principles.md`](docs/engineering-principles.md)
- [`docs/system-architecture.md`](docs/system-architecture.md)
- [`docs/README.md`](docs/README.md)

## RVV Miniputt command surface

`.agents/skills/rvv/SKILL.md` owns shared RVV policy. Harness command files are deliberately thin adapters and must not copy Stage 1–4 policy or scheduling semantics.

In Pi, `/rvv-miniputt ...` is provided by the Pi extension and should be executed directly there. Pi slash commands are not shell binaries.

Outside Pi, use the harness-local adapter when available or the repository entrypoints:

- `scripts/rvv-miniputt ...`
- `python3 -m tournament_scheduler.cli.rvv_cli ...`

For the checkpoint-reviewed agent flow, use `scripts/rvv-miniputt run --interactive` and make decisions only from the returned `DecisionContext`, its `available_actions`, and its `decision_action_template`.

Do not invoke `tournament_scheduler.pipeline.stageN_*` modules directly when that bypasses checkpointing, resumption, structured decisions, or run logging.

When changing scheduling behavior, input semantics, source validity, or publication rules, update the relevant active documentation and generated rules/report wording so operators and agents see the same policy the code enforces.

The live implementation backlog belongs in GitHub issues. Do not create a second backlog in `docs/`.
