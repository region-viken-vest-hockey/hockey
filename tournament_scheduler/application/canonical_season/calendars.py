"""Promoted-season calendar evidence refresh and booking reconciliation."""

from __future__ import annotations

import copy
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.calendar_bookings import (
    BOOKING_AMBIGUOUS,
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_MANUALLY_BOOKED,
    BOOKING_MANUALLY_NOT_BOOKED,
    BOOKING_NOT_CHECKABLE,
    CALENDAR_BOOKING_ASSOCIATIONS_KEY,
    MANUAL_ASSERTION_REVOKED,
    MANUAL_ASSERTION_SCOPES,
    MANUAL_ASSERTION_SUPERSEDED,
    MANUAL_BOOKING_ASSERTIONS_KEY,
    MANUAL_BOOKING_STATUS_CHOICES,
    TOURNAMENT_BOOKING_EVIDENCE_KEY,
    association_findings,
    booking_assessment,
    booking_status_report as _booking_status_report,
    event_covers_tournament_interval,
    event_fingerprint,
    find_event,
    iter_events,
    manual_assertion_for_tournament,
    manual_assertion_projection_status,
    manual_assertion_stale_reasons,
    new_association_record,
    new_booking_evidence_record,
    new_manual_assertion_record,
    valid_active_associations,
    validate_stated_interval,
)
from tournament_scheduler.canonical_baseline import approval_fingerprint
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.club_registry import CLUB_REGISTRY, club_for_source_name
from tournament_scheduler.infrastructure.canonical_calendar_snapshot_archive import (
    calendar_snapshot_content,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.planning_contract import verify_candidate

from .shared import (
    APPROVED_STATUS,
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _resolve_plan_problem,
    _parse_iso_date_for_move,
    _attributable_blockers,
)

# #467: every promoted-season calendar refresh must independently verify that
# tournaments placed during planning at these clubs still show up in the
# freshly rescraped calendar, not just that the refresh bypassed the cache.
# Kept narrow and explicit rather than "all clubs" because this classification
# is evidence surfaced automatically on every refresh, unprompted by an
# operator -- it must stay scoped to the clubs the harness cannot otherwise
# independently verify.
_AUTO_REFRESH_RECONCILE_CLUBS = ("Kongsberg", "Ringerike")

_CALENDAR_PROBLEM_KEYS = (
    "club_busy_dates",
    "club_busy_intervals",
    "club_calendar_status",
    "club_source_integrity",
    "club_coverage_proven",
    "unclassified_calendar_events",
)


def _calendar_problem_payload(problem: Mapping[str, Any] | None) -> dict[str, Any]:
    return {key: copy.deepcopy((problem or {}).get(key)) for key in _CALENDAR_PROBLEM_KEYS}


def _calendar_source_summaries(scrape: Mapping[str, Any], *, fetched_at: str) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for source in scrape.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        payload = {
            "name": source.get("name"),
            "url": source.get("url"),
            "type": source.get("type"),
            "events": source.get("events") or [],
            "blocked": bool(source.get("blocked")),
            "event_count": int(source.get("event_count") or len(source.get("events") or [])),
        }
        summaries.append(
            {
                "name": str(source.get("name") or ""),
                "type": str(source.get("type") or ""),
                "url": str(source.get("url") or ""),
                "event_count": payload["event_count"],
                "blocked": payload["blocked"],
                "fetched_at": str(source.get("scrape_timestamp") or fetched_at),
                "fingerprint": stable_payload_sha256(payload),
            }
        )
    return sorted(summaries, key=lambda item: (item["name"], item["type"], item["url"]))


_SOURCE_POLICY_SCHEMA_VERSION = 1


def _source_policy_entry(source: Mapping[str, Any]) -> dict[str, Any]:
    """Return one source's operator and code-owned policy facts.

    Operator facts (name/type/url) come from ``input.xlsx``; the parser kind,
    trust and event-classification facts come from the code-owned club
    registry so a workbook-only edit can never silently change how a source is
    interpreted.
    """

    name = str(source.get("name") or "").strip()
    club = club_for_source_name(name)
    registry = CLUB_REGISTRY.get(club) if club else None
    entry: dict[str, Any] = {
        "name": name,
        "type": str(source.get("type") or "").strip().lower(),
        "url": str(source.get("url") or "").strip(),
        "club": club,
    }
    if registry is not None:
        entry.update(
            {
                "parser": registry.kind.value,
                "trusted_for_auto_placement": bool(registry.trusted_for_auto_placement),
                "club_controlled_calendar": bool(registry.club_controlled_calendar),
                "location_filter": registry.location_filter,
                "location_exclude_substring": registry.location_exclude_substring,
                "event_classification_rules": [
                    {
                        "pattern": str(rule.pattern),
                        "classification": rule.classification.value,
                        "reason": str(rule.reason or ""),
                    }
                    for rule in registry.event_classification_rules
                ],
            }
        )
    # The broad registry kind above does not describe Stage 2's actual
    # deterministic dispatch; snapshot the effective code-owned strategy so a
    # change to the strategy table is visible as source-policy drift too.
    from tournament_scheduler.pipeline.scraper_strategies import (
        get_deterministic_scraper_type,
        get_strategy,
        needs_llm_agent,
        requires_credentials,
    )

    strategy = get_strategy(club) if club else None
    if strategy is not None:
        entry["engine"] = strategy.engine.value
        entry["deterministic_scraper"] = get_deterministic_scraper_type(strategy)
        entry["requires_credentials"] = requires_credentials(strategy)
        entry["needs_llm_agent"] = needs_llm_agent(strategy)
    return entry


def _source_policy_snapshot(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the normalized source-policy snapshot for an effective config."""

    entries = [
        _source_policy_entry(source)
        for source in config.get("sources") or []
        if isinstance(source, Mapping)
    ]
    entries.sort(key=lambda item: (item["name"], item["type"], item["url"]))
    return {
        "schema_version": _SOURCE_POLICY_SCHEMA_VERSION,
        "start_date": str(config.get("start_date") or ""),
        "end_date": str(config.get("end_date") or ""),
        "age_groups": sorted(str(group) for group in (config.get("age_groups") or [])),
        "operator_confirmed_available_clubs": sorted(
            str(club) for club in (config.get("operator_confirmed_available_clubs") or [])
        ),
        "sources": entries,
    }


def _source_policy_from_evidence(evidence: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the promoted source policy recorded on an evidence record.

    A full policy is used verbatim. A season refreshed before source-policy
    persistence still exposes its per-source name/type/url summaries, which are
    enough to detect URL/parser drift on the next refresh.
    """

    if not isinstance(evidence, Mapping):
        return None
    persisted = evidence.get("source_policy")
    # An explicit ``sources`` list is authoritative even when it is empty: a
    # refresh accepted with zero sources must still protect against a later
    # silent addition, so emptiness is not treated as "no policy recorded".
    if isinstance(persisted, Mapping) and isinstance(persisted.get("sources"), list):
        return copy.deepcopy(dict(persisted))
    sources = evidence.get("sources")
    if not isinstance(sources, list) or not sources:
        return None
    entries = [
        {
            "name": str(source.get("name") or ""),
            "type": str(source.get("type") or "").lower(),
            "url": str(source.get("url") or ""),
        }
        for source in sources
        if isinstance(source, Mapping) and str(source.get("name") or "")
    ]
    if not entries:
        return None
    entries.sort(key=lambda item: (item["name"], item["type"], item["url"]))
    return {"schema_version": _SOURCE_POLICY_SCHEMA_VERSION, "sources": entries}


def _source_policy_signature(entry: Mapping[str, Any]) -> str:
    """Return a stable content signature for one source-policy entry."""

    return stable_payload_sha256(entry)


def _source_policy_changes(
    promoted: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return a bounded before/after diff of promoted vs current source policy.

    Only fields actually present on the promoted policy are compared, so a
    legacy evidence record that recorded only name/type/url does not report a
    spurious change merely because the current policy now also carries the
    code-owned parser/trust facts. Sources are compared as a multiset keyed by
    name: two configured rows may share a name, so a surviving row must never
    mask the addition, removal or change of a same-name sibling.
    """

    if not isinstance(promoted, Mapping):
        return []
    changes: list[dict[str, Any]] = []
    for key in ("start_date", "end_date", "age_groups", "operator_confirmed_available_clubs"):
        if key in promoted and promoted.get(key) != current.get(key):
            changes.append({"field": key, "before": promoted.get(key), "after": current.get(key)})

    def _by_name(policy: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in policy.get("sources") or []:
            if isinstance(entry, Mapping) and entry.get("name"):
                grouped.setdefault(str(entry["name"]), []).append(dict(entry))
        return grouped

    promoted_sources = _by_name(promoted)
    current_sources = _by_name(current)
    for name in sorted(set(promoted_sources) | set(current_sources)):
        before_entries = promoted_sources.get(name, [])
        after_entries = current_sources.get(name, [])
        if not before_entries:
            changes.append({"field": "sources", "source": name, "change": "added", "after": after_entries})
        elif not after_entries:
            changes.append({"field": "sources", "source": name, "change": "removed", "before": before_entries})
        elif len(before_entries) == 1 and len(after_entries) == 1:
            before = before_entries[0]
            after = after_entries[0]
            field_changes = {
                key: {"before": before.get(key), "after": after.get(key)}
                for key in before
                if key != "name" and before.get(key) != after.get(key)
            }
            if field_changes:
                changes.append(
                    {"field": "sources", "source": name, "change": "modified", "fields": field_changes}
                )
        else:
            # Project both sides onto the fields the promoted policy actually
            # recorded, so a legacy name/type/url-only duplicate-name policy
            # does not report drift merely because current entries now also
            # carry registry/strategy fields.
            compared_keys = sorted({key for entry in before_entries for key in entry})
            before_signatures = sorted(
                _source_policy_signature({key: entry.get(key) for key in compared_keys})
                for entry in before_entries
            )
            after_signatures = sorted(
                _source_policy_signature({key: entry.get(key) for key in compared_keys})
                for entry in after_entries
            )
            if before_signatures != after_signatures:
                changes.append(
                    {
                        "field": "sources",
                        "source": name,
                        "change": "multiset_changed",
                        "before_count": len(before_entries),
                        "after_count": len(after_entries),
                        "before": before_entries,
                        "after": after_entries,
                    }
                )
    return changes


def refresh_calendars(
    service,
    *,
    season: str,
    input_path: str | os.PathLike[str] = "input.xlsx",
    work_dir: str | os.PathLike[str] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    allow_missing_sources: bool = False,
    allow_source_policy_change: bool = False,
) -> dict[str, Any]:
    """Refresh promoted-season calendar evidence without changing the schedule.

    The promoted plan/decision state remains the operational truth.  Only
    the verification-context calendar facts are rebuilt from a fresh Stage 2
    scrape, preserving the previous evidence fingerprint in history and
    advancing the canonical-state revision on commit.

    The source configuration that produced the promoted evidence is persisted as
    a source-policy snapshot. A refresh whose current ``input.xlsx`` source
    configuration (URL, parser kind, trust/classification, coverage) differs
    from the promoted policy is refused unless ``allow_source_policy_change`` is
    set, so today's workbook configuration can never silently rewrite the
    meaning of already-promoted evidence. The complete previous calendar payload
    and source policy are archived before replacement, so an audit can
    reconstruct what evidence a refresh replaced even on the first refresh of a
    season promoted before ``calendar_evidence`` existed.
    """

    from tournament_scheduler.pipeline import stage1_config, stage2_scraping
    from tournament_scheduler.pipeline.state import PipelineState
    from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
    from tournament_scheduler.season_maintenance import list_findings

    snapshot = service.load(season)
    schedule = copy.deepcopy(snapshot.schedule)
    decisions = copy.deepcopy(snapshot.decisions)
    plan = copy.deepcopy(schedule.get("plan") or {})
    context = copy.deepcopy(schedule.get("verification_context") or {})
    problem = copy.deepcopy(context.get("problem") or {})
    if not isinstance(problem, dict) or not problem:
        raise SeasonStateError("Canonical season carries no verification-context problem to refresh")

    start = _parse_iso_date_for_move(str(problem.get("start_date") or plan.get("start_date")), "start_date")
    end = _parse_iso_date_for_move(str(problem.get("end_date") or plan.get("end_date")), "end_date")
    before_calendar = _calendar_problem_payload(problem)
    before_fingerprint = stable_payload_sha256(before_calendar)
    before_schedule_fingerprint = schedule_fingerprint(plan)
    before_revision = canonical_state_revision(schedule, decisions)
    fetched_at = _now_iso()
    current_evidence = context.get("calendar_evidence")
    if not isinstance(current_evidence, Mapping):
        current_evidence = None
    promoted_policy = _source_policy_from_evidence(current_evidence)
    promoted_policy_fingerprint = stable_payload_sha256(promoted_policy) if promoted_policy is not None else None

    def _scrape_in(workspace: Path) -> dict[str, Any]:
        state = PipelineState(workspace)
        stage1_config.run(input_path, state, strict=True)
        config = stage1_config.load_effective_config(state, input_path=input_path)
        config["start_date"] = start.isoformat()
        config["end_date"] = end.isoformat()
        # Keep the promoted planning contract's non-source facts authoritative
        # for problem reconstruction; the current workbook supplies sources.
        for key in (
            "teams",
            "age_groups",
            "parallel_games",
            "rounds_per_tournament",
            "round_length_minutes",
            "ice_time_minutes",
            "participation_targets_by_age_group",
        ):
            if key in problem:
                config[key] = copy.deepcopy(problem[key])
        current_policy = _source_policy_snapshot(config)
        source_policy_changes = _source_policy_changes(promoted_policy, current_policy)
        if source_policy_changes and not allow_source_policy_change:
            return {
                "refused": True,
                "source_policy": current_policy,
                "source_policy_changes": source_policy_changes,
            }
        scrape = stage2_scraping.run(
            config,
            state,
            datetime.combine(start, datetime.min.time()),
            datetime.combine(end, datetime.min.time()),
            strict=not allow_missing_sources,
            allow_missing_sources=allow_missing_sources,
            force_refresh=True,
        )
        rebuilt = build_planning_problem(
            config,
            scrape,
            start,
            end,
            waivers=problem.get("operator_waivers") or [],
            canonical_baseline=problem.get("canonical_baseline") if isinstance(problem.get("canonical_baseline"), dict) else None,
        )
        return {
            "scrape": scrape,
            "rebuilt_problem": rebuilt,
            "source_policy": current_policy,
            "source_policy_changes": source_policy_changes,
        }

    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="rvv-calendar-refresh-") as tmp:
            scrape_result = _scrape_in(Path(tmp))
    else:
        workspace = Path(work_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        scrape_result = _scrape_in(workspace)

    if scrape_result.get("refused"):
        result = {
            "season": season,
            "dry_run": bool(dry_run),
            "refused": True,
            "schedule_fingerprint": before_schedule_fingerprint,
            "previous_calendar_fingerprint": before_fingerprint,
            "previous_canonical_state_revision": before_revision,
            "source_policy_fingerprint": stable_payload_sha256(scrape_result["source_policy"]),
            "previous_source_policy_fingerprint": promoted_policy_fingerprint,
            "source_policy_changes": scrape_result["source_policy_changes"],
            "refusal_reasons": ["source configuration changed since promotion"],
        }
        if not dry_run:
            raise SeasonStateError(
                "Refusing calendar evidence refresh: the configured source policy changed since "
                "promotion; pass --accept-source-policy-change to advance the source policy explicitly"
            )
        return result

    scrape = scrape_result["scrape"]
    rebuilt_problem = scrape_result["rebuilt_problem"]
    current_policy = scrape_result["source_policy"]
    source_policy_changes = scrape_result["source_policy_changes"]
    new_problem = copy.deepcopy(problem)
    for key in _CALENDAR_PROBLEM_KEYS:
        new_problem[key] = copy.deepcopy(rebuilt_problem.get(key))
    after_calendar = _calendar_problem_payload(new_problem)
    after_fingerprint = stable_payload_sha256(after_calendar)
    source_summaries = _calendar_source_summaries(scrape, fetched_at=fetched_at)

    previous_snapshot = {
        "schema_version": 1,
        "captured_at": fetched_at,
        "calendar_fingerprint": before_fingerprint,
        "calendar_payload": before_calendar,
        "source_policy": promoted_policy,
        "source_policy_fingerprint": promoted_policy_fingerprint,
        "prior_calendar_evidence": copy.deepcopy(current_evidence),
    }
    snapshot_ref, snapshot_bytes = calendar_snapshot_content(previous_snapshot)

    evidence_record = {
        "schema_version": 1,
        "refreshed_at": fetched_at,
        "refreshed_by": _operator_identity(actor),
        "note": note or "",
        "input_path": str(input_path),
        "dry_run": bool(dry_run),
        "previous_calendar_fingerprint": before_fingerprint,
        "calendar_fingerprint": after_fingerprint,
        "stage2_fingerprint": stable_payload_sha256(scrape),
        "source_count": len(source_summaries),
        "blocked_sources": list(scrape.get("blocked") or []),
        "empty_sources": list(scrape.get("empty_sources") or []),
        "sources": source_summaries,
        "source_policy": current_policy,
        "source_policy_fingerprint": stable_payload_sha256(current_policy),
        "previous_source_policy_fingerprint": promoted_policy_fingerprint,
        "source_policy_changes": source_policy_changes,
        "previous_snapshot": {
            "sha256": snapshot_ref["sha256"],
            "path": snapshot_ref["path"],
            "calendar_fingerprint": before_fingerprint,
            "source_policy_fingerprint": promoted_policy_fingerprint,
            "captured_at": fetched_at,
            "had_prior_calendar_evidence": current_evidence is not None,
            "had_prior_source_policy": promoted_policy is not None,
        },
    }

    preview_context = copy.deepcopy(context)
    preview_context["problem"] = new_problem
    preview_context["problem_fingerprint"] = stable_payload_sha256(new_problem)
    preview_context.setdefault("calendar_evidence_history", [])
    if current_evidence is not None:
        preview_context["calendar_evidence_history"].append(copy.deepcopy(current_evidence))
    preview_context["calendar_evidence"] = evidence_record
    preview_schedule = copy.deepcopy(schedule)
    preview_schedule["verification_context"] = preview_context
    preview_schedule["updated_at"] = fetched_at

    resolved_problem = _resolve_plan_problem(preview_schedule, None, decisions)
    verification = verify_candidate(plan, resolved_problem)
    findings_before = list_findings(season, root=service.store.root)

    # #467: reconcile the freshly scraped Kongsberg/Ringerike calendars against
    # tournaments placed at those clubs during planning. Read-only classification
    # against the just-fetched evidence -- it must never itself write booking
    # decisions or approvals, only surface what the fresh scrape shows so a
    # stale-cache placement mismatch cannot hide inside a "refresh succeeded"
    # result.
    planned_tournament_reconciliation: dict[str, Any] = {"clubs": {}}
    for reconcile_club in _AUTO_REFRESH_RECONCILE_CLUBS:
        hosted = [
            tournament
            for tournament in plan.get("tournaments", []) or []
            if str(tournament.get("host_club") or "") == reconcile_club
        ]
        if not hosted:
            continue
        classified_rows, _unused_records = _classify_club_calendar_bookings(
            plan=plan,
            resolved_problem=resolved_problem,
            decisions=decisions,
            club=reconcile_club,
            actor=actor,
            note=note,
            now=fetched_at,
            source_revision=before_revision,
        )
        # A row needs review when: the calendar classification alone is not
        # confirmed_booked; it is confirmed_booked but conflicts with an active
        # manual booking assertion (#454) -- a valid association coexisting
        # with a later "not booked" assertion must never look green; or it is
        # confirmed_booked from a *pre-existing* association while this club's
        # calendar status has degraded to source_review_required/untrusted --
        # the booking authority still stands, but the degraded source is its
        # own separate concern that must not be silently absorbed into a green
        # row.
        requires_review = [
            row
            for row in classified_rows
            if row["status"] != BOOKING_CONFIRMED_BOOKED
            or row.get("manual_conflict")
            or row.get("source_integrity_concern")
        ]
        planned_tournament_reconciliation["clubs"][reconcile_club] = {
            "classified": classified_rows,
            "count": len(classified_rows),
            "requires_review_count": len(requires_review),
        }
    # The persisted evidence keeps only the deterministic per-club
    # classification. The booking_assessment() crosswalk below explicitly
    # binds itself to one exact canonical_state_revision (#467 P2 review): if
    # it were embedded here, the very act of persisting it would change the
    # canonical revision computed from this content, immediately
    # invalidating its own binding. Compute and return it separately (see
    # below) against whichever revision is actually current once this call
    # returns, instead of baking a stale/circular one into schedule.json.
    evidence_record["planned_tournament_reconciliation"] = copy.deepcopy(planned_tournament_reconciliation)
    reconcile_clubs_present = list(planned_tournament_reconciliation["clubs"].keys())

    result = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": before_fingerprint != after_fingerprint,
        "schedule_fingerprint": before_schedule_fingerprint,
        "previous_calendar_fingerprint": before_fingerprint,
        "calendar_fingerprint": after_fingerprint,
        "source_count": len(source_summaries),
        "blocked_sources": list(scrape.get("blocked") or []),
        "empty_sources": list(scrape.get("empty_sources") or []),
        "sources": source_summaries,
        "source_policy_fingerprint": evidence_record["source_policy_fingerprint"],
        "previous_source_policy_fingerprint": promoted_policy_fingerprint,
        "source_policy_changes": source_policy_changes,
        "previous_snapshot": evidence_record["previous_snapshot"],
        "verification_ok": bool(verification.get("ok")),
        "verification_violations": list(verification.get("violations") or []),
        "manual_external_conflict_placements": list(
            verification.get("manual_external_conflict_placements") or []
        ),
        "planned_tournament_reconciliation": {
            "clubs": planned_tournament_reconciliation["clubs"],
            "assessment": None,
        },
    }
    if dry_run:
        result["canonical_state_revision"] = before_revision
        if reconcile_clubs_present:
            # Nothing is committed in a dry run, so the current canonical
            # revision remains before_revision for as long as this result is
            # read -- the exact-revision binding booking_assessment() promises
            # is accurate here.
            result["planned_tournament_reconciliation"]["assessment"] = booking_assessment(
                problem=resolved_problem,
                plan=plan,
                decisions=decisions,
                canonical_state_revision=before_revision,
                season=season,
                clubs=reconcile_clubs_present,
            )
        return result

    schedule = preview_schedule
    promoted_from = dict(schedule.get("promoted_from") or {})
    promoted_from["export_stale"] = True
    promoted_from["export_stale_reason"] = "calendar evidence refreshed"
    promoted_from["export_stale_at"] = fetched_at
    schedule["promoted_from"] = promoted_from
    decisions["updated_at"] = fetched_at
    decisions["export_state"] = {
        "status": "stale",
        "stale_reason": "calendar_evidence_refreshed",
        "stale_at": fetched_at,
        "requires_fresh_export": True,
        "requires_fresh_audit": True,
        "calendar_fingerprint": after_fingerprint,
    }
    _append_decision_history(
        decisions,
        event="refresh_calendar_evidence",
        tournament_id="",
        actor=actor,
        now=fetched_at,
        note=note,
        details={
            "previous_calendar_fingerprint": before_fingerprint,
            "calendar_fingerprint": after_fingerprint,
            "stage2_fingerprint": evidence_record["stage2_fingerprint"],
            "source_count": len(source_summaries),
            "blocked_sources": list(scrape.get("blocked") or []),
            "source_policy_fingerprint": evidence_record["source_policy_fingerprint"],
            "source_policy_changes": source_policy_changes,
            "previous_snapshot": evidence_record["previous_snapshot"],
            "planned_tournament_reconciliation": {
                club: {"count": entry["count"], "requires_review_count": entry["requires_review_count"]}
                for club, entry in planned_tournament_reconciliation["clubs"].items()
            },
        },
    )
    committed = service._commit(
        snapshot.with_schedule(schedule).with_decisions(decisions),
        extra_evidence={snapshot_ref["path"]: snapshot_bytes},
    )
    findings_after = list_findings(season, root=service.store.root)
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    result["previous_canonical_state_revision"] = before_revision
    if reconcile_clubs_present:
        # Compute the crosswalk against the actually committed state so its
        # exact-revision binding matches what a caller reads immediately
        # afterward (e.g. `season status`) -- computing it beforehand would
        # bind it to before_revision, which is stale the instant this refresh
        # commits (#467 P2 review).
        committed_problem = _resolve_plan_problem(committed.schedule, None, committed.decisions)
        result["planned_tournament_reconciliation"]["assessment"] = booking_assessment(
            problem=committed_problem,
            plan=committed.schedule.get("plan") or {},
            decisions=committed.decisions,
            canonical_state_revision=result["canonical_state_revision"],
            season=season,
            clubs=reconcile_clubs_present,
        )
    result["findings_before"] = {
        "finding_count": findings_before.get("finding_count"),
        "baseline_comparison": findings_before.get("baseline_comparison"),
    }
    result["findings_after"] = {
        "finding_count": findings_after.get("finding_count"),
        "baseline_comparison": findings_after.get("baseline_comparison"),
    }
    result["export_state"] = decisions["export_state"]
    return result


def calendar_booking_candidates(
    service,
    *,
    season: str,
    club: str | None = None,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic candidate tournaments for scraped calendar bookings."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions) or {}
    ice = resolved_problem.get("ice_time_minutes") or {}
    rows: list[dict[str, Any]] = []
    for event in iter_events(resolved_problem):
        if club and str(event.get("club") or "") != club:
            continue
        candidates: list[dict[str, Any]] = []
        for tournament in plan.get("tournaments", []) or []:
            if str(tournament.get("host_club") or "") != str(event.get("club") or ""):
                continue
            if str(tournament.get("date") or "") != str(event.get("date") or ""):
                continue
            start = str(tournament.get("start_time") or "")
            if not start:
                continue
            duration = int((ice.get(str(tournament.get("age_group") or "")) or 0) or 0)
            if duration <= 0:
                continue
            try:
                t_start_h, t_start_m = (int(p) for p in start.split(":", 1))
                e_start_h, e_start_m = (int(p) for p in str(event.get("start") or "").split(":", 1))
                e_end_h, e_end_m = (int(p) for p in str(event.get("end") or "").split(":", 1))
            except ValueError:
                continue
            t_start = t_start_h * 60 + t_start_m
            t_end = t_start + duration
            e_start = e_start_h * 60 + e_start_m
            e_end = e_end_h * 60 + e_end_m
            if t_start < e_end and e_start < t_end:
                candidates.append(
                    {
                        "id": tournament.get("id"),
                        "age_group": tournament.get("age_group"),
                        "host_club": tournament.get("host_club"),
                        "arena": tournament.get("arena"),
                        "date": tournament.get("date"),
                        "start_time": tournament.get("start_time"),
                        "duration_minutes": duration,
                    }
                )
        if candidates:
            rows.append(
                {
                    "calendar_event": {
                        "title": event.get("calendar_event"),
                        "date": event.get("date"),
                        "start": event.get("start"),
                        "end": event.get("end"),
                        "club": event.get("club"),
                        "availability": event.get("availability"),
                        "fingerprint": event.get("fingerprint") or event_fingerprint(event),
                    },
                    "candidate_tournaments": candidates,
                }
            )
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "booking_candidates": rows,
    }


def calendar_booking_assessment(
    service,
    *,
    season: str,
    club: str | None = None,
    problem: dict[str, Any] | None = None,
    date_window_days: int = 7,
) -> dict[str, Any]:
    """Return a read-only, revision/source-bound booking crosswalk for the season.

    This is the #453 assessment boundary: it proposes plausible
    tournament<->event relations (including changed-date/non-overlapping
    candidates, competing mappings, group bookings and unmatched events) but
    never persists an association or claims that calendar absence proves a
    tournament is unbooked. Only the exact canonical revision and each club's
    calendar fingerprint it reports are authoritative; a caller must re-run it
    after any change rather than trusting a cached result.
    """

    snapshot = service.load(season)
    resolved_problem = _resolve_plan_problem(snapshot.schedule, problem, snapshot.decisions)
    return booking_assessment(
        problem=resolved_problem,
        plan=snapshot.schedule.get("plan") or {},
        decisions=snapshot.decisions,
        canonical_state_revision=canonical_state_revision(snapshot.schedule, snapshot.decisions),
        season=season,
        clubs=[club] if club else None,
        date_window_days=date_window_days,
    )


def calendar_booking_findings(service, *, season: str, problem: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = service.load(season)
    resolved_problem = _resolve_plan_problem(snapshot.schedule, problem, snapshot.decisions)
    findings = association_findings(
        problem=resolved_problem,
        plan=snapshot.schedule.get("plan") or {},
        decisions=snapshot.decisions,
    )
    return {"season": season, "findings": findings, "count": len(findings)}


def booking_status_report(service, *, season: str, problem: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = service.load(season)
    resolved_problem = _resolve_plan_problem(snapshot.schedule, problem, snapshot.decisions)
    report = _booking_status_report(
        problem=resolved_problem,
        plan=snapshot.schedule.get("plan") or {},
        decisions=snapshot.decisions,
    )
    report["season"] = season
    report["canonical_state_revision"] = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    return report


def _tournament_interval(tournament: Mapping[str, Any], ice: Mapping[str, Any]) -> tuple[int, int] | None:
    start = str(tournament.get("start_time") or "")
    duration = int((ice.get(str(tournament.get("age_group") or "")) or 0) or 0)
    if duration <= 0 or ":" not in start:
        return None
    try:
        h, m = (int(part) for part in start.split(":", 1))
    except ValueError:
        return None
    t_start = h * 60 + m
    return t_start, t_start + duration


def _event_interval(event: Mapping[str, Any]) -> tuple[int, int] | None:
    try:
        s_h, s_m = (int(part) for part in str(event.get("start") or "").split(":", 1))
        e_h, e_m = (int(part) for part in str(event.get("end") or "").split(":", 1))
    except ValueError:
        return None
    return s_h * 60 + s_m, e_h * 60 + e_m


def _overlaps(tournament: Mapping[str, Any], event: Mapping[str, Any], ice: Mapping[str, Any]) -> bool:
    if str(tournament.get("date") or "") != str(event.get("date") or ""):
        return False
    t_interval = _tournament_interval(tournament, ice)
    e_interval = _event_interval(event)
    if not t_interval or not e_interval:
        return False
    return t_interval[0] < e_interval[1] and e_interval[0] < t_interval[1]


def _approved_placement_locked(decisions: Mapping[str, Any], tournament_id: str) -> bool:
    """Return whether the tournament already has an approved placement lock.

    Public-calendar absence is source-specific follow-up evidence. It must not
    be projected as a negative booking conclusion for an already approved slot,
    but approval-note prose is not parsed as booking authority either.
    """

    record = (decisions.get("decisions") or {}).get(tournament_id) or {}
    if not isinstance(record, Mapping):
        return False
    return str(record.get("status") or "") == APPROVED_STATUS and bool(record.get("placement_locked"))


def _manual_conflict_with_classification(
    *,
    decisions: Mapping[str, Any],
    resolved_problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    classified_status: str,
) -> bool:
    """Return whether an active manual assertion conflicts with *classified_status*.

    ``_classify_club_calendar_bookings`` only looks at calendar-derived evidence
    (associations/overlaps); it does not know about a durable manual booking
    assertion (#454), so a valid calendar association can silently coexist with
    a later active ``manually_not_booked`` assertion. Mirrors the conflict
    semantics already used by :func:`~tournament_scheduler.calendar_bookings.booking_status_report`,
    applied to a freshly computed (not-yet-persisted) classification rather
    than the last persisted calendar evidence.
    """

    tournament_id = str(tournament.get("id") or "")
    manual = manual_assertion_for_tournament(decisions, tournament_id)
    if manual is None:
        return False
    if manual_assertion_stale_reasons(manual, problem=resolved_problem, tournament=tournament):
        # A stale manual assertion itself requires operator re-confirmation.
        return True
    manual_status = manual_assertion_projection_status(manual)
    if manual_status == BOOKING_MANUALLY_NOT_BOOKED and classified_status == BOOKING_CONFIRMED_BOOKED:
        return True
    if manual_status == BOOKING_MANUALLY_BOOKED and classified_status == BOOKING_CONFIRMED_NOT_BOOKED:
        return True
    return False


def _classify_club_calendar_bookings(
    *,
    plan: Mapping[str, Any],
    resolved_problem: Mapping[str, Any],
    decisions: Mapping[str, Any],
    club: str,
    actor: str | None,
    note: str,
    now: str,
    source_revision: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify every tournament hosted by *club* against calendar evidence.

    Pure/read-only: returns ``(rows, records)`` without persisting anything, so
    it can be reused both by the mutating :func:`reconcile_calendar_bookings`
    action and by evidence-only callers (e.g. a promoted-season calendar
    refresh) that must never alter booking decisions.

    The classification is evidence, not booking proof.  A lone busy event that
    merely overlaps a tournament is recorded as ``ambiguous`` so the operator /
    harness can own the semantic match through ``confirm-calendar-booking``;
    only an already-valid explicit association is reported ``confirmed_booked``.

    Absence of an overlapping event is an observation about the *current*
    canonical interval, not a booking outcome for the tournament: the booking
    may have moved, the calendar may be incomplete, or attribution may be
    unresolved.  It is therefore recorded as ``ambiguous`` for every approval
    state, and only explicit source-supported negative/rejection evidence may
    ever assert ``confirmed_not_booked``.
    """

    ice = resolved_problem.get("ice_time_minutes") or {}
    status = str((resolved_problem.get("club_calendar_status") or {}).get(club) or "")
    trustworthy = status == "known"
    events = [event for event in iter_events(resolved_problem) if str(event.get("club") or "") == club]
    confirmed_by_tournament: dict[str, Mapping[str, Any] | None] = {}
    for record in valid_active_associations(decisions, problem=resolved_problem, plan=plan):
        tournament_id = str(record.get("tournament_id") or "")
        if tournament_id:
            confirmed_by_tournament[tournament_id] = find_event(
                resolved_problem, str(record.get("event_fingerprint") or "")
            )
    resolved_actor = _operator_identity(actor)
    rows: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for tournament in plan.get("tournaments", []) or []:
        if str(tournament.get("host_club") or "") != club:
            continue
        tournament_id = str(tournament.get("id") or "")
        if tournament_id in confirmed_by_tournament:
            # Only an explicit operator/harness-validated association is proof that
            # an occupied interval is this tournament; raw overlap is not.
            booking_status = BOOKING_CONFIRMED_BOOKED
            matched_event = confirmed_by_tournament[tournament_id]
            reason = "explicit_calendar_booking_association"
        elif not trustworthy:
            booking_status = BOOKING_NOT_CHECKABLE
            matched_event = None
            reason = f"calendar_status:{status or 'missing'}"
        else:
            overlaps = [event for event in events if _overlaps(tournament, event, ice)]
            if len(overlaps) == 1:
                # Exactly one busy event overlaps, but occupancy is not proof that
                # the event is this RVV tournament. Keep the candidate visible and
                # require an explicit `confirm-calendar-booking` match.
                booking_status = BOOKING_AMBIGUOUS
                matched_event = overlaps[0]
                reason = "single_overlapping_event_requires_confirmation"
            elif len(overlaps) == 0:
                matched_event = None
                # No event covers the *current* canonical interval. That is a
                # statement about this slot, not proof that the tournament is
                # unbooked elsewhere. Keep it as ambiguity that requires review
                # instead of a final negative; a changed slot, incomplete source
                # or incomplete attribution must remain visible.
                booking_status = BOOKING_AMBIGUOUS
                if _approved_placement_locked(decisions, tournament_id):
                    reason = "approved_placement_without_calendar_evidence"
                else:
                    reason = "no_covering_event_for_current_slot"
            else:
                booking_status = BOOKING_AMBIGUOUS
                matched_event = None
                reason = "multiple_overlapping_events"
        record = new_booking_evidence_record(
            tournament=tournament,
            status=booking_status,
            problem=resolved_problem,
            actor=resolved_actor,
            note=note,
            checked_at=now,
            source_revision=source_revision,
            event=matched_event,
            reason=reason,
        )
        records.append(record)
        manual_conflict = _manual_conflict_with_classification(
            decisions=decisions,
            resolved_problem=resolved_problem,
            tournament=tournament,
            classified_status=booking_status,
        )
        rows.append(
            {
                "tournament_id": tournament.get("id"),
                "status": booking_status,
                "reason": reason,
                "event_fingerprint": record.get("event_fingerprint"),
                "manual_conflict": manual_conflict,
                # A pre-existing valid association is checked *before* the
                # trustworthy gate above, so it can still report
                # confirmed_booked even when this club's calendar status has
                # since degraded to source_review_required/untrusted. The
                # booking authority is preserved (an association is not
                # invalidated by a later bad scrape), but the degraded source
                # must stay visible as its own concern rather than silently
                # making the row look fully green.
                "source_status": status,
                "source_integrity_concern": not trustworthy,
            }
        )
    return rows, records


def reconcile_calendar_bookings(
    service,
    *,
    season: str,
    club: str,
    actor: str | None = None,
    note: str = "",
    problem: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Classify every hosted tournament for one club against current calendar evidence.

    See :func:`_classify_club_calendar_bookings` for the classification rules;
    this action additionally persists the classification as tournament booking
    evidence unless ``dry_run`` is set.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions) or {}
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    rows, records = _classify_club_calendar_bookings(
        plan=plan,
        resolved_problem=resolved_problem,
        decisions=decisions,
        club=club,
        actor=actor,
        note=note,
        now=now,
        source_revision=canonical_state_revision(schedule, decisions),
    )
    result = {"season": season, "club": club, "dry_run": dry_run, "classified": rows, "count": len(rows)}
    if dry_run:
        return result
    updated = dict(decisions)
    prior = [dict(record) for record in updated.get(TOURNAMENT_BOOKING_EVIDENCE_KEY) or [] if not (isinstance(record, Mapping) and str(record.get("tournament_id") or "") in {str(r.get("tournament_id") or "") for r in records})]
    updated[TOURNAMENT_BOOKING_EVIDENCE_KEY] = prior + records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="reconcile_calendar_bookings",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={"club": club, "classified": rows},
    )
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    result["booking_status"] = _booking_status_report(problem=resolved_problem, plan=plan, decisions=committed.decisions)
    return result


def confirm_calendar_booking(
    service,
    *,
    season: str,
    event_fingerprint: str,
    tournament_id: str,
    actor: str | None = None,
    note: str = "",
    problem: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Bind one scraped calendar event to one canonical tournament and approve it."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    base_problem = _resolve_plan_problem(schedule, problem, decisions)
    event = find_event(base_problem, event_fingerprint)
    if event is None:
        raise SeasonStateError(f"Unknown calendar event fingerprint: {event_fingerprint}")
    tournament = next(
        (t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None
    )
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    diagnostics: list[str] = []
    if str(event.get("club") or "") != str(tournament.get("host_club") or ""):
        diagnostics.append("host_mismatch")
    if str(event.get("date") or "") != str(tournament.get("date") or ""):
        diagnostics.append("date_mismatch")
    elif not event_covers_tournament_interval(event, tournament, base_problem):
        diagnostics.append("interval_mismatch")
    if diagnostics:
        raise SeasonStateError("Calendar booking is not compatible with tournament: " + ", ".join(diagnostics))

    for record in decisions.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []:
        if not isinstance(record, Mapping) or record.get("status", "active") != "active":
            continue
        if str(record.get("event_fingerprint") or "") != event_fingerprint:
            continue
        existing_tournament_id = str(record.get("tournament_id") or "")
        if existing_tournament_id and existing_tournament_id != tournament_id:
            raise SeasonStateError(
                "Calendar event already has an active tournament association; "
                "release it before rebinding"
            )

    resolved_actor = _operator_identity(actor)
    assoc = new_association_record(
        event=event,
        tournament=tournament,
        actor=resolved_actor,
        note=note,
        source_revision=canonical_state_revision(schedule, decisions),
        problem=base_problem,
    )
    updated = dict(decisions)
    records = [
        dict(record)
        for record in (updated.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or [])
        if not (
            str(record.get("event_fingerprint") or "") == event_fingerprint
            and str(record.get("tournament_id") or "") == tournament_id
        )
    ]
    records.append(assoc)
    updated[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = records
    checked_at = _now_iso()
    evidence = new_booking_evidence_record(
        tournament=tournament,
        status=BOOKING_CONFIRMED_BOOKED,
        problem=base_problem,
        actor=resolved_actor,
        note=note,
        checked_at=checked_at,
        source_revision=canonical_state_revision(schedule, decisions),
        event=event,
        reason="operator_confirmed_calendar_booking_association",
    )
    prior_evidence = [
        dict(record)
        for record in updated.get(TOURNAMENT_BOOKING_EVIDENCE_KEY) or []
        if not (isinstance(record, Mapping) and str(record.get("tournament_id") or "") == tournament_id)
    ]
    updated[TOURNAMENT_BOOKING_EVIDENCE_KEY] = prior_evidence + [evidence]

    temp_problem = _resolve_plan_problem(schedule, base_problem, updated)
    verification = verify_candidate(plan, temp_problem) if temp_problem else verify_candidate(plan)
    hard_blockers, unresolved_blockers = _attributable_blockers(verification, tournament_id)
    blockers = hard_blockers + unresolved_blockers
    if blockers:
        messages = "; ".join(str(blocker.get("message") or blocker.get("code")) for blocker in blockers)
        raise SeasonStateError(f"Refusing to confirm booking for {tournament_id}: {messages}")

    approved_at = checked_at
    tournament_fingerprint = approval_fingerprint(tournament)
    previous = dict(decisions["decisions"].get(tournament_id, {}))
    record = dict(previous)
    record.update(
        {
            "status": APPROVED_STATUS,
            "placement_locked": True,
            "participants_locked": False,
            "approved_fingerprint": tournament_fingerprint,
            "approved_at": approved_at,
            "approved_by": resolved_actor,
            "note": note,
        }
    )
    record.pop("stale_at", None)
    record.pop("stale_reason", None)
    record.pop("unapproved_at", None)
    record.pop("unapproved_by", None)
    updated["decisions"] = dict(updated.get("decisions", {}))
    updated["decisions"][tournament_id] = record
    updated["updated_at"] = approved_at
    _append_decision_history(
        updated,
        event="confirm_calendar_booking",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=approved_at,
        tournament_fingerprint=tournament_fingerprint,
        previous_fingerprint=previous.get("approved_fingerprint"),
        note=note,
        details={"event_fingerprint": event_fingerprint, "calendar_event": event.get("calendar_event")},
    )
    if dry_run:
        return {"season": season, "dry_run": True, "association": assoc, "approved": record}
    committed = service._commit(snapshot.with_decisions(updated))
    return {
        "season": season,
        "dry_run": False,
        "association": assoc,
        "approved": committed.decisions.get("decisions", {}).get(tournament_id),
        "canonical_state_revision": canonical_state_revision(committed.schedule, committed.decisions),
    }


def release_calendar_booking(
    service,
    *,
    season: str,
    event_fingerprint: str,
    tournament_id: str | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Release an active event->tournament association before rebinding."""

    snapshot = service.load(season)
    decisions = snapshot.decisions
    resolved_actor = _operator_identity(actor)
    now = _now_iso()
    updated = dict(decisions)
    records: list[dict[str, Any]] = []
    released: list[dict[str, Any]] = []
    for record in updated.get(CALENDAR_BOOKING_ASSOCIATIONS_KEY) or []:
        if not isinstance(record, Mapping):
            continue
        row = dict(record)
        matches = str(row.get("event_fingerprint") or "") == event_fingerprint and row.get("status", "active") == "active"
        if tournament_id is not None:
            matches = matches and str(row.get("tournament_id") or "") == tournament_id
        if matches:
            row["status"] = "released"
            row["released_at"] = now
            row["released_by"] = resolved_actor
            row["release_note"] = note or ""
            released.append(row)
        records.append(row)
    if not released:
        raise SeasonStateError("No active calendar booking association matched the release request")
    updated[CALENDAR_BOOKING_ASSOCIATIONS_KEY] = records
    updated["updated_at"] = now
    for row in released:
        _append_decision_history(
            updated,
            event="release_calendar_booking",
            tournament_id=str(row.get("tournament_id") or ""),
            actor=resolved_actor,
            now=now,
            note=note,
            details={"event_fingerprint": event_fingerprint, "association_id": row.get("id")},
        )
    result = {"season": season, "dry_run": dry_run, "released": released}
    if dry_run:
        return result
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    return result


def _manual_assertion_matches_existing(
    existing: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    *,
    reference: str,
) -> bool:
    """Return whether a repeat assertion is the same deliberate statement."""

    if existing is None:
        return False
    return (
        str(existing.get("booking_status") or "") == str(candidate.get("booking_status") or "")
        and str(existing.get("source_scope") or "tournament") == str(candidate.get("source_scope") or "tournament")
        and dict(existing.get("tournament_facts") or {}) == dict(candidate.get("tournament_facts") or {})
        and dict(existing.get("asserted_interval") or {}) == dict(candidate.get("asserted_interval") or {})
        and dict(existing.get("stated_interval") or {}) == dict(candidate.get("stated_interval") or {})
        and str(existing.get("reference") or "") == reference
    )


def set_manual_booking_assertion(
    service,
    *,
    season: str,
    tournament_id: str,
    booking_status: str,
    actor: str | None = None,
    note: str = "",
    reference: str = "",
    source_scope: str = "tournament",
    stated_start: str | None = None,
    stated_end: str | None = None,
    expected_revision: str | None = None,
    supersede: bool = False,
    problem: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record an explicit operator/club booking assertion for one tournament.

    The assertion is durable, revision-bound source evidence rather than a
    scrape result.  It never changes the schedule, approval state or
    participants; the booking-status projection reports it as
    ``manually_booked``/``manually_not_booked`` and a routine calendar
    reconcile/refresh cannot erase or demote it.  A material canonical change
    (date/start/duration/host/arena) invalidates it until the operator confirms
    the new slot again; a stale assertion is replaced directly (retaining its
    audit history) rather than requiring ``--supersede``, which is reserved for
    changing the conclusion about the same still-current slot. Repeating the
    identical assertion is a no-op; changing it requires an explicit
    ``supersede`` with a reason. Positive confirmations must carry a traceable
    source reference or rationale.
    """

    if booking_status not in MANUAL_BOOKING_STATUS_CHOICES:
        raise SeasonStateError(
            "Unknown manual booking status: "
            f"{booking_status!r}; expected one of {', '.join(MANUAL_BOOKING_STATUS_CHOICES)}"
        )
    if source_scope not in MANUAL_ASSERTION_SCOPES:
        raise SeasonStateError(
            f"Unknown manual assertion source scope: {source_scope!r}; "
            f"expected one of {', '.join(MANUAL_ASSERTION_SCOPES)}"
        )
    try:
        stated_interval = validate_stated_interval(stated_start, stated_end) or None
    except ValueError as exc:
        raise SeasonStateError(str(exc)) from exc

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    current_revision = canonical_state_revision(schedule, decisions)
    if expected_revision and expected_revision != current_revision:
        raise SeasonStateError(
            f"Stale canonical revision: expected {expected_revision}, current is {current_revision}"
        )
    plan = schedule["plan"]
    tournament = next(
        (t for t in plan.get("tournaments", []) or [] if str(t.get("id")) == tournament_id),
        None,
    )
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if tournament.get("cancelled"):
        raise SeasonStateError(
            f"Tournament {tournament_id} is cancelled and cannot carry a booking assertion"
        )
    if not (str(reference).strip() or str(note).strip()):
        raise SeasonStateError(
            "A manual booking assertion requires a traceable source reference (--reference) "
            "or rationale (--note)"
        )
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    existing = manual_assertion_for_tournament(decisions, tournament_id)
    existing_stale_reasons = (
        manual_assertion_stale_reasons(existing, problem=resolved_problem, tournament=tournament)
        if existing is not None
        else []
    )
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    candidate = new_manual_assertion_record(
        tournament=tournament,
        booking_status=booking_status,
        problem=resolved_problem,
        actor=resolved_actor,
        note=note,
        reference=reference,
        source_scope=source_scope,
        stated_interval=stated_interval,
        asserted_at=now,
        source_revision=current_revision,
    )
    if _manual_assertion_matches_existing(existing, candidate, reference=reference):
        return {
            "season": season,
            "dry_run": bool(dry_run),
            "changed": False,
            "idempotent": True,
            "tournament_id": tournament_id,
            "assertion": existing,
            "canonical_state_revision": current_revision,
        }
    if existing is not None:
        if not existing_stale_reasons and not supersede:
            raise SeasonStateError(
                f"Tournament {tournament_id} already has an active manual booking assertion "
                f"({existing.get('booking_status')!r}); repeat it unchanged or pass --supersede "
                "with a reason to replace it"
            )
        if not existing_stale_reasons and not note:
            raise SeasonStateError("Superseding a manual booking assertion requires a --note reason")
        candidate["supersedes"] = str(existing.get("id") or "")
        candidate["reconfirms_stale_reasons"] = list(existing_stale_reasons)

    updated = dict(decisions)
    records = [
        dict(record)
        for record in updated.get(MANUAL_BOOKING_ASSERTIONS_KEY) or []
        if isinstance(record, Mapping)
    ]
    if existing is not None:
        existing_id = str(existing.get("id") or "")
        supersede_reason = note or (
            "re-confirmed after canonical slot change: " + ", ".join(existing_stale_reasons)
            if existing_stale_reasons
            else "superseded"
        )
        for record in records:
            if str(record.get("id") or "") == existing_id:
                record["status"] = MANUAL_ASSERTION_SUPERSEDED
                record["superseded_at"] = now
                record["superseded_by"] = candidate["id"]
                record["supersede_reason"] = supersede_reason
                if existing_stale_reasons:
                    record["superseded_stale_reasons"] = list(existing_stale_reasons)
    records.append(candidate)
    updated[MANUAL_BOOKING_ASSERTIONS_KEY] = records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="set_manual_booking_assertion",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "booking_status": booking_status,
            "authority": candidate["authority"],
            "source_scope": candidate["source_scope"],
            "reference": reference or "",
            "asserted_interval": candidate["asserted_interval"],
            "stated_interval": candidate["stated_interval"],
            "supersedes": candidate.get("supersedes") or "",
            "reconfirms_stale_reasons": candidate.get("reconfirms_stale_reasons") or [],
        },
    )
    result: dict[str, Any] = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": True,
        "idempotent": False,
        "tournament_id": tournament_id,
        "assertion": candidate,
        "previous_assertion": existing,
        "canonical_state_revision": current_revision,
    }
    result["booking_status"] = _booking_status_report(
        problem=resolved_problem,
        plan=plan,
        decisions=updated,
    )
    if dry_run:
        return result
    committed = service._commit(snapshot.with_decisions(updated))
    committed_problem = _resolve_plan_problem(committed.schedule, problem, committed.decisions)
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    result["booking_status"] = _booking_status_report(
        problem=committed_problem,
        plan=committed.schedule.get("plan") or {},
        decisions=committed.decisions,
    )
    return result


def clear_manual_booking_assertion(
    service,
    *,
    season: str,
    tournament_id: str,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Revoke one active manual booking assertion without touching the schedule."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    current_revision = canonical_state_revision(schedule, decisions)
    tournament = next(
        (t for t in schedule["plan"].get("tournaments", []) or [] if str(t.get("id")) == tournament_id),
        None,
    )
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    existing = manual_assertion_for_tournament(decisions, tournament_id)
    if existing is None:
        return {
            "season": season,
            "dry_run": bool(dry_run),
            "changed": False,
            "tournament_id": tournament_id,
            "revoked": None,
            "canonical_state_revision": current_revision,
        }
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    updated = dict(decisions)
    records = [
        dict(record)
        for record in updated.get(MANUAL_BOOKING_ASSERTIONS_KEY) or []
        if isinstance(record, Mapping)
    ]
    existing_id = str(existing.get("id") or "")
    revoked: dict[str, Any] | None = None
    for record in records:
        if str(record.get("id") or "") == existing_id:
            record["status"] = MANUAL_ASSERTION_REVOKED
            record["revoked_at"] = now
            record["revoked_by"] = resolved_actor
            record["revoke_reason"] = note
            revoked = record
    updated[MANUAL_BOOKING_ASSERTIONS_KEY] = records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="clear_manual_booking_assertion",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        note=note,
        details={"assertion_id": existing_id},
    )
    result: dict[str, Any] = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": True,
        "tournament_id": tournament_id,
        "revoked": revoked,
        "previous_assertion": existing,
        "canonical_state_revision": current_revision,
    }
    if dry_run:
        return result
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    return result
