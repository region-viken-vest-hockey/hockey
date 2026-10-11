# Claude instructions

Read and follow [`AGENTS.md`](AGENTS.md). It is the shared, harness-neutral source for repository instructions and tells you which task-specific skills/docs to load when relevant.

## Claude Code sandbox

- Run pytest and `scripts/check` in the foreground, within the 600-second tool timeout. Background jobs (`&`, `nohup`, `setsid`) were killed silently partway through runs, at 3%, 35% and 97%. If a suite is too slow for one call, split it into file-glob chunks and run each in the foreground.
- Pre-push hooks and the dependency-lock phase need PyPI (`pypi.org`, `files.pythonhosted.org`) and writable caches. Pass those hosts in `allowed_domains` and set the cache variables from `AGENTS.md`. Never bypass the hook with `--no-verify`.

Keep Claude-specific files as transport/UI adapters only. Do not duplicate shared repository, RVV planning, source-validity, scheduling, verification, or publication policy here or under `.claude/commands/`; reference the shared agent-neutral files instead.
