"""Interactive HTML overview for the season plan.

Reads a :class:`~tournament_scheduler.models.SeasonPlan` and generates a
standalone, interactive HTML page showing all tournaments, filtering by
age group / arena / club / search, and expandable match tables.

HTML is assembled from template fragments in ``templates/``.
Data computation lives in :mod:`data_computation`; rendering helpers
live in :mod:`renderers`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from tournament_scheduler.club_distances import furthest_traveling_team
from ..models import SeasonPlan

from .data_computation import (
    ICON_CALENDAR,
    ICON_CLIPBOARD,
    ICON_USERS,
    ICON_TARGET,
    ICON_TRAVEL,
    ICON_WARNING,
    ICON_BAR_CHART,
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
from .renderers.heatmap import build_club_color_maps
from .renderers.rules_table import render_rules_sections_html
from ..rules_model import build_rules_model

# ---------------------------------------------------------------------------
# Load template fragments
# ---------------------------------------------------------------------------

from .templates import (
    STYLES_CSS,
    NAVBAR,
    HEADER,
    FILTERS,
    HEATMAP,
    REPORT_OVERVIEW,
    PAGE_TEMPLATE,
    SHARED_JAVASCRIPT,
    SCHEDULE_JAVASCRIPT,
    COUNT_BAR,
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
        rules = build_rules_model(plan)
        for rule in rules:
            if rule.get("id") == "metric_travel_distance" and team_travel:
                rule["detail_rows"] = {
                    "label": "Vis reise per lag",
                    "kind": "travel",
                    "rows": [{"team": label, "km": km} for label, km in team_travel.items()],
                }
        rules_table_html = render_rules_sections_html(rules)
        report_overview_html = self._report_overview_html(rules_table_html)
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
                # Heatmap belongs on the operational season-plan view near the
                # top, not repeated on the report (issue #277). It renders via
                # the same $HEATMAP_JSON$ data/script on both pages, so this
                # only controls whether the markup slot is present.
                "$HEATMAP$": HEATMAP if not include_diagnostics else "",
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
            page_title="Regler",
            page_subtitle=f"RVV Hockey &mdash; {' + '.join(display_age_groups)} &middot; hva styrte planen?",
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
    def _report_overview_html(rules_table_html: str) -> str:
        """Render the Regler view: the canonical rules table plus its compact summary.

        Everything else the page used to show (percentage quality score,
        age-group/club/tournament summary tables, full diagnostics
        accordion, generic advisory section) was redundant with
        ``season_plan.html`` / ``manual_schedule.html`` and has been removed
        (issue #305) -- this page's only job is to answer "what governed the
        plan, was it satisfied, what needs manual action, and what
        trade-offs were accepted?".
        """
        html = REPORT_OVERVIEW
        return html.replace("$REPORT_RULES_TABLE$", rules_table_html)


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
