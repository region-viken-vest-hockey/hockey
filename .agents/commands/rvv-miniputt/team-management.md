# Team management (lazy-loaded)

Load for registration, identity, withdrawal, participation, roster or guest-place requests. Use the relevant `season.md` commands and `tournament-maintenance.md` for affected tournaments.

Classify label-only rename versus one-tournament roster change versus authoritative registration-set change versus genuine season/age-group withdrawal. Label corrections use rename-team dry-run and atomic apply; do not create a different team. Update the controlled registration input through its supported workflow before season-wide reconciliation; do not directly edit canonical roster JSON. Genuine withdrawal must preserve historical participation and use durable withdrawal provenance, not an ad hoc deletion. A one-tournament dropout does not authorize season-wide withdrawal.

For replacements, swaps and guest capacity, preview all affected teams and verify age eligibility, tournament counts, spacing, opponent exposure, hosting, locks and accepted requests. Use canonical atomic operations and explicit narrow regression acceptance only when granted. Reconcile the final eligible pool and regenerated games.
