# Codex instructions

Read and follow [`AGENTS.md`](AGENTS.md) first. It defines the shared project rules, source-of-truth order, and command boundaries.

For RVV Miniputt scraping, calendar generation, season planning, or pipeline debugging, read `.agents/skills/rvv/SKILL.md` and use the Codex adapter under `.codex/commands/rvv-miniputt/` or the repository entrypoints:

```bash
scripts/rvv-miniputt ...
python3 -m tournament_scheduler.cli.rvv_cli ...
```

Pi `/rvv-miniputt ...` commands are extension commands, not shell binaries. Never run them through a shell.

Codex-specific command files are transport adapters only. They must not define independent Stage 1–4 policy, scheduling semantics, source-validity rules, or publication safety behavior.
