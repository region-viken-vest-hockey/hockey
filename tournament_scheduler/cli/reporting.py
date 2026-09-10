"""RVV CLI log/status reporting helpers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rich.console import Console

_console = Console()

_STAGE_FILES = [
    ("Stage 1 (Config)", "stage1_config.json"),
    ("Stage 2 (Scraping)", "stage2_scraping.json"),
    ("Stage 3 (Planning)", "stage3_planning.json"),
    ("Stage 4 (Export)", "stage4_export.json"),
]
_STAGE_LABELS = {
    "config": "Konfigurasjon",
    "scraping": "Skraping",
    "planning": "Planlegging",
    "export": "Eksport",
}


def _read_checkpoint(work_dir: Path, filename: str) -> dict[str, Any] | None:
    candidates = [filename, "stage3_plan.json"] if filename == "stage3_planning.json" else [filename]
    for candidate in candidates:
        path = work_dir / candidate
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def _format_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    minutes, remainder = divmod(ms, 60000)
    return f"{minutes}m {round(remainder / 1000)}s"


def _load_jsonl_entries(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries



def _candidate_log_paths(work_dir: Path) -> list[Path]:
    paths: list[Path] = []
    # Keep authoritative log history in the export tree; legacy
    # ``.pipeline/logs`` files are intentionally not part of the lookup.
    for export_root in (work_dir / "export", work_dir.parent / "export"):
        if export_root.exists():
            paths.extend(sorted(export_root.rglob("run-*.jsonl"), reverse=True))

    deduped: dict[str, Path] = {}
    for path in paths:
        run_id = path.stem
        if run_id not in deduped:
            deduped[run_id] = path
            continue
        existing = deduped[run_id]
        if "export" in path.parts and "export" not in existing.parts:
            deduped[run_id] = path
            continue
        if path.stat().st_mtime > existing.stat().st_mtime:
            deduped[run_id] = path
    return sorted(deduped.values(), key=lambda path: path.stat().st_mtime, reverse=True)



def _find_log_path(work_dir: Path, run_id: str) -> Path | None:
    for path in _candidate_log_paths(work_dir):
        if path.stem == run_id:
            return path
    return None



def _load_run_history(work_dir: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for log_path in _candidate_log_paths(work_dir):
        run_id = log_path.stem
        meta = None
        for entry in reversed(_load_jsonl_entries(log_path)):
            if entry.get("type") == "run_meta" and entry.get("run_id") == run_id and entry.get("end_time"):
                meta = entry
                break
        runs.append({"run_id": run_id, "log_path": log_path, "meta": meta})
    return runs


def _build_status_text(work_dir: Path) -> str:
    try:
        from ..pipeline.state import PipelineState

        PipelineState(work_dir).invalidate_if_config_fingerprint_changed()
    except Exception:
        # Status should remain inspectable even if stale detection itself fails.
        pass

    lines = [f"Pipeline work-dir: {work_dir}", ""]
    for label, filename in _STAGE_FILES:
        checkpoint = _read_checkpoint(work_dir, filename)
        if not checkpoint:
            lines.append(f"  {label}: pending (no checkpoint)")
            continue

        status = checkpoint.get("status", "unknown")
        updated = checkpoint.get("updated_at", "")
        stale = checkpoint.get("stale")
        stale_from = checkpoint.get("stale_from", "?")
        stale_suffix = f"  (stale from {stale_from})" if stale else ""
        updated_suffix = f"  ({updated})" if updated else ""
        lines.append(f"  {label}: {status}{stale_suffix}{updated_suffix}")

        data = checkpoint.get("data") or {}
        if label.startswith("Stage 2"):
            blocked = data.get("blocked") or []
            if blocked:
                lines.append(f"    Blokkerte kilder: {', '.join(blocked)}")
            expectation_warnings = data.get("event_expectation_warnings") or []
            if expectation_warnings:
                lines.append(f"    Mistenkelig få kalenderhendelser: {len(expectation_warnings)} kilde(r)")
                for warning in expectation_warnings[:3]:
                    if isinstance(warning, dict):
                        message = warning.get("message") or (
                            f"{warning.get('name', 'ukjent kilde')}: "
                            f"{warning.get('event_count', '?')} vs forventet minst "
                            f"{warning.get('expected_min_events', '?')}"
                        )
                        lines.append(f"      - {message}")
                if len(expectation_warnings) > 3:
                    lines.append(f"      - ... og {len(expectation_warnings) - 3} flere")
        if label.startswith("Stage 3"):
            plan_dict = data.get("plan") if isinstance(data, dict) else None
            if isinstance(plan_dict, dict):
                try:
                    from .plan_critic import count_critic_issues_from_dict
                    n = count_critic_issues_from_dict(plan_dict)
                    if n:
                        lines.append(f"    Critic: {n} issue(s) found — run 'rvv-miniputt critic' for details")
                    else:
                        lines.append("    Critic: no issues")
                except Exception:
                    pass
                gate = plan_dict.get("fairness_gate") if isinstance(plan_dict.get("fairness_gate"), dict) else {}
                for note in gate.get("notes", []) if isinstance(gate, dict) else []:
                    if isinstance(note, dict) and note.get("key") == "missing_calendar_clubs":
                        excluded = note.get("excluded_clubs") or []
                        if excluded:
                            lines.append(f"    Manglende kalenderdata (utelatt fra belastningsvurdering): {', '.join(excluded)}")
                        break
                else:
                    for metric in gate.get("metrics", []) if isinstance(gate, dict) else []:
                        if isinstance(metric, dict) and metric.get("key") == "missing_calendar_clubs":
                            excluded = metric.get("excluded_clubs") or []
                            if excluded:
                                lines.append(f"    Manglende kalenderdata (utelatt fra belastningsvurdering): {', '.join(excluded)}")
                            break
        if label.startswith("Stage 4"):
            output_files = data.get("output_files") or {}
            for key, path in output_files.items():
                lines.append(f"    {key}: {path}")

    runs = _load_run_history(work_dir)
    if runs:
        lines.extend(["", f"Logs: {runs[0]['log_path'].parent}", f"  Siste {min(3, len(runs))} kjøringer:"])
        for run in runs[:3]:
            lines.append(f"    • {run['run_id']}.jsonl")

    return "\n".join(lines)


def _resolve_run_id(work_dir: Path, requested: str | None) -> str | None:
    if not requested or requested == "latest":
        runs = _load_run_history(work_dir)
        return runs[0]["run_id"] if runs else None
    return requested


def _build_logs_list_text(work_dir: Path, count: int) -> str:
    runs = _load_run_history(work_dir)[:count]
    log_root = runs[0]["log_path"].parent if runs else (work_dir.parent / "export")
    if not runs:
        return f"Ingen loggførte kjøringer funnet i {log_root}/"

    lines = [
        "=== Pipeline kjøringshistorie ===",
        f"Logg-katalog: {log_root}/",
        f"Viser {len(runs)} siste kjøringer",
        "",
        f"{'Kjøring'.ljust(30)} {'Status'.ljust(12)} {'Varighet'.ljust(12)} {'Starter'.ljust(22)}",
        f"{'─' * 30} {'─' * 12} {'─' * 12} {'─' * 22}",
    ]
    for run in runs:
        meta = run["meta"] or {}
        status = meta.get("exit_status", "ukjent")
        duration = _format_duration(meta["duration_ms"]) if meta.get("duration_ms") else "─"
        start = (meta.get("start_time") or "─")[:19].replace("T", " ")
        lines.append(f"{run['run_id'].ljust(30)} {status.ljust(12)} {duration.ljust(12)} {start}")
    return "\n".join(lines)


def _build_logs_show_text(work_dir: Path, run_id: str) -> str:
    log_path = _find_log_path(work_dir, run_id)
    if not log_path:
        return f"Kjøring {run_id} ikke funnet i eksporttreet."

    entries = _load_jsonl_entries(log_path)
    run_meta = next((entry for entry in reversed(entries) if entry.get("type") == "run_meta" and entry.get("run_id") == run_id and entry.get("end_time")), None)
    stage_entries = [entry for entry in entries if entry.get("type") == "stage_meta"]
    llm_entries = [entry for entry in entries if entry.get("type") == "llm_interaction"]
    update_entries = [entry for entry in entries if entry.get("type") == "tournament_update"]

    lines = [f"=== Kjørings-detalj: {run_id} ===", f"Logg-fil: {log_path.name}", ""]
    if run_meta:
        lines.append(f"Status:      {run_meta.get('exit_status', 'ukjent')}")
        if run_meta.get("duration_ms") is not None:
            lines.append(f"Varighet:    {_format_duration(run_meta['duration_ms'])}")
        lines.append(f"Start:       {(run_meta.get('start_time') or '─')[:19].replace('T', ' ')}")
        lines.append(f"Slutt:       {(run_meta.get('end_time') or '─')[:19].replace('T', ' ')}")
        commit = (run_meta.get("git_commit") or "─")[:8]
        dirty = " (dirty)" if run_meta.get("git_dirty") else ""
        lines.append(f"Git commit:  {commit}{dirty}")
        lines.append(f"Gjenopptok:  Trinn {run_meta.get('resume_from', '─')}")
        argv = " ".join(
            f"--{key.replace('_', '-')} {value}" for key, value in (run_meta.get("args") or {}).items() if value is not None
        )
        if argv:
            lines.append(f"Argv:        {argv}")
        lines.append("")

    lines.extend([
        "Stadier:",
        f"{'#'.ljust(4)} {'Stage'.ljust(16)} {'Status'.ljust(10)} {'Varighet'.ljust(12)} Feil",
        f"{'─' * 4} {'─' * 16} {'─' * 10} {'─' * 12} {'─' * 20}",
    ])
    for entry in stage_entries:
        index = f"{entry.get('stage_index', '?')}."
        name = _STAGE_LABELS.get(entry.get("stage_name"), entry.get("stage_name", "?"))
        status = entry.get("status")
        icon = "✓" if status == "ok" else "─" if status == "skipped" else "✗"
        duration = _format_duration(entry["duration_ms"]) if entry.get("duration_ms") else "─"
        error = (entry.get("error") or "")[:40]
        lines.append(f"{index.ljust(4)} {name.ljust(16)} {icon.ljust(10)} {duration.ljust(12)} {error}")
        data_volume = entry.get("data_volume") or {}
        if data_volume:
            volume = ", ".join(f"{key}: {value}" for key, value in data_volume.items())
            lines.append(f"    Data: {volume}")

    if llm_entries:
        token_calls = 0
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        for entry in llm_entries:
            prompt = int(entry.get("prompt_tokens") or 0)
            completion = int(entry.get("completion_tokens") or 0)
            total = int(entry.get("total_tokens") or entry.get("tokens") or 0)
            if not prompt and not completion and not total:
                continue
            token_calls += 1
            prompt_tokens += prompt
            completion_tokens += completion
            total_tokens += total or (prompt + completion)

        lines.extend(["", f"LLM-interaksjoner ({len(llm_entries)}):"])
        lines.append(f"  Tokenbruk: kall={token_calls}, prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")
        for entry in llm_entries[:10]:
            confidence = f" (confidence: {entry['confidence']})" if entry.get("confidence") is not None else ""
            tokens = f" [{entry['tokens']} tokens]" if entry.get("tokens") is not None else ""
            lines.append(f"  • {entry.get('stage_name', '?')}: {entry.get('action', '?')}{confidence}{tokens}")
        if len(llm_entries) > 10:
            lines.append(f"  ... og {len(llm_entries) - 10} flere")

    if update_entries:
        lines.extend(["", f"Turneringsoppdateringer ({len(update_entries)}):"])
        for entry in update_entries[:10]:
            op = entry.get("operation", "?")
            label = "Fjern lag" if op == "team_drop" else "Flytt dato" if op == "date_move" else op
            verdict = "✓" if entry.get("success") is True else "✗" if entry.get("success") is False else "?"
            first_line = (entry.get("summary_nb") or "").splitlines()[0][:80]
            lines.append(f"  {verdict} [{entry.get('tournament_id', '?')}] {label}: {first_line}")
        if len(update_entries) > 10:
            lines.append(f"  ... og {len(update_entries) - 10} flere")

    return "\n".join(lines)


def _build_logs_stats_text(work_dir: Path) -> str:
    runs = _load_run_history(work_dir)
    if not runs:
        return "Ingen loggførte kjøringer funnet i eksporttreet."

    success_runs = [run for run in runs if (run["meta"] or {}).get("exit_status") == "success"]
    failed_runs = [run for run in runs if (run["meta"] or {}).get("exit_status") == "failure"]
    total_duration = sum((run["meta"] or {}).get("duration_ms", 0) for run in runs)
    average_duration = round(total_duration / len(runs)) if runs else 0

    token_calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    lines = [
        "=== Pipeline selvforbedrings-statistikk ===",
        "",
        f"Totalt antall kjøringer: {len(runs)}",
        f"Vellykkede:              {len(success_runs)}",
        f"Feil:                    {len(failed_runs)}",
        f"Feilrate:                {round((len(failed_runs) / len(runs)) * 100) if runs else 0}%",
        f"Gjennomsnittlig varighet: {_format_duration(average_duration)}",
        f"Siste kjøring:           {(((runs[0]['meta'] or {}).get('start_time')) or '─')[:10]}",
        "",
    ]

    stage_stats: dict[str, dict[str, int]] = {}
    for run in runs:
        log_path = run["log_path"]
        for entry in _load_jsonl_entries(log_path):
            if entry.get("type") != "stage_meta" or not entry.get("duration_ms"):
                continue
            stats = stage_stats.setdefault(entry["stage_name"], {"count": 0, "total_ms": 0, "fails": 0})
            stats["count"] += 1
            stats["total_ms"] += int(entry["duration_ms"])
            if entry.get("status") == "failed":
                stats["fails"] += 1

        for entry in _load_jsonl_entries(log_path):
            if entry.get("type") != "llm_interaction":
                continue
            prompt = int(entry.get("prompt_tokens") or 0)
            completion = int(entry.get("completion_tokens") or 0)
            total = int(entry.get("total_tokens") or entry.get("tokens") or 0)
            if not prompt and not completion and not total:
                continue
            token_calls += 1
            prompt_tokens += prompt
            completion_tokens += completion
            total_tokens += total or (prompt + completion)

    if token_calls > 0:
        lines.extend([
            "Tokenbruk (alle kjøringer):",
            f"  LLM-kall: {token_calls}",
            f"  Prompt:   {prompt_tokens}",
            f"  Completion: {completion_tokens}",
            f"  Total:    {total_tokens}",
            "",
        ])

    if stage_stats:
        lines.extend([
            "Stage-statistikk:",
            f"{'Stage'.ljust(20)} {'Kjøringer'.ljust(12)} {'Gj.snitt'.ljust(12)} {'Feil'.ljust(8)} Feilrate",
            f"{'─' * 20} {'─' * 12} {'─' * 12} {'─' * 8} {'─' * 8}",
        ])
        for name, stats in stage_stats.items():
            avg = _format_duration(round(stats['total_ms'] / stats['count']))
            fail_rate = f"{round((stats['fails'] / stats['count']) * 100)}%" if stats["count"] else "0%"
            lines.append(f"{name.ljust(20)} {str(stats['count']).ljust(12)} {avg.ljust(12)} {str(stats['fails']).ljust(8)} {fail_rate}")
        lines.append("")

    recent_runs = [run for run in runs[:5] if (run["meta"] or {}).get("duration_ms")]
    if len(recent_runs) >= 2:
        lines.append("Varighetstrend (siste 5 kjøringer):")
        for run in recent_runs:
            meta = run["meta"] or {}
            lines.append(f"  {(meta.get('start_time') or '??')[:10]}  {_format_duration(meta['duration_ms'])}  ({meta.get('exit_status', 'ukjent')})")
        first = (recent_runs[-1]["meta"] or {}).get("duration_ms", 0)
        last = (recent_runs[0]["meta"] or {}).get("duration_ms", 0)
        if first and last:
            pct = round(((last - first) / first) * 100)
            arrow = "↓" if pct < -5 else "↑" if pct > 5 else "→"
            lines.append(f"  Trend: {arrow} {abs(pct)}% ({_format_duration(first)} → {_format_duration(last)})")

    return "\n".join(lines)


def _cmd_status(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        from ..pipeline.run_manifest import RunManifest

        manifest = RunManifest(Path(args.work_dir)).read()
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return 0
    _console.print(_build_status_text(Path(args.work_dir)))
    return 0


_SOURCE_STATUS_ICON = {"ok": "✓", "warning": "⚠", "blocked": "⛔", "failed": "✗"}
_SOURCE_STATUS_STYLE = {"ok": "green", "warning": "yellow", "blocked": "red", "failed": "red"}


def _cmd_sources_status(args: argparse.Namespace) -> int:
    from ..pipeline.source_health import compute_source_health

    results = compute_source_health(args.work_dir)

    if getattr(args, "json", False):
        print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
        return 0

    if not results:
        _console.print(
            f"Ingen Stage 2-sjekkpunkt funnet i {args.work_dir}/. "
            "Kjør 'rvv-miniputt run' eller 'rvv-miniputt operator run' først."
        )
        return 0

    _console.print(f"[bold]Kildehelse[/bold] ({len(results)} kilde(r))\n")
    for result in results:
        icon = _SOURCE_STATUS_ICON.get(result.status, "?")
        style = _SOURCE_STATUS_STYLE.get(result.status, "white")
        name = result.capability.split(":", 1)[-1] if ":" in result.capability else result.capability
        _console.print(f"[{style}]{icon}[/{style}] [bold]{name}[/bold] — {result.summary}")
        for item in result.evidence:
            _console.print(f"    [dim]{item}[/dim]")
        for problem in result.problems:
            _console.print(f"    [yellow]· {problem}[/yellow]")
        for action in result.suggested_actions:
            _console.print(f"    [cyan]→ {action}[/cyan]")
        _console.print()

    blocked_or_warning = [r for r in results if r.status != "ok"]
    return 1 if any(r.status == "blocked" for r in blocked_or_warning) else 0


def _cmd_logs(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir)
    subcommand = getattr(args, "logs_command", "list") or "list"
    if subcommand == "show":
        run_id = _resolve_run_id(work_dir, getattr(args, "run_id", None))
        if not run_id:
            _console.print("Ingen loggførte kjøringer funnet i eksporttreet.")
            return 0
        _console.print(_build_logs_show_text(work_dir, run_id))
        return 0
    if subcommand == "stats":
        _console.print(_build_logs_stats_text(work_dir))
        return 0

    count = getattr(args, "count", 10) or 10
    _console.print(_build_logs_list_text(work_dir, count))
    return 0


_CANDIDATE_STATUS_STYLE = {"pass": "green", "warn": "yellow", "fail": "red", "failed": "red"}


def _read_stage3_checkpoint(work_dir: Path) -> dict[str, Any] | None:
    checkpoint = _read_checkpoint(work_dir, "stage3_planning.json")
    if not checkpoint:
        return None
    return checkpoint.get("data") or {}


def _most_consequential_metric_deltas(
    selected: dict[str, Any], runner_up: dict[str, Any], *, limit: int = 3
) -> list[tuple[str, float, float, float]]:
    """Return the *limit* fairness metrics with the largest score delta.

    Each tuple is ``(label, selected_score, runner_up_score, delta)`` sorted
    by ``abs(delta)`` descending, comparing the selected candidate against
    the next-best-ranked candidate.
    """
    selected_by_key = {m["key"]: m for m in selected.get("metrics", []) if isinstance(m, dict)}
    runner_up_by_key = {m["key"]: m for m in runner_up.get("metrics", []) if isinstance(m, dict)}

    deltas: list[tuple[str, float, float, float]] = []
    for key, metric in selected_by_key.items():
        other = runner_up_by_key.get(key)
        if other is None:
            continue
        selected_score = float(metric.get("score", 0))
        runner_up_score = float(other.get("score", 0))
        delta = selected_score - runner_up_score
        if delta != 0:
            deltas.append((str(metric.get("label", key)), selected_score, runner_up_score, delta))

    deltas.sort(key=lambda item: abs(item[3]), reverse=True)
    return deltas[:limit]


def _build_candidates_text(work_dir: Path) -> str:
    data = _read_stage3_checkpoint(work_dir)
    if not data or not data.get("candidates"):
        return (
            f"Ingen kandidatdata funnet i {work_dir}/. "
            "Kjør 'rvv-miniputt run --iterations N' (N > 1) for å generere flere kandidater."
        )

    candidates: list[dict[str, Any]] = data["candidates"]
    selected_attempt = data.get("selected_candidate_attempt")

    lines = [f"=== Plankandidater ({len(candidates)}) ===", ""]
    lines.append(f"{'Forsøk'.ljust(8)} {'Seed'.ljust(8)} {'Status'.ljust(8)} {'Score'.ljust(7)} {'Turneringer'.ljust(12)} Valgt")
    lines.append(f"{'─' * 8} {'─' * 8} {'─' * 8} {'─' * 7} {'─' * 12} {'─' * 5}")
    for candidate in candidates:
        attempt = candidate.get("attempt")
        seed = candidate.get("seed")
        status = str(candidate.get("status", "?"))
        score = candidate.get("score")
        count = candidate.get("tournament_count", 0)
        marker = "←" if attempt == selected_attempt else ""
        lines.append(
            f"{str(attempt).ljust(8)} {str(seed if seed is not None else 'default').ljust(8)} "
            f"{status.ljust(8)} {str(score if score is not None else '-').ljust(7)} {str(count).ljust(12)} {marker}"
        )

    ranked = sorted(
        (c for c in candidates if c.get("rank") is not None),
        key=lambda c: tuple(c["rank"]),
        reverse=True,
    )
    if len(ranked) >= 2:
        selected, runner_up = ranked[0], ranked[1]
        deltas = _most_consequential_metric_deltas(selected, runner_up)
        if deltas:
            lines.append("")
            lines.append(
                f"Mest utslagsgivende forskjeller (forsøk {selected['attempt']} vs. forsøk {runner_up['attempt']}):"
            )
            for label, sel_score, other_score, delta in deltas:
                sign = "+" if delta > 0 else ""
                lines.append(f"  {label}: {sel_score:.0f} vs {other_score:.0f} ({sign}{delta:.0f})")

    fingerprints = candidates[0]
    lines.append("")
    lines.append(f"Planner-versjon: {fingerprints.get('planner_version', '?')}")
    lines.append(f"Config-fingerprint: {str(fingerprints.get('config_fingerprint', '?'))[:16]}...")
    lines.append(f"Kilde-fingerprint: {str(fingerprints.get('source_fingerprint', '?'))[:16]}...")

    return "\n".join(lines)


def _cmd_candidates(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir)
    if getattr(args, "json", False):
        data = _read_stage3_checkpoint(work_dir) or {}
        print(json.dumps(
            {
                "candidates": data.get("candidates", []),
                "selected_candidate_attempt": data.get("selected_candidate_attempt"),
            },
            indent=2,
            ensure_ascii=False,
        ))
        return 0
    _console.print(_build_candidates_text(work_dir))
    return 0
