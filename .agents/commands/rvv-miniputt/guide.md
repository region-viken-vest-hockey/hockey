# RVV Miniputt: guide

Use this as the internal shared routing guide behind `operate.md`. This is the **shared conversational guide procedure for every agent harness**. It is not a separate operator-facing command; the operator should normally invoke only `operate` and describe the desired outcome in natural language.

1. Determine which lifecycle the user's goal belongs to:
   - **initial season creation**: `run`, `status`, `logs`, `calendars`, `scrape`, `scrape-llm`, then audit/review and explicit baseline promotion;
   - **refine a reviewed-but-unpromoted candidate**: `stage3 refine` after the semantic audit finds a localized defect (do not promote/reset/rerun just to repair one finding);
   - **promoted-season maintenance**: `season` for promote/status/approvals/approve/unapprove/move/replan/diff/apply/export;
   - **publication**: `publish` only after the exact current exported revision has passed the semantic audit.
2. Prefer the canonical `season` workflow once `season/<season>/schedule.json` exists. Do not treat ordinary club feedback such as booking approval, a requested move, or bounded replanning as a request to regenerate the season from scratch. Before applying a club-requested move or participant change, inspect `season protections` and `season constraints`, and assign a stable `--request-id`; accepted requests are durable constraints, not disposable optimization hints.
3. Ask only for missing arguments that materially affect the command.
4. Load the matching shared procedure from `.agents/commands/rvv-miniputt/` and execute the repository-local command it specifies.
5. Summarize the result and, when useful, suggest the next canonical RVV action.

Typical promoted-season routing:

- reviewed Stage 4 candidate has a localized audit finding and is not promoted yet -> `stage3 refine`, then re-run the semantic audit over the new export;
- schedule accepted for club review/ice booking -> `season promote`;
- inspect the operational schedule/revision -> `season status`;
- club confirms ice -> `season approve`;
- booking/change request for an approved tournament -> inspect `season protections` and `season constraints`, assign/reuse a stable request id, `season unapprove`, then `season move --request-id <id>`, then reapprove only after confirmation;
- semantic club request (unavailable date/range, minimum gap, opponent avoidance) that is not an exact placement -> `season add-constraint --request-id <id>` first, then search/choose any legal result satisfying all active constraints; if the request needs an unsupported type, surface that capability gap rather than locking an exact placement;
- improve unresolved/unapproved parts without churning booked tournaments -> `season replan`, inspect `season diff`, then verified `season apply`;
- a candidate is hard-valid but would newly place a tournament on `fixed_busy`/untrusted ice or introduce manual/host-confirmation work -> treat it as **not an acceptable automatic repair** (the repository classifies it and refuses the automatic apply); keep searching for a candidate that does not regress, and only use `--allow-manual-placement`/`--allow-host-confirmation` when the operator explicitly requests that exact provisional placement;
- canonical schedule/decision state changed and the result should be reviewed/published -> `season export`, semantic audit, then `publish`.
- reserve/fill/release external guest capacity -> route through the `season` guest capabilities; for policy-level placement choose among repository-generated guest candidates.
- change tournament participants or make room for a team -> inspect `season protections` and `season constraints`; when a one-for-one exchange is appropriate, evaluate `season swap-participants --dry-run --request-id <id>` candidates and require acceptable consequences for **both** teams before applying. Prefer the smallest verified roster/capacity repair before moving an otherwise valid placement or broad replanning.
- a club's own tournaments are clustered and no single move or same-age roster swap is legal -> use the `temporal_clustering` finding with `repair-options`/`search`, which enumerate a bounded `coupled_placement` family (cross-age placement exchange plus same-age roster reselection for a displaced tournament) and commit through `season apply-repair`. Participants never move across age groups; do not conclude the cluster is unavoidable from one bounded search.
- candidate conflicts with an earlier accepted request or an active request constraint -> do **not** auto-release it. First seek another legal candidate. Release only when the newer operator/club request explicitly supersedes the earlier request; record that relationship with `season release-protection`/`season release-constraint --request-id <old-id> --note "superseded by <new-id>"` before applying the replacement change.
- add/remove a registered team or change the season team set -> update the authoritative controlled registration/input through its supported path, then reconcile/replan around the canonical baseline; if no supported operator capability exists, surface that gap rather than editing canonical schedule JSON.

Do not call internal stage modules directly and do not define new scheduling/source/publication semantics in a harness-local guide or extension.
