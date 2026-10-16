# Season delivery and health (lazy-loaded)

Load for season reconciliation, health checks, export, audit, publication, republish or rollback. For any publication audit or blocked publish, load `audit-publication.md` and resolve the exact public delta rather than treating season-wide debt as an automatic publication blocker. Follow `season.md`, `republish.md`, `publish.md` and the RVV semantic audit contract as applicable; this guide adds no publication authority.

A health report compares canonical revision, fresh findings, approvals/locks, accepted bookings, calendar coverage, constraints, distribution, export fingerprint, audit and public publication revision. Distinguish absent evidence from negative evidence, planning candidates from accepted bookings, and an incomplete season from a publication blocker. Reconcile mismatches through supported canonical operations, never edits to generated output.

## Presentation regeneration intent

When the operator asks to **regenerate**, **refresh** or **rebuild** `season_plan.html`, the season presentation, HTML/CSS/JS output, renderer output or templates after a presentation-only code change, treat that wording as presentation-regeneration intent. Do not require the operator to restate the safety contract. Default to:

```bash
make season-presentation-export SEASON=<season>
```

That target is the human/harness entrypoint for `scripts/rvv-miniputt season export --season <season> --presentation-only`. It is appropriate only when no canonical schedule or decision change is intended. Do not substitute `run`, `season replan`, planner `apply`, mutating normalization, rescraping or a publication command.

A clean presentation regeneration requires the command report to have `status: PASS`, `canonical_revision_unchanged: true`, `canonical_schedule_or_decisions_changed: false`, and `artifact_parity.status: PASS`. Report the generated export directory/fingerprint and working-tree effects.

For an already published/sealed season, regeneration is also the preview stage of a possible presentation-only republish. After a clean regeneration, automatically resolve the authoritative `active_publication` and compare the generated public presentation with the exact immutable published run. Do not make the operator ask for this comparison manually. Separate and report:

- canonical/operational tournament data changes (placements, participants, booking/status/cancellation/guest facts);
- expected revision/metadata changes; and
- presentation-only changes such as HTML structure, text, filters, CSS, JavaScript behaviour and ordering that does not alter canonical facts.

Present the public-facing delta in a compact comparison table and explicitly state whether tournament data is unchanged. If the request was only to regenerate/refresh/review, stop at the publication authority boundary and ask for the single real-world decision that remains: whether to publish this verified presentation. If the operator already explicitly requested publication, do not pause again merely to repeat that authorization; continue through `republish.md`.

When the operator later confirms publication of the previewed presentation, reuse the exact verified export rather than regenerating it when its canonical revision and export fingerprint are still current. Re-check freshness and the authoritative active publication, run the required in-session semantic audit and publication gates, and publish through the repository-owned boundary. If either the canonical revision or active publication changed since the preview, refresh the comparison before publishing. Do not require the operator to restate the presentation-only safety contract or choose an internal resume command.

A presentation-only regeneration renders once without marking the canonical export fresh or writing any canonical partition, requires the canonical revision to be unchanged before and after, and reports artifact parity against that revision. The entire canonical season tree remains byte-for-byte unchanged whether export freshness starts stale or fresh. It fails rather than reporting a clean regeneration when the revision moved, the rendered artifacts were overtaken, or a canonical fact is not faithfully projected. Byte comparison of old and new HTML is not proof by itself: the preview comparison supplements, rather than replaces, canonical revision checks and artifact parity. The report never authorizes publication, and the semantic audit still runs in-session against the exact export when publication is later requested. Only explicit review, audit, delivery or publication intent materializes export artifacts. Previewing, verifying, rejecting, retrying or committing canonical maintenance should use the revision-bound effective canonical projection and leave `export/` untouched. After a canonical schedule or decision change that is explicitly intended for review, regenerate the canonical export and run semantic audit against the exact revision. For public output, include active tournaments whose exact published interval is either backed by accepted source-backed booking evidence (rendered as **booked**) or a clearly labelled proposal awaiting host confirmation (rendered as proposed, never as booked); a placement whose own evidence explicitly rejects/contradicts it is replanned rather than published, and other unfinished season work does not automatically block incremental publication. Use publication-evidence and the existing publish/republish preflight, preserve audit and approval gates, and verify the resulting public revision. Never publish or roll back without an explicit operator request. If the deployment cannot yet be observed, report publication as unconfirmed rather than successful.
