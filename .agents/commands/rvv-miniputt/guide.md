# RVV Miniputt: guide

Use this as the shared non-Pi conversational guide procedure. Pi may provide its own native interactive guide UI, but the underlying command choices remain repository-owned.

1. Determine which lifecycle the user's goal belongs to:
   - **initial season creation**: `run`, `status`, `logs`, `calendars`, `scrape`, `scrape-llm`, then audit/review and explicit baseline promotion;
   - **promoted-season maintenance**: `season` for promote/status/approvals/approve/unapprove/move/replan/diff/apply/export;
   - **publication**: `publish` only after the exact current exported revision has passed the semantic audit.
2. Prefer the canonical `season` workflow once `season/<season>/schedule.json` exists. Do not treat ordinary club feedback such as booking approval, a requested move, or bounded replanning as a request to regenerate the season from scratch.
3. Ask only for missing arguments that materially affect the command.
4. Load the matching shared procedure from `.agents/commands/rvv-miniputt/` and execute the repository-local command it specifies.
5. Summarize the result and, when useful, suggest the next canonical RVV action.

Typical promoted-season routing:

- schedule accepted for club review/ice booking -> `season promote`;
- inspect the operational schedule/revision -> `season status`;
- club confirms ice -> `season approve`;
- booking/change request for an approved tournament -> `season unapprove`, then `season move`, then reapprove only after confirmation;
- improve unresolved/unapproved parts without churning booked tournaments -> `season replan`, inspect `season diff`, then verified `season apply`;
- canonical schedule/decision state changed and the result should be reviewed/published -> `season export`, semantic audit, then `publish`.

Do not call internal stage modules directly and do not define new scheduling/source/publication semantics in the guide.
