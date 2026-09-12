"""Stage 1 validation and parsing helpers."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .input_workbook import load_workbook_config

from ..roster_loader import RosterLoader

logger = logging.getLogger(__name__)


def validate_config(raw: dict[str, Any], input_path: Path) -> list[str]:
    """Validate *raw* config dict and return a list of Norwegian error messages.

    Returns an empty list if the config is valid.

    Parameters
    ----------
    raw:
        Parsed workbook config dict from ``input.xlsx``.
    input_path:
        Path to ``input.xlsx`` itself.  Used to resolve relative team-file
        paths relative to the workbook's directory rather than the process
        working directory.
    """
    errors: list[str] = []

    # --- Obsolete global participation target ---
    # `Innstillinger.deltakelser_per_lag` / `target_tournament_count` used to be
    # a global fallback for every team's participation target. The canonical
    # workbook no longer supports it — the per-age-group before/after-Christmas
    # columns in `Aldersgrupper` are now the single source of truth, so a
    # leftover global setting is rejected rather than silently honoured.
    for legacy_key in ("deltakelser_per_lag", "target_tournament_count"):
        if legacy_key in raw:
            errors.append(
                f"Feltet '{legacy_key}' i 'Innstillinger' er ikke lenger støttet. "
                "Sett hvert lags deltakelsesmål via kolonnene "
                "'deltakelser_per_lag_før_jul' og 'deltakelser_per_lag_etter_jul' "
                "i arket 'Aldersgrupper' i stedet."
            )

    # --- Required fields ---
    if "start_date" not in raw:
        errors.append("Mangler felt 'start_date' (format: ÅÅÅÅ-MM-DD).")
    if "end_date" not in raw:
        errors.append("Mangler felt 'end_date' (format: ÅÅÅÅ-MM-DD).")

    # --- Date parsing ---
    start: date | None = None
    end: date | None = None
    if "start_date" in raw:
        start = _parse_date(raw["start_date"], "start_date", errors)
    if "end_date" in raw:
        end = _parse_date(raw["end_date"], "end_date", errors)

    if start and end:
        if end <= start:
            errors.append(
                f"'end_date' ({raw['end_date']}) må være etter 'start_date' ({raw['start_date']})."
            )
        diff = (end - start).days
        if diff < 7:
            errors.append(
                f"Perioden mellom start_date og end_date er bare {diff} dager — "
                "minst 7 dager er påkrevd for å planlegge turneringer."
            )

    # --- Age groups and age-group keyed settings ---
    defined_age_groups: list[str] = []
    if "age_groups" in raw:
        raw_groups = raw["age_groups"]
        if not isinstance(raw_groups, list):
            errors.append("'age_groups' må være en liste (f.eks. [\"U10\", \"U12\"]).")
        else:
            for ag in raw_groups:
                if not isinstance(ag, str) or not ag.strip():
                    errors.append("'age_groups' må kun inneholde ikke-tomme tekststrenger.")
                else:
                    defined_age_groups.append(ag)

    def _age_group_is_defined(ag: str) -> bool:
        return not defined_age_groups or ag in defined_age_groups

    # --- Parallel games ---
    if "parallel_games" in raw:
        pg = raw["parallel_games"]
        if not isinstance(pg, dict):
            errors.append("'parallel_games' må være et objekt (f.eks. {\"U10\": 3}).")
        else:
            for ag, count in pg.items():
                if not isinstance(ag, str) or not ag.strip():
                    errors.append("'parallel_games' må bruke ikke-tomme tekstnøkler for aldersgrupper.")
                    continue
                if not _age_group_is_defined(ag):
                    errors.append(f"Ukjent aldersgruppe '{ag}' i 'parallel_games'.")
                    continue
                if not isinstance(count, int) or count < 1:
                    errors.append(
                        f"'parallel_games[\"{ag}\"]' må være et positivt heltall, fikk: {count!r}."
                    )

    # --- Round length (minutes) ---
    if "round_length_minutes" in raw:
        rl = raw["round_length_minutes"]
        if not isinstance(rl, dict):
            errors.append(
                "'round_length_minutes' må være et objekt (f.eks. {\"U10\": 10})."
            )
        else:
            for ag, minutes in rl.items():
                if not isinstance(ag, str) or not ag.strip():
                    errors.append("'round_length_minutes' må bruke ikke-tomme tekstnøkler for aldersgrupper.")
                    continue
                if not _age_group_is_defined(ag):
                    errors.append(f"Ukjent aldersgruppe '{ag}' i 'round_length_minutes'.")
                    continue
                if not isinstance(minutes, int) or minutes < 1:
                    errors.append(
                        f"'round_length_minutes[\"{ag}\"]' må være et positivt heltall, fikk: {minutes!r}."
                    )

    # --- Target tournament count by age group ---
    target_by_age = raw.get("participation_targets_by_age_group")
    if target_by_age is not None:
        if not isinstance(target_by_age, dict):
            errors.append(
                "'participation_targets_by_age_group' må være et objekt med aldersgruppe-nøkler."
            )
        else:
            for ag, cfg in target_by_age.items():
                if not isinstance(ag, str) or not ag.strip():
                    errors.append(
                        "'participation_targets_by_age_group' må bruke ikke-tomme tekstnøkler for aldersgrupper."
                    )
                    continue
                if not _age_group_is_defined(ag):
                    errors.append(
                        f"Ukjent aldersgruppe '{ag}' i 'participation_targets_by_age_group'."
                    )
                    continue
                if not isinstance(cfg, dict):
                    errors.append(
                        f"'participation_targets_by_age_group[\"{ag}\"]' må være et objekt med "
                        "nøklene 'before_christmas' og 'after_christmas'."
                    )
                    continue
                before = cfg.get("before_christmas")
                after = cfg.get("after_christmas")
                if before is None or after is None:
                    errors.append(
                        f"'participation_targets_by_age_group[\"{ag}\"]' må oppgi både "
                        "'before_christmas' og 'after_christmas'."
                    )
                    continue
                for field_name, value in (("before_christmas", before), ("after_christmas", after)):
                    # 0 is a legitimate target -- e.g. the
                    # youngest age group intentionally playing no tournaments
                    # before the New Year -- only negative/non-integer values
                    # are rejected.
                    if not isinstance(value, int) or value < 0:
                        errors.append(
                            f"'participation_targets_by_age_group[\"{ag}\"].{field_name}' må være et "
                            f"ikke-negativt heltall, fikk: {value!r}."
                        )

    # --- Every active age group must carry both half-targets ---
    # `deltakelser_per_lag_før_jul` / `_etter_jul` are the authoritative
    # per-team participation targets for every active age group; a missing
    # or partial pair must fail loudly instead of silently falling back to a
    # heuristic/global target.
    if defined_age_groups:
        target_by_age_dict = target_by_age if isinstance(target_by_age, dict) else {}
        for ag in defined_age_groups:
            cfg = target_by_age_dict.get(ag)
            has_before = isinstance(cfg, dict) and cfg.get("before_christmas") is not None
            has_after = isinstance(cfg, dict) and cfg.get("after_christmas") is not None
            if not (has_before and has_after):
                errors.append(
                    f"Aldersgruppen '{ag}' mangler 'deltakelser_per_lag_før_jul' og/eller "
                    "'deltakelser_per_lag_etter_jul' i arket 'Aldersgrupper'. Begge er "
                    "påkrevd for hver aktiv aldersgruppe."
                )

    # --- Teams / roster ---
    if "teams" not in raw:
        errors.append(
            "Mangler felt 'teams'. Oppgi en liste med lag eller en sti til en lagfil."
        )
    else:
        teams_val = raw["teams"]
        if isinstance(teams_val, str):
            # External file reference — resolve relative to the workbook directory
            # so the check is not sensitive to the process working directory.
            teams_path = (input_path.parent / teams_val).resolve()
            if not teams_path.exists():
                errors.append(
                    f"Lagfilen '{teams_val}' finnes ikke. "
                    "Sjekk at stien er riktig."
                )
        elif isinstance(teams_val, list):
            # An empty roster means registration has not started yet.  Treat it
            # as a valid no-op input so the pipeline can still publish the
            # standard placeholder export files instead of failing in Stage 1.
            if teams_val:
                errors.extend(_validate_team_list(teams_val, allowed_age_groups=defined_age_groups or None))
                # The canonical workbook's per-age-group before/after
                # targets are the single participation-target model. A per-team
                # `target_tournament_count` override in `Lag` would silently take
                # precedence over that (see `SeasonPlanner._team_target_tournament_count`),
                # so the canonical pipeline rejects it rather than let it override
                # normal age-group targets unnoticed.
                overridden_labels = sorted(
                    str(t.get("label") or f"#{i + 1}")
                    for i, t in enumerate(teams_val)
                    if isinstance(t, dict) and t.get("target_tournament_count") is not None
                )
                if overridden_labels:
                    errors.append(
                        "Feltet 'target_tournament_count' i arket 'Lag' er ikke støttet i "
                        "kanonisk input.xlsx — deltakelsesmål styres av 'Aldersgrupper'. "
                        f"Fjern verdien for: {', '.join(overridden_labels)}."
                    )
        else:
            errors.append(
                "'teams' må være enten en liste med lag-objekter eller en sti til en lagfil (streng)."
            )

    return errors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_workbook_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load an Excel workbook and return it as the canonical raw config dict.

    Validates that the file exists and has an Excel extension (.xlsx or .xlsm),
    then delegates to load_workbook_config for parsing.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Finner ikke konfigurasjonsfilen '{p}'. Sjekk at stien er riktig."
        )
    if p.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError(
            f"Konfigurasjonsfilen '{p}' må være en Excel-arbeidsbok (.xlsx). "
            "JSON input er ikke lenger støttet som pipeline-standard."
        )
    return load_workbook_config(p)


# Backward-compatibility alias — existing importers can keep using _load_json.
_load_json = _load_workbook_config


def _parse_date(value: Any, field: str, errors: list[str]) -> date | None:
    if not isinstance(value, str):
        errors.append(f"'{field}' må være en tekststreng (format: ÅÅÅÅ-MM-DD), fikk: {value!r}.")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        errors.append(
            f"'{field}' har ugyldig datoformat '{value}'. Bruk ÅÅÅÅ-MM-DD (f.eks. 2025-09-01)."
        )
        return None


def _validate_team_list(teams: list[Any], *, allowed_age_groups: list[str] | None = None) -> list[str]:
    errors: list[str] = []
    required_keys = {"club", "label", "age_group"}
    valid_teams: list[tuple[int, dict[str, Any]]] = []  # (1-based index, team dict)
    for i, team in enumerate(teams):
        prefix = f"Lag #{i + 1}"
        if not isinstance(team, dict):
            errors.append(f"{prefix}: forventet et objekt, fikk {type(team).__name__!r}.")
            continue
        valid_teams.append((i + 1, team))
        missing = required_keys - team.keys()
        if missing:
            errors.append(
                f"{prefix} ('{team.get('label', '?')}'): mangler felt: "
                + ", ".join(f"'{k}'" for k in sorted(missing))
                + "."
            )
        ag = team.get("age_group")
        if allowed_age_groups is not None and ag not in allowed_age_groups:
            errors.append(
                f"{prefix} ('{team.get('label', '?')}'): aldersgruppen '{ag}' finnes ikke i 'age_groups'."
            )
    # Detect duplicate labels within the same age group.
    # The same label is allowed across different age groups — team_key() handles
    # disambiguation in that case (see models.team_key).
    label_ag_to_indices: dict[tuple[str, str], list[int]] = {}
    for idx, team in valid_teams:
        label = team.get("label")
        ag = team.get("age_group") or ""
        if label is not None:
            label_ag_to_indices.setdefault((label, ag), []).append(idx)
    for (label, ag), indices in label_ag_to_indices.items():
        if len(indices) > 1:
            lag_liste = " og ".join(f"Lag #{n}" for n in indices)
            errors.append(
                f"duplikat 'label': {lag_liste} har samme etikett '{label}'"
                + (f" i aldersgruppe '{ag}'" if ag else "")
                + "."
            )
    return errors


def _parse_config(raw: dict[str, Any], input_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Build the Stage 1 checkpoint dict with only **computed** fields.

    Human-editable fields (start_date, end_date, age_groups, parallel_games,
    global planning knobs, participation_targets_by_age_group, sources) are
    intentionally excluded — they live only in ``input.xlsx``.
    """

    rl_dict: dict[str, int] = {}
    if "round_length_minutes" in raw and isinstance(raw["round_length_minutes"], dict):
        rl_dict.update({k: int(v) for k, v in raw["round_length_minutes"].items()})

    # Teams — expand from file reference if needed
    teams_val = raw["teams"]
    if isinstance(teams_val, str):
        roster, _ = RosterLoader.load_with_defaults(teams_val)
        teams_data = [
            {"club": t.club, "label": t.label, "age_group": t.age_group}
            for t in roster.teams
        ]
    else:
        teams_data = list(teams_val)

    ignored_keys = sorted(
        key
        for key in raw
        if key
        not in {
            "start_date",
            "end_date",
            "age_groups",
            "parallel_games",
            "round_length_minutes",
            "teams",
            "sources",
            "target_tournament_count",
            "deltakelser_per_lag",
            "participation_targets_by_age_group",
            "max_hosting_days_per_month",
            "fairness_thresholds",
        }
    )
    if ignored_keys:
        logger.warning(
            "Stage 1 ignorerer ukjente konfigurasjonsfelt: %s",
            ", ".join(ignored_keys),
        )

    result: dict[str, Any] = {
        "input_path": str(Path(input_path).resolve()),
        "teams": teams_data,
        "round_length_minutes": rl_dict,
    }

    if "fairness_thresholds" in raw:
        result["fairness_thresholds"] = dict(raw["fairness_thresholds"])
    target_by_age = raw.get("participation_targets_by_age_group")
    if target_by_age:
        result["participation_targets_by_age_group"] = {
            ag: dict(cfg) for ag, cfg in target_by_age.items()
        }

    return result


# ---------------------------------------------------------------------------
