# RVV Miniputt: guide

Use this as the shared non-Pi conversational guide procedure. Pi may provide its own native interactive guide UI, but the underlying command choices remain repository-owned.

1. Determine the user's goal: normally `run`, `status`, `logs`, `calendars`, `scrape`, `scrape-llm`, or `publish`.
2. Ask only for missing arguments that materially affect the command.
3. Load the matching shared procedure from `.agents/commands/rvv-miniputt/` and execute the repository-local command it specifies.
4. Summarize the result and, when useful, suggest the next canonical RVV action.

Do not call internal stage modules directly and do not define new scheduling/source/publication semantics in the guide.
