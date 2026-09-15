"""Renders manual_schedule.html -- the "Må planlegges manuelt" view."""

from __future__ import annotations

import html as _html

from ..models import SeasonPlan
from ..html.data_computation import (
    ICON_BAR_CHART,
    ICON_CALENDAR,
    ICON_CLIPBOARD,
    ICON_USERS,
    ICON_WARNING,
    fmt_date,
    season_label,
)
from ..html.templates import MANUAL_SCHEDULE, STYLES_CSS

MANUAL_SCHEDULE_FILENAME = "manual_schedule.html"

# Only these structured categories represent genuine manual ice-time/booking
# work and belong in the booking table (`$ROWS$`). Participation-target
# deviations (over or under) are team-level planning-quality signals, not
# ice-booking tasks -- they render in their own section instead (see
# `_participation_section_html`), never mixed into this table even if a
# caller passes one in via `manual_entries`.
MANUAL_SCHEDULE_CATEGORIES = frozenset(
    {
        "arena_collision",
        "manual_calendar_verification",
        "manual_hosting_obligation",
        "manual_external_conflict",
        "manual_tournament_placement",
    }
)


def _participation_section_html(entries: list[dict[str, str]]) -> str:
    """Render the "Deltakelsesavvik" section listing participation shortfalls.

    issue #321: the final verifier's `manual_participation_placements` are
    counted in `publication_readiness`, so they must be visible to the
    operator on this same page instead of only in
    `plan.unresolved_participation_shortfalls` / the rules report -- an
    operator reviewing only this page would otherwise never see them. Kept
    as its own section/table (not merged into the booking table above)
    since these are team-level participation-count mismatches, not
    tournaments that need an arena/time slot.
    """
    if not entries:
        return ""

    def _sort_key(item: dict[str, str]) -> tuple:
        return (
            str(item.get("age_group", "")),
            str(item.get("club", "")),
            str(item.get("label", "")),
            str(item.get("half", "") or item.get("period", "")),
        )

    rows: list[str] = []
    for idx, item in enumerate(sorted(entries, key=_sort_key), start=1):
        club = str(item.get("club", "") or "")
        label = str(item.get("label", "") or "")
        age_group = str(item.get("age_group", "") or "")
        half = str(item.get("half", "") or item.get("period", "") or "")
        half_display = {
            "before_christmas": "Før jul",
            "after_christmas": "Etter jul",
        }.get(half, half or "Hele sesongen")
        actual = str(item.get("actual", "") or "")
        target = str(item.get("target", "") or "")
        category = str(item.get("category", "") or "")
        reason = str(item.get("reason", "") or "actual participation count does not match target")
        evidence = item.get("same_date_capacity_evidence") or []
        if category == "participation_under_target_same_date_capacity" and evidence:
            evidence_bits = []
            for ev in evidence:
                if not isinstance(ev, dict):
                    continue
                ev_category = ev.get("category")
                if ev_category == "same_date_uniqueness_limit":
                    evidence_bits.append(
                        f"{ev.get('date', '')}: {ev.get('requested_slots', '?')} parallelle turneringer "
                        f"ønsket, {ev.get('feasible_slots', '?')} mulig med {ev.get('distinct_team_count', '?')} lag"
                    )
                elif ev_category == "same_date_participant_pool_capacity":
                    evidence_bits.append(
                        f"{ev.get('unplaced_participations', '?')} deltakelse(r) uten plass på "
                        f"{', '.join(str(d) for d in (ev.get('limiting_dates') or []))}"
                    )
            if evidence_bits:
                reason = f"{reason} ({'; '.join(evidence_bits)})"
        rows.append(
            "<tr>"
            f"<td class=\"numeric-cell\">{idx}</td>"
            f"<td><strong>{_html.escape(age_group)}</strong></td>"
            f"<td>{_html.escape(club)}</td>"
            f"<td>{_html.escape(label)}</td>"
            f"<td>{_html.escape(half_display)}</td>"
            f"<td class=\"numeric-cell\">{_html.escape(actual)}/{_html.escape(target)}</td>"
            f"<td>{_html.escape(reason)}</td>"
            "</tr>"
        )

    return (
        '<section class="report-section" id="participationShortfalls">'
        '<div class="section-head"><div><p class="eyebrow">Deltakelse</p>'
        "<h2>Deltakelsesavvik som ikke fikk plass automatisk</h2></div>"
        f'<p class="section-note">{len(entries)} lag deltar ikke det konfigurerte antallet ganger. '
        "Dette er ikke en ledig-istid-oppgave, men et planleggingsavvik som må vurderes før publisering.</p>"
        "</div>"
        '<div class="table-wrap"><table class="report-table"><thead><tr>'
        "<th>#</th><th>Aldersgruppe</th><th>Klubb</th><th>Lag</th><th>Halvdel</th><th>Faktisk/Mål</th><th>Årsak</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        "</section>"
    )


def _waiver_section_html(entries: list[dict[str, object]]) -> str:
    """Render the "Operator-godkjente unntak" section.

    A plan that only passes verification because an authorized operator
    explicitly waived a hard planning rule must not look identical to a plan
    that passed with no exception at all -- this section keeps the rule,
    exact scope, configured-vs-accepted value, reason and actor visible on
    the operator page.
    """
    entries = [item for item in entries or [] if isinstance(item, dict)]
    if not entries:
        return ""

    rule_labels = {
        "participation_target_exceeded": "Deltakelsestak overskredet",
    }
    half_labels = {
        "before_christmas": "Før jul",
        "after_christmas": "Etter jul",
    }

    rows: list[str] = []
    for idx, item in enumerate(entries, start=1):
        rule = str(item.get("rule", "") or "")
        team = item.get("team") or {}
        club = str(team.get("club", "") or "")
        label = str(team.get("label", "") or "")
        age_group = str(team.get("age_group", "") or "")
        half = half_labels.get(str(item.get("half", "") or ""), str(item.get("half", "") or "Hele sesongen"))
        configured = str(item.get("configured_value", "") or "")
        allowed = str(item.get("allowed_value", "") or "")
        tournament_id = str(item.get("tournament_id", "") or "-")
        reason = str(item.get("reason", "") or "")
        created_by = str(item.get("created_by", "") or "")
        created_at = str(item.get("created_at", "") or "")
        rows.append(
            "<tr>"
            f'<td class="numeric-cell">{idx}</td>'
            f"<td><strong>{_html.escape(age_group)}</strong></td>"
            f"<td>{_html.escape(club)}</td>"
            f"<td>{_html.escape(label)}</td>"
            f"<td>{_html.escape(half)}</td>"
            f'<td class="numeric-cell">{_html.escape(allowed)}/{_html.escape(configured)}</td>'
            f"<td>{_html.escape(tournament_id)}</td>"
            f"<td>{_html.escape(rule_labels.get(rule, rule))}</td>"
            f"<td>{_html.escape(reason)}</td>"
            f"<td>{_html.escape(created_by)}{(' &middot; ' + _html.escape(created_at)) if created_at else ''}</td>"
            "</tr>"
        )

    return (
        '<section class="report-section" id="operatorWaivers">'
        '<div class="section-head"><div><p class="eyebrow">Operatorunntak</p>'
        "<h2>Eksplisitt godkjente unntak fra harde planleggingsregler</h2></div>"
        f'<p class="section-note">{len(entries)} unntak er godkjent av operatør. '
        "Planen ville ellers blitt stoppet av verifiseringen. Unntaket gjelder kun nøyaktig "
        "det viste laget, halvdelen, turneringen og verdien.</p>"
        "</div>"
        '<div class="table-wrap"><table class="report-table"><thead><tr>'
        "<th>#</th><th>Aldersgruppe</th><th>Klubb</th><th>Lag</th><th>Halvdel</th>"
        "<th>Godkjent/Konfigurert</th><th>Turnering</th><th>Regel</th><th>Begrunnelse</th><th>Godkjent av</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        "</section>"
    )


def _manual_schedule_html(
    plan: SeasonPlan,
    *,
    manual_entries: list[dict[str, str]] | None = None,
    participation_entries: list[dict[str, str]] | None = None,
    waiver_entries: list[dict[str, object]] | None = None,
    generated_at: str = "",
    input_path: str = "",
    date_range: str = "",
    source_count: int = 0,
    event_count: int = 0,
    blocked: list[str] | None = None,
    scrape_age: str = "",
    calendars_href: str = "",
    season_plan_href: str = "",
    report_href: str = "",
    input_href: str = "",
) -> str:
    """Render the dedicated “Må planlegges manuelt” page.

    Lists everything that cannot be treated as auto-confirmed hall time:

    - tournaments that ended up with a same-arena/sequence overflow collision
      during host/time assignment (the auto-planner already tried to shift
      them; hall time must be booked by hand or the plan re-run), and
    - tournaments hosted by clubs whose calendar source could not be scraped
      (they still receive their share of home tournaments, but the assigned
      start time is provisional — the istid must be booked/verified manually).

    Each entry carries a ``type`` (Grunn) so the arena scheduler can see why
    it must act.

    ``participation_entries`` (issue #321) renders as a separate section
    below the booking table -- team-level participation-target shortfalls
    that `publication_readiness` counts as an operator-facing finding, but
    that are not themselves an ice-time/booking task.
    """
    from ..html.data_computation import canonical_rvv_club_name

    # Filter on the structured category, not the rendered reason text -- a
    # participation-target deviation must never inflate this page's count
    # even if a caller forgets to filter it out first.
    entries = sorted(
        (e for e in (manual_entries or []) if e.get("category") in MANUAL_SCHEDULE_CATEGORIES),
        key=lambda c: (c.get("date", ""), c.get("arena", ""), c.get("tournament_id", "")),
    )
    rows: list[str] = []
    for idx, c in enumerate(entries, start=1):
        arena = str(c.get("arena", "") or "")
        raw_host = str(c.get("host_club", "") or "")
        host = canonical_rvv_club_name(raw_host) if raw_host else ""
        if not host or host == "-":
            host = raw_host or arena or "?"
        tournament_id = str(c.get("tournament_id", "") or "")
        age_group = str(c.get("age_group", "") or "")
        date_val = str(c.get("date", "") or "")
        interval = str(c.get("interval", "") or "")
        if not interval and date_val:
            try:
                from datetime import date as _date
                interval = fmt_date(_date.fromisoformat(date_val)) or date_val
            except ValueError:
                interval = date_val
        entry_type = str(c.get("type", "") or "Arena-/tidskollisjon")
        conflict_id = str(c.get("conflicting_tournament_id", "") or "")
        conflict_ag = str(c.get("conflicting_age_group", "") or "")
        conflict_interval = str(c.get("conflicting_interval", "") or "")
        detail = str(c.get("message", "") or "")
        if not detail:
            detail = f"{interval} kolliderer med {conflict_interval}" if conflict_interval else interval
        conflict_cell = " ".join(part for part in (conflict_id, conflict_ag, conflict_interval) if part) or "-"
        rows.append(
            "<tr>"
            f"<td class=\"numeric-cell\">{idx}</td>"
            f"<td>{_html.escape(tournament_id)}</td>"
            f"<td>{_html.escape(date_val)}</td>"
            f"<td><strong>{_html.escape(age_group)}</strong></td>"
            f"<td>{_html.escape(host)}</td>"
            f"<td>{_html.escape(arena)}</td>"
            f"<td>{_html.escape(interval)}</td>"
            f"<td>{_html.escape(entry_type)}</td>"
            f"<td>{_html.escape(conflict_cell)}</td>"
            f"<td>{_html.escape(detail)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan=\"10\" class=\"empty-cell\">Ingen turneringer trenger manuell planlegging.</td></tr>')

    rows_html = "".join(rows)

    def _nav_link(href: str, label: str, icon: str, active: bool = False) -> str:
        cls = "active" if active else ""
        return f'<a href="{_html.escape(href)}" class="{cls}"><span class="nav-icon">{icon}</span> {_html.escape(label)}</a>'

    calendar_nav = _nav_link(calendars_href, "Skrapede kalendere", ICON_CALENDAR) if calendars_href else ""
    season_plan_nav = (
        _nav_link(season_plan_href, "Sesongplan", ICON_CLIPBOARD) if season_plan_href else ""
    )
    report_nav = _nav_link(report_href, "Regler", ICON_BAR_CHART) if report_href else ""
    manual_nav = _nav_link(MANUAL_SCHEDULE_FILENAME, "Må planlegges manuelt", ICON_WARNING, active=True)
    input_nav = _nav_link(input_href, "Påmeldte lag", ICON_USERS) if input_href else ""

    scrape_meta_parts: list[str] = []
    if source_count:
        scrape_meta_parts.append(f"{source_count} kilder")
    if event_count:
        scrape_meta_parts.append(f"{event_count} hendelser")
    if scrape_age:
        scrape_meta_parts.append(f"Data: {scrape_age}")
    scrape_meta = " &middot; ".join(scrape_meta_parts)
    if scrape_meta:
        scrape_meta = f'<span class="meta-nav">{scrape_meta}</span>'

    subtitle = "RVV Hockey &mdash; manuelt behov"
    if season_label(plan):
        subtitle = f"{_html.escape(season_label(plan))} &mdash; manuelt behov"

    extra_note = ""
    if blocked:
        names = ", ".join(str(item) for item in blocked)
        extra_note = (
            '<div class="report-action report-action--warn"><strong>Datagrunnlag</strong>'
            f"<p>{_html.escape(names)} var utilgjengelig under skraping; husk å følge opp manuelt.</p></div>"
        )

    hidden_parts: list[str] = []
    if date_range:
        hidden_parts.append(f"Periode: {date_range}")
    if generated_at:
        hidden_parts.append(f"Generert: {generated_at}")
    if input_path:
        hidden_parts.append(f"Input: {input_path}")

    participation_section = _participation_section_html(list(participation_entries or []))
    waiver_section = _waiver_section_html(list(waiver_entries or []))
    # issue #321: the hero count/copy above is specifically about ice-time
    # booking work (see the module docstring's `MANUAL_SCHEDULE_CATEGORIES`
    # note), so it stays scoped to `entries` -- participation findings get
    # their own count in `_participation_section_html`'s section note, which
    # must equal `publication_readiness.reasons[participation_shortfalls]`
    # independently rather than being folded into this unrelated headline.
    entry_count = str(len(entries))
    parts = {
        "$STYLES$": STYLES_CSS,
        "$CALENDAR_NAV$": calendar_nav,
        "$SEASON_PLAN_NAV$": season_plan_nav,
        "$REPORT_NAV$": report_nav,
        "$MANUAL_NAV$": manual_nav,
        "$INPUT_NAV$": input_nav,
        "$SCRAPE_META$": scrape_meta,
        "$ICON_WARNING$": ICON_WARNING,
        "$SUBTITLE$": subtitle,
        "$ENTRY_COUNT$": entry_count,
        "$EXTRA_NOTE$": extra_note,
        "$ROWS$": rows_html,
        "$PARTICIPATION_SECTION$": participation_section,
        "$WAIVER_SECTION$": waiver_section,
        "$HIDDEN_CONTEXT$": _html.escape(" · ".join(hidden_parts)),
    }
    html = MANUAL_SCHEDULE
    for token, value in parts.items():
        html = html.replace(token, value)
    return html
