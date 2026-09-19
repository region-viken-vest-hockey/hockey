# RVV Miniputt: guide

Use this as the internal shared routing guide behind `operate.md`. It is not a separate operator-facing command; the operator should normally invoke only `operate` and describe the desired outcome in natural language.

1. Determine which lifecycle the user's goal belongs to:
   - **initial season creation**: `run`, `status`, `logs`, `calendars`, `scrape`, `scrape-llm`, then audit/review and explicit baseline promotion;
   - **refine a reviewed-but-unpromoted candidate**: `stage3 refine` after the semantic audit finds a localized defect (do not promote/reset/rerun just to repair one finding);
   - **promoted-season maintenance**: `season` for promote/status/approvals/approve/unapprove/move/replan/diff/apply/export;
   - **publication**: `publish` only after the exact current exported revision has passed the semantic audit.
2. Prefer the canonical `season` workflow once `season/<season>/schedule.json` exists. Do not treat ordinary club feedback such as booking approval, a requested move, or bounded replanning as a request to regenerate the season from scratch.
3. Ask only for missing arguments that materially affect the command.
4. Load the matching shared procedure from `.agents/commands/rvv-miniputt/` and execute the repository-local command it specifies.
5. Summarize the result and, when useful, suggest the next canonical RVV action.

Typical promoted-season routing:

- reviewed Stage 4 candidate has a localized audit finding and is not promoted yet -> `stage3 refine`, then re-run the semantic audit over the new export;
- schedule accepted for club review/ice booking -> `season promote`;
- inspect the operational schedule/revision -> `season status`;
- club confirms ice -> `season approve`;
- booking/change request for an approved tournament -> `season unapprove`, then `season move`, then reapprove only after confirmation;
- improve unresolved/unapproved parts without churning booked tournaments -> `season replan`, inspect `season diff`, then verified `season apply`;
- canonical schedule/decision state changed and the result should be reviewed/published -> `season export`, semantic audit, then `publish`.
- reserve/fill/release external guest capacity -> route through the `season` guest capabilities; for policy-level placement choose among repository-generated guest candidates.
- change tournament participants or make room for a team -> prefer the smallest verified roster/capacity repair before moving an otherwise valid placement or broad replanning.
- add/remove a registered team or change the season team set -> update the authoritative controlled registration/input through its supported path, then reconcile/replan around the canonical baseline; if no supported operator capability exists, surface that gap rather than editing canonical schedule JSON.

Do not call internal stage modules directly and do not define new scheduling/source/publication semantics in a harness-local guide or extension.
