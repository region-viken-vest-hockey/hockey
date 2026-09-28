# Governance and change authority (lazy-loaded)

Load for approvals, locks, accepted changes, durable constraints, supersession, exception acceptance or undo. Consult `season.md` for supported canonical commands and the shared RVV skill for audit boundaries.

Inspect lifecycle, approvals, protections and constraints before any accepted feedback mutation. Assign/reuse a stable request id and preserve provenance. Encode semantic intent with the narrowest supported type; never turn host/arena unavailability into team-wide unavailability or a global ban. Do not silently release earlier requests to fit a candidate. Release only the specific protection/constraint explicitly superseded by a newer authorized request, recording that relationship.

An approved/locked or source-backed booked commitment is not automatically editable. Explicit unapproval, narrow regression acceptance, manual placement/host confirmation, withdrawal reversal, reopening planning and publication are distinct authority decisions. Do not infer them from a general request to improve quality.

For undo, prefer a new verified compensating/superseding canonical action with provenance, considering all intervening dependent changes. Never delete history or restore an old decisions/schedule JSON snapshot. If the repository has no safe reversal operation, surface the capability gap. Report what remains protected and any outstanding authorization.
