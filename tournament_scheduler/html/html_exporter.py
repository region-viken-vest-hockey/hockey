"""Interactive HTML overview for the season plan.

Reads a :class:`~tournament_scheduler.models.SeasonPlan` and generates a
standalone, interactive HTML page showing all tournaments, filtering by
age group / arena / club / search, and expandable match tables.

HTML is assembled from template fragments in ``templates/``.
Data computation lives in :mod:`data_computation`; rendering helpers
live in :mod:`renderers`.
"""

from __future__ import annotations

import html as _html
import json
import os
import re
from pathlib import Path
from typing import Any

from tournament_scheduler.club_distances import furthest_traveling_team
from ..models import SeasonPlan, team_key

from .data_computation import (
    ICON_CALENDAR,
    ICON_CLIPBOARD,
    ICON_USERS,
    ICON_TARGET,
    ICON_TRAVEL,
    ICON_WARNING,
    ICON_BAR_CHART,
    ICON_FILE_SPREADSHEET,
    ICON_CLOCK,
    _RVV_CLUBS,
    _CLUB_ALIASES,
    canonical_rvv_club_name,
    season_label,
    fmt_date,
    timestamp_string,
    compute_team_game_counts,
    compute_team_travel_info,
    compute_heatmap_data,
    compute_club_stats,
    build_export_links_html,
    compute_display_age_groups,
)
from .renderers.fairness import (
    render_fairness_gate_html,
    render_fairness_adjustments_html,
)
from .renderers.review import analyze_review_summary, render_review_summary_html
from .renderers.judgment import analyze_opinionated_judgment, render_judgment_cards_html
from .renderers.heatmap import build_club_color_maps

# ---------------------------------------------------------------------------
# Load template fragments
# ---------------------------------------------------------------------------

from .templates import (
    STYLES_CSS,
    NAVBAR,
    HEADER,
    FILTERS,
    TEAM_STATS,
    TRAVEL_STATS,
    HEATMAP,
    REPORT_OVERVIEW,
    PAGE_TEMPLATE,
    SHARED_JAVASCRIPT,
    SCHEDULE_JAVASCRIPT,
    COUNT_BAR,
    SCORES,
    METRICS,
    CLUB_DASHBOARD,
)


# ---------------------------------------------------------------------------
# Exporter class
# ---------------------------------------------------------------------------


class HtmlExporter:
    """Generates a standalone interactive HTML overview of a :class:`SeasonPlan`."""

    def export(
        self,
        plan: SeasonPlan,
        path: str | os.PathLike[str],
        meta: dict[str, Any] | None = None,
        *,
        output_files: dict[str, str] | None = None,
        pipeline_meta: dict[str, Any] | None = None,
        round_length_for_age_group: dict[str, int] | None = None,
        age_groups: list[str] | None = None,
        calendars_path: str | None = None,
        input_html_path: str | None = None,
        manual_schedule_path: str | None = None,
    ) -> str:
        """Write an interactive HTML overview to *path*, return the path.

        Parameters
        ----------
        plan: The season plan to export.
        path: Output file path.
        meta: Optional metadata from scraped data cache (total_events, source_count, etc.).
        output_files: Optional dict mapping format name to absolute file paths for download links.
        pipeline_meta: Optional pipeline-wide metadata with blocked sources, date range, etc.
        round_length_for_age_group: Optional mapping of age group -> round
            length in minutes, used together with each tournament's
            ``start_time`` to compute and display a "HH:MM-HH:MM" time
            range via ``Tournament.end_time()``.
        calendars_path: Absolute path to the generated calendars.html file. When provided and
            the file exists, a navbar link to calendars.html is included.
        input_html_path: Absolute path to the generated input.html file (public overview of
            registered clubs/teams). When provided and the file exists, a navbar link to
            input.html is included.
        manual_schedule_path: Absolute path to the generated manual_schedule.html file. When
            provided and the file exists, a navbar link to the manual scheduling view is included.
        """
        tournaments_json = self._plan_to_json(plan, round_length_for_age_group)

        # Count unique teams
        all_teams: set[str] = set()
        for t in plan.tournaments:
            for g in t.games:
                all_teams.add(g.home.label)
                all_teams.add(g.away.label)

        # Team game counts
        team_game_counts = compute_team_game_counts(plan)
        label_to_identities: dict[str, set[tuple[str, str]]] = {}
        for tournament in plan.tournaments:
            for game in tournament.games:
                for team_obj in (game.home, game.away):
                    identity = (getattr(team_obj, "club", ""), getattr(team_obj, "age_group", ""))
                    label_to_identities.setdefault(team_obj.label, set()).add(identity)
        duplicate_labels = {label for label, ids in label_to_identities.items() if len(ids) > 1}
        team_game_counts_json = json.dumps(team_game_counts, ensure_ascii=False)

        # Travel info
        team_travel, most_travel_team, most_travel_km, total_travel_km, travel_count_estimate_html = (
            compute_team_travel_info(plan)
        )
        team_travel_json = json.dumps(team_travel, ensure_ascii=False)

        # Heatmap data
        heatmap, heatmap_weeks, heatmap_clubs = compute_heatmap_data(plan)
        heatmap_json = json.dumps(heatmap, ensure_ascii=False)
        heatmap_weeks_json = json.dumps(heatmap_weeks, ensure_ascii=False)
        heatmap_clubs_json = json.dumps(heatmap_clubs, ensure_ascii=False)

        # Club colours
        club_color_maps = build_club_color_maps(heatmap_clubs)
        heatmap_club_colors_json = json.dumps(club_color_maps, ensure_ascii=False)

        # Club stats
        club_stats, all_clubs_list = compute_club_stats(plan, team_travel)
        club_stats_json = json.dumps(club_stats, ensure_ascii=False)
        all_clubs_json = json.dumps(all_clubs_list, ensure_ascii=False)

        season_label_str = season_label(plan)
        display_age_groups = compute_display_age_groups(plan, age_groups)
        age_group_options = "".join(
            f'<option value="{ag}">{ag}</option>'
            for ag in display_age_groups
        )

        # Pipeline metrics
        pipeline = pipeline_meta or {}
        meta = meta or {}
        ev = int(pipeline.get("total_events", meta.get("total_events", 0)) or 0)
        src = int(pipeline.get("source_count", meta.get("source_count", 0)) or 0)
        source_count = src
        event_count = ev
        generated_at = str(pipeline.get("generated_at", ""))
        scrape_stamp = timestamp_string(generated_at)
        scrape_meta = f"{source_count} kilder &middot; {event_count} hendelser"
        if scrape_stamp:
            scrape_meta += f" &middot; {scrape_stamp}"

        # Pipeline metrics
        blocked = pipeline.get("blocked", [])
        blocked_count = len(blocked)
        blocked_names = ""
        if blocked:
            blocked_names = ": " + ", ".join(blocked)
        date_range = pipeline.get("date_range", f"{fmt_date(plan.start_date)} – {fmt_date(plan.end_date)}" if plan.start_date else "")
        if isinstance(date_range, str):
            date_range = date_range.replace("&ndash;", "–")
        input_path = str(pipeline.get("input_path", ""))
        scrape_age = pipeline.get("scrape_age", "")
        scrape_age_html = ""
        if scrape_age:
            scrape_age_html = f'<div class="metrics-group"><span class="metrics-group-label">Data-alder</span><span class="metrics-group-value">{scrape_age}</span></div>'

        # Render components
        fairness_gate_html = render_fairness_gate_html(
            plan.fairness_gate if isinstance(plan.fairness_gate, dict) else None
        )
        review_summary = analyze_review_summary(plan)
        review_summary_html = render_review_summary_html(
            plan,
            compact=not review_summary["has_unique_findings"],
        )
        fairness_adjustments_html = render_fairness_adjustments_html(plan)
        judgment = analyze_opinionated_judgment(
            plan,
            team_game_counts=team_game_counts,
            club_stats=club_stats,
            team_travel=team_travel,
        )
        judgment_cards_html = render_judgment_cards_html(list(judgment["cards"]))
        report_overview_html = self._report_overview_html(
            plan,
            source_count=source_count,
            event_count=event_count,
            blocked=blocked,
            date_range=date_range,
            display_age_groups=display_age_groups,
            team_game_counts=team_game_counts,
            club_stats=club_stats,
            team_travel=team_travel,
            fairness_gate_html=fairness_gate_html,
            fairness_adjustments_html=fairness_adjustments_html,
            review_summary_html=review_summary_html,
            judgment_cards_html=judgment_cards_html,
            team_stats_html=TEAM_STATS,
            travel_stats_html=TRAVEL_STATS,
            heatmap_html=HEATMAP,
            judgment=judgment,
            scores_html=SCORES,
            metrics_html=METRICS,
            club_dashboard_html=CLUB_DASHBOARD,
            generated_at=generated_at,
            input_path=input_path,
            most_travel_team=most_travel_team,
            most_travel_km=str(most_travel_km),
        )
        export_links_html = build_export_links_html(output_files)

        # Assemble pages from fragments
        # Link to calendars.html only when the file actually exists on disk.
        # calendars_path is passed by stage4_export after generating the file, before calling export().
        calendars_href = "calendars.html" if (calendars_path and os.path.exists(calendars_path)) else ""
        input_href = "input.html" if (input_html_path and os.path.exists(input_html_path)) else ""
        manual_href = "manual_schedule.html" if (manual_schedule_path and os.path.exists(manual_schedule_path)) else ""
        season_plan_href = "season_plan.html"
        report_href = "season_plan_report.html"

        def _render_page(*, page_title: str, page_subtitle: str, include_diagnostics: bool, include_timeline: bool, active_page: str) -> str:
            parts = {
                "$STYLES$": STYLES_CSS,
                "$NAVBAR$": NAVBAR,
                "$HEADER$": HEADER,
                "$REPORT_OVERVIEW$": report_overview_html if include_diagnostics else "",
                "$SCORES$": "",
                "$METRICS$": "",
                "$FAIRNESS_ADJUSTMENTS$": "",
                "$REVIEW_SUMMARY$": "",
                "$EXPORT_LINKS$": export_links_html,
                "$CLUB_DASHBOARD$": "",
                "$TEAM_STATS$": "",
                "$TRAVEL_STATS$": "",
                "$HEATMAP$": "",
                "$REPORT_HEATMAP$": "",
                "$JUDGMENT$": "",
                "$FILTERS$": FILTERS if include_timeline else "",
                "$COUNT_BAR$": COUNT_BAR if include_timeline else "",
                "$TIMELINE$": '<div class="timeline" id="timeline"></div>' if include_timeline else "",
                "$SCRIPT$": (
                    SHARED_JAVASCRIPT + ("\n" + SCHEDULE_JAVASCRIPT if include_timeline else "")
                ),
            }

            replacements = {
                "$ICON_CALENDAR$": ICON_CALENDAR,
                "$ICON_CLIPBOARD$": ICON_CLIPBOARD,
                "$ICON_USERS$": ICON_USERS,
                "$ICON_TARGET$": ICON_TARGET,
                "$ICON_TRAVEL$": ICON_TRAVEL,
                "$ICON_WARNING$": ICON_WARNING,
                "$ICON_BAR_CHART$": ICON_BAR_CHART,
                "$CALENDARS_HREF$": calendars_href,
                "$CALENDARS_NAV_ITEM$": (
                    f'<a href="{calendars_href}" class="{"active" if active_page == "calendars" else ""}"><span class="nav-icon">{ICON_CALENDAR}</span> Skrapede kalendere</a>'
                    if calendars_href else ""
                ),
                "$SEASON_PLAN_HREF$": season_plan_href,
                "$REPORT_HREF$": report_href,
                "$INPUT_NAV_ITEM$": (
                    f'<a href="{input_href}" class="{"active" if active_page == "input" else ""}"><span class="nav-icon">{ICON_USERS}</span> Påmeldte lag</a>'
                    if input_href else ""
                ),
                "$MANUAL_NAV_ITEM$": (
                    f'<a href="{manual_href}" class="{"active" if active_page == "manual" else ""}"><span class="nav-icon">{ICON_WARNING}</span> Må planlegges manuelt</a>'
                    if manual_href else ""
                ),
                "$CALENDARS_ACTIVE$": "active" if active_page == "calendars" else "",
                "$SEASON_PLAN_ACTIVE$": "active" if active_page == "season" else "",
                "$REPORT_ACTIVE$": "active" if active_page == "report" else "",
                "$PAGE_TITLE$": page_title,
                "$PAGE_SUBTITLE$": page_subtitle,
                "$SEASON_LABEL$": season_label_str,
                "$SCRAPE_META$": scrape_meta,
                "$AGE_GROUPS$": " + ".join(display_age_groups),
                "$TOURNAMENT_COUNT$": str(len(plan.tournaments)),
                "$GAME_COUNT$": str(sum(len(t.games) for t in plan.tournaments)),
                "$UNIQUE_TEAMS$": str(len(team_game_counts)),
                "$TEAM_COUNT$": str(len(team_game_counts)),
                "$GAME_COUNT_SPREAD$": (
                    f"{max(team_game_counts.values()) - min(team_game_counts.values())} spread"
                    if team_game_counts else "-"
                ),
                "$SOURCE_COUNT$": str(source_count),
                "$EVENT_COUNT$": str(event_count),
                "$BLOCKED_COUNT$": str(blocked_count),
                "$BLOCKED_NAMES$": blocked_names,
                "$DATE_RANGE$": date_range,
                "$TOTAL_TRAVEL_KM$": str(total_travel_km),
                "$SCRAPE_AGE_HTML$": scrape_age_html,
                "$TEAM_GAME_COUNTS_JSON$": team_game_counts_json,
                "$TEAM_TRAVEL_JSON$": team_travel_json,
                "$MOST_TRAVEL_TEAM$": most_travel_team,
                "$MOST_TRAVEL_KM$": most_travel_km,
                "$TRAVEL_COUNT_ESTIMATE_HTML$": travel_count_estimate_html,
                "$HEATMAP_JSON$": heatmap_json,
                "$HEATMAP_WEEKS_JSON$": heatmap_weeks_json,
                "$HEATMAP_CLUBS_JSON$": heatmap_clubs_json,
                "$HEATMAP_CLUB_COLORS_JSON$": heatmap_club_colors_json,
                "$HEATMAP_CLUBS_COUNT$": str(len(heatmap_clubs)),
                "$HEATMAP_WEEKS_COUNT$": str(len(heatmap_weeks)),
                "$CLUB_STATS_JSON$": club_stats_json,
                "$ALL_CLUBS_JSON$": all_clubs_json,
                "$DIVERSITY_SCORE$": str(int((plan.diversity_score or 0) * 100)),
                "$MONTH_BALANCE_SCORE$": str(int((plan.month_balance_score or 0) * 100)),
                "$PAIRWISE_SCORE$": str(int((plan.pairwise_matchup_score or 0) * 100)),
                "$FAIRNESS_GATE_SCORE$": str(int((plan.fairness_gate.get("score", 0) if isinstance(plan.fairness_gate, dict) else 0))),
                "$FAIRNESS_GATE_STATUS$": str((plan.fairness_gate.get("status", "pass") if isinstance(plan.fairness_gate, dict) else "pass")),
                "$FAIRNESS_GATE_STATUS_LABEL$": str({"pass": "PASS", "warn": "VARSEL", "fail": "FEIL"}.get(plan.fairness_gate.get("status", "pass") if isinstance(plan.fairness_gate, dict) else "pass", "PASS")),
                "$FAIRNESS_GATE_HTML$": fairness_gate_html,
                "$AGE_GROUP_OPTIONS$": age_group_options,
                "$TOURNAMENTS_JSON$": tournaments_json,
            }

            html = PAGE_TEMPLATE
            for part_key, part_value in parts.items():
                html = html.replace(part_key, part_value)
            for marker, value in replacements.items():
                html = html.replace(marker, value)
            return html

        schedule_html = _render_page(
            page_title="Sesongplan",
            page_subtitle=f"RVV Hockey &mdash; {' + '.join(display_age_groups)}",
            include_diagnostics=False,
            include_timeline=True,
            active_page="season",
        )
        report_html = _render_page(
            page_title="Sesongrapport",
            page_subtitle=f"RVV Hockey &mdash; {' + '.join(display_age_groups)} &middot; diagnostikk",
            include_diagnostics=True,
            include_timeline=False,
            active_page="report",
        )
        report_html = self._strip_schedule_controls(report_html)

        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(schedule_html, encoding="utf-8")
        report_dest = dest.with_name(f"{dest.stem}_report{dest.suffix}")
        report_dest.write_text(report_html, encoding="utf-8")
        return str(dest)

    @staticmethod
    def _report_overview_html(
        plan: SeasonPlan,
        *,
        source_count: int,
        event_count: int,
        blocked: list[str],
        date_range: str,
        display_age_groups: list[str],
        team_game_counts: dict[str, int],
        club_stats: dict[str, dict[str, object]],
        team_travel: dict[str, int],
        fairness_gate_html: str,
        fairness_adjustments_html: str,
        review_summary_html: str,
        judgment_cards_html: str,
        team_stats_html: str,
        travel_stats_html: str,
        heatmap_html: str,
        judgment: dict[str, object],
        scores_html: str,
        metrics_html: str,
        club_dashboard_html: str,
        generated_at: str = "",
        input_path: str = "",
        most_travel_team: str = "",
        most_travel_km: str = "0",
    ) -> str:
        """Render the organizer-first report overview above raw diagnostics."""
        # Compute Norwegian month-span string from plan dates
        _NO_MONTHS = [
            "", "januar", "februar", "mars", "april", "mai", "juni",
            "juli", "august", "september", "oktober", "november", "desember",
        ]
        if plan.start_date and plan.end_date:
            _start_month = _NO_MONTHS[plan.start_date.month]
            _end_month = _NO_MONTHS[plan.end_date.month]
            month_span = f"{_start_month}–{_end_month}" if _start_month != _end_month else _start_month
        else:
            month_span = date_range

        gate = plan.fairness_gate if isinstance(plan.fairness_gate, dict) else {}
        cancelled_count = sum(1 for tournament in plan.tournaments if tournament.cancelled)

        active_tournaments = [t for t in plan.tournaments if not t.cancelled]
        active_tournaments.sort(key=lambda t: (t.date or plan.end_date, t.age_group, t.host_club or "", t.arena))
        label_to_identities: dict[str, set[tuple[str, str]]] = {}
        for tournament in active_tournaments:
            for game in tournament.games:
                for team_obj in (game.home, game.away):
                    identity = (getattr(team_obj, "club", ""), getattr(team_obj, "age_group", ""))
                    label_to_identities.setdefault(team_obj.label, set()).add(identity)
        duplicate_labels = {label for label, ids in label_to_identities.items() if len(ids) > 1}
        host_counts: dict[str, int] = {}
        team_clubs = sorted(
            {
                canonical_rvv_club_name(team.club)
                for tournament in active_tournaments
                for team in tournament.teams
                if getattr(team, "club", None)
            }
        )
        for tournament in active_tournaments:
            host = tournament.host_club or ""
            if host:
                canonical_host = canonical_rvv_club_name(host)
                host_counts[canonical_host] = host_counts.get(canonical_host, 0) + 1
        missing_hosts = [club for club in _RVV_CLUBS if club not in host_counts]

        def _missing_host_label(club: str) -> str:
            return f"{club} (ingen lag i planen)" if club not in team_clubs else club

        metric_warnings = [m for m in gate.get("metrics", []) if isinstance(m, dict) and m.get("status") in {"warn", "fail"}]
        weakest_metric = sorted(
            metric_warnings,
            key=lambda m: (0 if str(m.get("status", "warn")) == "fail" else 1, m.get("score", 100)),
        )[0] if metric_warnings else None
        hard_blockers: list[str] = []
        if not active_tournaments:
            hard_blockers.append("Planen har ingen aktive turneringer.")
        if not team_game_counts:
            hard_blockers.append("Planen har ingen lagdata å vise.")
        arena_day_collisions = getattr(plan, "arena_day_collisions", None) or []
        # Tournaments hosted by clubs whose calendar could not be scraped are
        # provisional (start time must be booked/verified by hand) and are listed
        # in the manual-schedule view together with arena collisions.
        manual_host_count = sum(
            1
            for t in plan.tournaments
            if not t.cancelled and getattr(t, "manual_booking_reason", None)
        )
        manual_total = len(arena_day_collisions) + manual_host_count

        overall_status = "fail" if hard_blockers else ("warn" if manual_total else "pass")
        status_labels = {"pass": "KAN BRUKES", "warn": "MÅ SJEKKES", "fail": "BLOKKER"}

        actions: list[tuple[str, str, str]] = []
        for blocker in hard_blockers:
            actions.append(("fail", "Blokkerende feil", blocker))
        if manual_total:
            reasons = []
            if arena_day_collisions:
                reasons.append(f"{len(arena_day_collisions)} kollisjon(er)")
            if manual_host_count:
                reasons.append(f"{manual_host_count} med utilgjengelig kalender")
            actions.append((
                "warn",
                "Manuell istidsplanlegging",
                f"{manual_total} turnering(er) trenger manuell oppfølging av istid ({', '.join(reasons)}). Se egen visning 'Må planlegges manuelt'.",
            ))
        if blocked:
            actions.append(("warn", "Datagrunnlag", f"{len(blocked)} kalenderkilde(r) er blokkert: {', '.join(blocked)}."))
        if missing_hosts:
            actions.append(("warn", "Hjemmeturneringer", f"Ingen hjemmeturnering registrert for: {', '.join(_missing_host_label(club) for club in missing_hosts)}."))
        if cancelled_count:
            actions.append(("warn", "Avlyst/hoppet over", f"{cancelled_count} turnering(er) er markert som avlyst eller hoppet over."))
        issue_count = sum(1 for status, _, _ in actions if status != "pass") + len(metric_warnings) + (1 if str(gate.get("status", "pass")).lower() in {"warn", "fail"} else 0)
        if not actions:
            actions.append(("pass", "Ingen blokkeringer", "Fairness og småskjevheter ligger under detaljer og stopper ikke bruk."))

        _tournament_count = len(active_tournaments)
        if hard_blockers:
            answer = "Nei — planen bør stoppes"
            note = "Løs blokkeringene under før utsending."
        elif manual_total:
            answer = "Ja — men noen istider må avklares manuelt"
            note = "Komplett eksport er laget; bruk manuell-visningen til å følge opp klubb/arena før endelig utsending."
        else:
            answer = "Ja — planen kan brukes"
            note = "Ingen blokkeringer."

        def _format_generated_at(value: str) -> str:
            if not value:
                return ""
            return timestamp_string(value)

        hidden_notes: list[str] = [f"Periode: {month_span}"]
        if generated_at:
            hidden_notes.append(f"Generert: {_format_generated_at(generated_at)}")
        if input_path:
            hidden_notes.append(f"Input: {input_path}")
        if weakest_metric:
            hidden_notes.append(
                f"Svakeste metrikk: {weakest_metric.get('label', '')} ({int(weakest_metric.get('score', 0) or 0)}%)"
            )
            hidden_notes.append(
                f"Fairness-avvik: {weakest_metric.get('label', '')} — {weakest_metric.get('detail', '')}"
            )
        if blocked:
            hidden_notes.append(f"{len(blocked)} kilde(r) blokkert.")
        if cancelled_count:
            hidden_notes.append(f"{cancelled_count} turnering(er) avlyst.")
        if most_travel_team:
            hidden_notes.append(f"Mest reisende lag: {most_travel_team} (~{most_travel_km} km)")
        hidden_context_html = '<div class="report-hidden-context" aria-hidden="true">' + _html.escape(" · ".join(hidden_notes)) + '</div>'

        age_rows: list[str] = []
        for age_group in display_age_groups:
            tournaments = [t for t in active_tournaments if t.age_group == age_group]
            age_team_objs = sorted(
                {team.label: team for tournament in tournaments for team in tournament.teams}.values(),
                key=lambda t: (t.club, t.label),
            )
            labels = [team.label for team in age_team_objs]
            hosts = sorted({canonical_rvv_club_name(t.host_club or "") for t in tournaments if t.host_club})
            game_counts = [team_game_counts.get(team_key(team, duplicate_labels), 0) for team in age_team_objs]
            spread = f"{min(game_counts)}\u2013{max(game_counts)}" if game_counts else "-"
            first_date = fmt_date(min((t.date for t in tournaments if t.date), default=None))
            last_date = fmt_date(max((t.date for t in tournaments if t.date), default=None))
            dates = f"{first_date} \u2013 {last_date}" if first_date and last_date and first_date != last_date else (first_date or "-")
            age_rows.append(
                "<tr>"
                f"<td><strong>{_html.escape(age_group)}</strong></td>"
                f"<td class=\"numeric-cell\">{len(tournaments)}</td>"
                f"<td class=\"numeric-cell\">{len(labels)}</td>"
                f"<td>{_html.escape(', '.join(hosts) or '-')}</td>"
                f"<td>{_html.escape(spread)}</td>"
                f"<td>{_html.escape(dates)}</td>"
                "</tr>"
            )
        if not age_rows:
            age_rows.append('<tr><td colspan="6" class="empty-cell">Ingen aldersgrupper i planen</td></tr>')

        club_rows: list[str] = []
        for club in sorted(club_stats):
            stats = club_stats[club]
            hosted = int(stats.get("hosted", 0) or 0)
            away = int(stats.get("away", 0) or 0)
            teams = int(stats.get("teams", 0) or 0)
            travel_km = int(stats.get("travel_km", 0) or 0)
            review_note = "Sjekk hjemmedatoer og lagliste"
            if hosted == 0:
                review_note = "Mangler hjemmeturnering i planen"
            elif travel_km > 0 and team_travel:
                review_note = "Sjekk anslått reisebelastning og bortedatoer"
            club_rows.append(
                "<tr>"
                f"<td><strong>{_html.escape(club)}</strong></td>"
                f"<td class=\"numeric-cell\">{teams}</td>"
                f"<td class=\"numeric-cell\">{hosted}</td>"
                f"<td class=\"numeric-cell\">{away}</td>"
                f"<td class=\"numeric-cell\">{travel_km}</td>"
                f"<td>{_html.escape(review_note)}</td>"
                "</tr>"
            )
        if not club_rows:
            club_rows.append('<tr><td colspan="6" class="empty-cell">Ingen klubbdata tilgjengelig</td></tr>')

        tournament_rows: list[str] = []
        for tournament in active_tournaments[:120]:
            team_count = len(tournament.teams)
            tournament_rows.append(
                "<tr>"
                f"<td>{_html.escape(fmt_date(tournament.date) or '-')}</td>"
                f"<td><strong>{_html.escape(tournament.age_group)}</strong></td>"
                f"<td>{_html.escape(canonical_rvv_club_name(tournament.host_club or '-') or '-')}</td>"
                f"<td>{_html.escape(tournament.arena)}</td>"
                f"<td class=\"numeric-cell\">{team_count}</td>"
                f"<td class=\"numeric-cell\">{len(tournament.games)}</td>"
                "</tr>"
            )
        if len(active_tournaments) > 120:
            tournament_rows.append(f'<tr><td colspan="6" class="empty-cell">Viser 120 av {len(active_tournaments)} turneringer</td></tr>')
        if not tournament_rows:
            tournament_rows.append('<tr><td colspan="6" class="empty-cell">Ingen turneringer i planen</td></tr>')

        card_defs = [
            ("Planstatus", status_labels.get(overall_status, "STATUS"), f"{len(active_tournaments)} turneringer, {sum(len(t.games) for t in active_tournaments)} kamper"),
            ("Datagrunnlag", f"{source_count} kilder", f"{event_count} kalenderhendelser, {len(blocked)} blokkert"),
            ("Generert", _format_generated_at(generated_at) or "Ikke oppgitt", f"{len(active_tournaments)} turneringer fra denne kjøringen"),
            ("Input", Path(input_path).name if input_path else "input.xlsx", input_path or "Inputfil ikke oppgitt"),
            ("Tidsrom", date_range or "Ikke oppgitt", f"{len(display_age_groups)} aldersgrupper"),
            ("Klubbfordeling", f"{len(host_counts)} vertsklubber", f"{len(missing_hosts)} RVV-klubber uten hjemmeturnering"),
        ]
        status_cards = "".join(
            '<article class="report-card">'
            f'<span>{_html.escape(label)}</span>'
            f'<strong>{_html.escape(value)}</strong>'
            f'<p>{_html.escape(note)}</p>'
            '</article>'
            for label, value, note in card_defs
        )
        action_items = [
            f'<article class="report-action report-action--{_html.escape(status)}"><strong>{_html.escape(label)}</strong><p>{_html.escape(text)}</p></article>'
            for status, label, text in actions
        ]
        actions_html = '<div class="report-action-list">' + "".join(action_items) + '</div>'
        priority_section_html = ''
        if any(status != "pass" for status, _, _ in actions):
            priority_section_html = (
                '<section class="report-section report-section--priority" id="priorityActions">'
                '<div class="section-head">'
                '<div>'
                '<p class="eyebrow">Viktigst først</p>'
                '<h2>Hva må sjekkes eller endres?</h2>'
                '</div>'
                '<p class="section-note">Bare blokkeringer og tydelige avvik vises her; fairness ligger under detaljer.</p>'
                '</div>'
                f'{actions_html}'
                '</section>'
            )
        age_summary = (
            '<div class="table-wrap"><table class="report-table"><thead><tr>'
            '<th>Aldersgruppe</th><th>Turneringer</th><th>Lag</th><th>Vertsklubber</th><th>Kamper per lag</th><th>Datoer</th>'
            '</tr></thead><tbody>' + "".join(age_rows) + '</tbody></table></div>'
        )
        club_summary = (
            '<div class="table-wrap"><table class="report-table"><thead><tr>'
            '<th>Klubb</th><th>Lag</th><th>Hjemme</th><th>Borte</th><th>Anslått kjøreavstand (km)</th><th>Klubben bør sjekke</th>'
            '</tr></thead><tbody>' + "".join(club_rows) + '</tbody></table></div>'
        )
        tournament_table = (
            '<div class="table-wrap"><table class="report-table"><thead><tr>'
            '<th>Dato</th><th>Aldersgruppe</th><th>Vert</th><th>Arena</th><th>Lag</th><th>Kamper</th>'
            '</tr></thead><tbody>' + "".join(tournament_rows) + '</tbody></table></div>'
        )
        rule_transparency_html = fairness_gate_html + fairness_adjustments_html
        advisory_html = review_summary_html
        diagnostics_html = team_stats_html + travel_stats_html

        replacements = {
            "$REPORT_STATUS$": overall_status,
            "$REPORT_STATUS_LABEL$": status_labels.get(overall_status, "STATUS"),
            "$REPORT_ACTION_COUNT$": str(issue_count),
            "$REPORT_ANSWER$": answer,
            "$REPORT_NOTE$": note,
            "$REPORT_STATUS_CARDS$": status_cards,
            "$REPORT_PRIORITY_SECTION$": priority_section_html,
            "$REPORT_RULE_TRANSPARENCY$": rule_transparency_html,
            "$REPORT_CONTEXT$": hidden_context_html,
            "$REPORT_AGE_SUMMARY$": age_summary,
            "$REPORT_CLUB_SUMMARY$": club_summary,
            "$REPORT_ADVISORY$": advisory_html,
            "$REPORT_TOURNAMENT_TABLE$": tournament_table,
            "$REPORT_DIAGNOSTICS$": diagnostics_html,
            "$REPORT_HEATMAP$": heatmap_html,
            "$REPORT_JUDGMENT_CARDS$": judgment_cards_html,
            "$REPORT_SCORES$": scores_html,
            "$REPORT_METRICS$": metrics_html,
            "$REPORT_CLUB_DASHBOARD$": club_dashboard_html,
        }
        html = REPORT_OVERVIEW
        for marker, value in replacements.items():
            html = html.replace(marker, value)
        return html

    @staticmethod
    def _strip_schedule_controls(html: str) -> str:
        """Remove schedule-only filter and count-bar fragments from report pages."""
        html = re.sub(r"\n?\s*<!-- Filters -->\s*<div class=\"filters\">.*?</div>\s*", "\n", html, flags=re.S)
        html = re.sub(r"\n?\s*<!-- Count bar -->\s*<div class=\"count-bar\">.*?</div>\s*", "\n", html, flags=re.S)
        html = re.sub(r"\n{3,}", "\n\n", html)
        return html

    @staticmethod
    def _plan_to_json(plan: SeasonPlan, round_length_for_age_group: dict[str, int] | None = None) -> str:
        """Serialize the plan's tournaments to the compact JSON format used by the HTML."""
        round_length_for_age_group = round_length_for_age_group or {}
        data = []
        for t in plan.tournaments:
            games = [
                [g.home.label, g.away.label, g.parallel_slot, g.round_number]
                for g in t.games
            ]
            bye_data = {
                str(r): labels
                for r, labels in t.get_bye_rounds().items()
            } if t.get_bye_rounds() else {}
            travel = furthest_traveling_team(t)
            travel_str = f"{travel[0].label} ~{travel[1]} km" if travel else ""
            entry: dict[str, object] = {
                "d": t.date.isoformat(),
                "a": t.arena,
                "g": t.age_group,
                "h": t.host_club or "",
                "m": games,
                "b": bye_data,
                "tr": travel_str,
            }
            if t.start_time:
                entry["ts"] = t.start_time
                round_length = round_length_for_age_group.get(t.age_group)
                if round_length:
                    end_time = t.end_time(round_length)
                    if end_time:
                        entry["te"] = end_time
            if t.cancelled:
                entry["cx"] = True
                entry["cr"] = t.cancellation_reason or ""
            if t.manual_booking_reason:
                entry["mb"] = t.manual_booking_reason
            data.append(entry)
        return json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys
    from ..pipeline.state import PipelineState, StageName

    parser = argparse.ArgumentParser(description="Generer interaktiv HTML-oversikt over sesongplanen")
    parser.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory")
    parser.add_argument("--output", default="export/season_plan.html", help="Output HTML path")
    args = parser.parse_args()

    state = PipelineState(args.work_dir)
    plan_ckpt = state.read_stage(StageName.PLANNING)
    if not plan_ckpt or "plan" not in plan_ckpt:
        print("Fant ikke Stage 3-planen - kj\u00f8r Stage 3 f\u00f8rst.", file=sys.stderr)
        sys.exit(1)

    from ..pipeline.stage4_export import _dict_to_plan
    plan = _dict_to_plan(plan_ckpt["plan"])

    exporter = HtmlExporter()
    path = exporter.export(plan, args.output)
    print(f"HTML generert: {path}")
