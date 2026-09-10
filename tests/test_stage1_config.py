"""Tests for tournament_scheduler.pipeline.stage1_config."""

import logging
from pathlib import Path

import openpyxl
import pytest

from tournament_scheduler.pipeline.stage1_config import (
    Stage1Error,
    load_effective_config,
    run,
    validate_config,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName


def _make_valid_raw():
    return {
        "start_date": "2025-09-01",
        "end_date": "2025-12-01",
        "teams": [
            {"club": "Kongsberg", "label": "Kongsberg U10A", "age_group": "U10"},
            {"club": "Skien",     "label": "Skien U10A",     "age_group": "U10"},
        ],
    }


def _write_input_workbook(path: Path, raw: dict | None = None) -> None:
    raw = raw or _make_valid_raw()
    wb = openpyxl.Workbook()
    settings = wb.active
    settings.title = "Innstillinger"
    settings.append(["felt", "verdi"])
    for key in (
        "start_date",
        "end_date",
        "target_tournament_count",
        "deltakelser_per_lag",
        "max_hosting_days_per_month",
    ):
        if key in raw:
            settings.append([key, raw[key]])

    if "age_groups" in raw:
        age_groups = wb.create_sheet("Aldersgrupper")
        header_cols = ["age_group", "parallel_games", "round_length_minutes"]
        target_by_age = raw.get("target_tournament_counts_by_age_group", {})
        has_age_targets = any(target_by_age.get(age_group) for age_group in raw["age_groups"])
        if has_age_targets:
            header_cols.extend([
                "deltakelser_per_lag_før_jul",
                "deltakelser_per_lag_etter_jul",
            ])
        age_groups.append(header_cols)
        for age_group in raw["age_groups"]:
            row = [
                age_group,
                raw.get("parallel_games", {}).get(age_group, 3),
                raw.get("round_length_minutes", {}).get(age_group, 10),
            ]
            if has_age_targets:
                target = target_by_age.get(age_group, {})
                row.extend([
                    target.get("before_christmas"),
                    target.get("after_christmas"),
                ])
            age_groups.append(row)

    teams = wb.create_sheet("Lag")
    header_cols = ["club", "label", "age_group"]
    team_has_ttc = any("target_tournament_count" in team for team in raw.get("teams", []))
    if team_has_ttc:
        header_cols.append("target_tournament_count")
    teams.append(header_cols)
    for team in raw.get("teams", []):
        row = [team.get("club"), team.get("label"), team.get("age_group")]
        if team_has_ttc:
            row.append(team.get("target_tournament_count"))
        teams.append(row)

    sources = wb.create_sheet("Kilder")
    sources.append(["name", "type", "url"])
    for source in raw.get("sources", [
        {"name": "Kongsberg", "type": "outlook", "url": "https://example.test/calendar"}
    ]):
        sources.append([source.get("name"), source.get("type"), source.get("url")])

    wb.save(path)


_DUMMY_INPUT_PATH = Path("/tmp/input.xlsx")


class TestValidateConfig:
    def test_valid_config_has_no_errors(self):
        assert validate_config(_make_valid_raw(), _DUMMY_INPUT_PATH) == []

    def test_missing_start_date(self):
        raw = _make_valid_raw()
        del raw["start_date"]
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert any("start_date" in e for e in errors)

    def test_missing_end_date(self):
        raw = _make_valid_raw()
        del raw["end_date"]
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert any("end_date" in e for e in errors)

    def test_end_before_start_produces_error(self):
        raw = _make_valid_raw()
        raw["end_date"] = "2025-08-01"
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert any("end_date" in e for e in errors)

    def test_period_too_short(self):
        raw = _make_valid_raw()
        raw["start_date"] = "2025-09-01"
        raw["end_date"] = "2025-09-03"
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert any("dager" in e for e in errors)

    def test_invalid_date_format(self):
        raw = _make_valid_raw()
        raw["start_date"] = "01.09.2025"
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert any("ÅÅÅÅ-MM-DD" in e or "format" in e.lower() for e in errors)

    def test_missing_teams(self):
        raw = _make_valid_raw()
        del raw["teams"]
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert any("teams" in e for e in errors)

    def test_empty_teams_list_is_allowed_for_not_started_exports(self):
        raw = _make_valid_raw()
        raw["teams"] = []
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert errors == []

    def test_team_age_group_must_exist_in_age_groups_sheet(self):
        raw = _make_valid_raw()
        raw["age_groups"] = ["U10"]
        raw["teams"][0]["age_group"] = "U99"
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert any("age_groups" in e or "U99" in e for e in errors)

    def test_duplicate_label_produces_norwegian_error(self):
        raw = _make_valid_raw()
        # Give both teams the same label to trigger duplicate detection
        raw["teams"][0]["label"] = "Kongsberg U10A"
        raw["teams"][1]["label"] = "Kongsberg U10A"
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert errors, "Expected at least one error for duplicate team label"
        assert any("duplikat" in e and "Kongsberg U10A" in e for e in errors), (
            f"Expected Norwegian duplicate-label error containing 'duplikat' and the label name; got: {errors}"
        )

    def test_unique_labels_no_extra_errors(self):
        """A valid config with all unique labels produces no errors."""
        raw = _make_valid_raw()
        assert validate_config(raw, _DUMMY_INPUT_PATH) == []

    def test_invalid_parallel_games_type(self):
        raw = _make_valid_raw()
        raw["parallel_games"] = "lots"
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert any("parallel_games" in e for e in errors)

    def test_parallel_games_must_be_positive(self):
        raw = _make_valid_raw()
        raw["parallel_games"] = {"U10": 0}
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert any("positivt heltall" in e for e in errors)

    def test_error_messages_are_norwegian(self):
        errors = validate_config({}, _DUMMY_INPUT_PATH)
        # Norwegian error messages should contain Norwegian words
        combined = " ".join(errors)
        assert any(word in combined for word in ["Mangler", "felt", "Oppgi"])

    def test_age_group_target_counts_are_validated(self):
        raw = _make_valid_raw()
        raw["target_tournament_counts_by_age_group"] = {
            "U10": {"before_christmas": 3, "after_christmas": 1},
        }
        errors = validate_config(raw, _DUMMY_INPUT_PATH)
        assert errors == []

    def test_teams_file_found_relative_to_input_dir(self, tmp_path):
        """A teams file that exists relative to the input dir passes validation."""
        (tmp_path / "teams.xlsx").touch()
        raw = _make_valid_raw()
        raw["teams"] = "teams.xlsx"
        errors = validate_config(raw, tmp_path / "input.xlsx")
        assert not any("finnes ikke" in e for e in errors)

    def test_teams_file_not_found_produces_error(self, tmp_path):
        """A teams file that does not exist relative to the input dir is reported."""
        raw = _make_valid_raw()
        raw["teams"] = "missing_teams.xlsx"
        errors = validate_config(raw, tmp_path / "input.xlsx")
        assert any("finnes ikke" in e for e in errors)


class TestRunStage1:
    def test_run_writes_checkpoint_on_success(self, tmp_path):
        input_file = tmp_path / "input.xlsx"
        _write_input_workbook(input_file)

        state = PipelineState(tmp_path / "pipeline")
        result = run(input_file, state)

        assert state.is_done(StageName.CONFIG)
        assert "teams" in result
        assert len(result["teams"]) == 2
        assert all(not key.startswith("derived_") for key in result)

    def test_run_warns_when_ignoring_unknown_config_keys(self, tmp_path, caplog):
        input_file = tmp_path / "input.xlsx"
        _write_input_workbook(input_file)
        workbook = openpyxl.load_workbook(input_file)
        workbook["Innstillinger"].append(["targt_tournament_count", 7])
        workbook.save(input_file)

        state = PipelineState(tmp_path / "pipeline")
        with caplog.at_level(logging.WARNING, logger="tournament_scheduler.pipeline.stage1_helpers"):
            result = run(input_file, state)

        assert result["teams"]
        assert any("targt_tournament_count" in record.message for record in caplog.records)

    def test_run_does_not_warn_for_supported_workbook_level_planning_settings(self, tmp_path, caplog):
        input_file = tmp_path / "input.xlsx"
        raw = _make_valid_raw()
        raw["deltakelser_per_lag"] = 5
        raw["max_hosting_days_per_month"] = 2
        _write_input_workbook(input_file, raw)

        state = PipelineState(tmp_path / "pipeline")
        with caplog.at_level(logging.WARNING, logger="tournament_scheduler.pipeline.stage1_helpers"):
            result = run(input_file, state)
        effective = load_effective_config(state, input_path=input_file)

        assert result["teams"]
        assert effective["target_tournament_count"] == 5
        assert effective["max_hosting_days_per_month"] == 2
        assert not any("deltakelser_per_lag" in record.message for record in caplog.records)
        assert not any("max_hosting_days_per_month" in record.message for record in caplog.records)

    def test_target_tournament_count_takes_precedence_over_norwegian_alias(self, tmp_path):
        input_file = tmp_path / "input.xlsx"
        raw = _make_valid_raw()
        raw["target_tournament_count"] = 6
        raw["deltakelser_per_lag"] = 4
        _write_input_workbook(input_file, raw)

        state = PipelineState(tmp_path / "pipeline")
        run(input_file, state)
        effective = load_effective_config(state, input_path=input_file)

        assert effective["target_tournament_count"] == 6

    def test_run_accepts_excel_workbook_input(self, tmp_path):
        input_file = tmp_path / "input.xlsx"
        raw = _make_valid_raw()
        raw["age_groups"] = ["U10"]
        raw["parallel_games"] = {"U10": 3}
        raw["round_length_minutes"] = {"U10": 10}
        _write_input_workbook(input_file, raw)

        state = PipelineState(tmp_path / "pipeline")
        result = run(input_file, state)
        effective = load_effective_config(state, input_path=input_file)
        checkpoint = state.read_stage(StageName.CONFIG)

        assert state.is_done(StageName.CONFIG)
        assert len(result["teams"]) == 2
        assert checkpoint["teams"] == result["teams"]
        assert "start_date" not in checkpoint
        assert result["input_path"] == str(input_file.resolve())
        assert effective["start_date"] == "2025-09-01"
        assert effective["end_date"] == "2025-12-01"
        assert effective["age_groups"] == ["U10"]
        assert effective["parallel_games"] == {"U10": 3}
        assert effective["sources"] == [
            {"name": "Kongsberg", "type": "outlook", "url": "https://example.test/calendar"}
        ]

    def test_run_stores_workbook_and_effective_config_fingerprints(self, tmp_path):
        input_file = tmp_path / "input.xlsx"
        _write_input_workbook(input_file)

        state = PipelineState(tmp_path / "pipeline")
        run(input_file, state)
        checkpoint = state.read_stage(StageName.CONFIG)

        assert checkpoint["input_fingerprint"]["algorithm"] == "sha256"
        assert checkpoint["input_fingerprint"]["path"] == str(input_file.resolve())
        assert len(checkpoint["input_fingerprint"]["sha256"]) == 64
        assert checkpoint["effective_config_fingerprint"]["algorithm"] == "sha256"
        assert len(checkpoint["effective_config_fingerprint"]["sha256"]) == 64

    def test_stage1_fingerprints_change_when_workbook_roster_changes(self, tmp_path):
        input_file = tmp_path / "input.xlsx"
        raw = _make_valid_raw()
        _write_input_workbook(input_file, raw)

        state = PipelineState(tmp_path / "pipeline")
        run(input_file, state)
        first = state.read_stage(StageName.CONFIG)

        raw["teams"].append({"club": "Jar", "label": "Jar U10A", "age_group": "U10"})
        _write_input_workbook(input_file, raw)
        run(input_file, state)
        second = state.read_stage(StageName.CONFIG)

        assert second["input_fingerprint"]["sha256"] != first["input_fingerprint"]["sha256"]
        assert second["effective_config_fingerprint"]["sha256"] != first["effective_config_fingerprint"]["sha256"]

    def test_run_reports_missing_required_workbook_sheet(self, tmp_path):
        input_file = tmp_path / "input.xlsx"
        wb = openpyxl.Workbook()
        wb.active.title = "Innstillinger"
        wb.save(input_file)

        state = PipelineState(tmp_path / "pipeline")
        with pytest.raises(ValueError) as exc_info:
            run(input_file, state)

        assert "Lag" in str(exc_info.value)

    def test_run_preserves_per_team_target_tournament_count(self, tmp_path):
        """The per-team `target_tournament_count` column in the Lag sheet is preserved."""
        raw = _make_valid_raw()
        raw["teams"] = [
            {"club": "Kongsberg", "label": "Kongsberg 1", "age_group": "U10"},
            {"club": "Kongsberg", "label": "Kongsberg 2", "age_group": "U7", "target_tournament_count": 2},
            {"club": "Jar", "label": "Jar 1", "age_group": "U10", "target_tournament_count": 6},
        ]
        input_file = tmp_path / "input.xlsx"
        _write_input_workbook(input_file, raw)

        state = PipelineState(tmp_path / "pipeline")
        result = run(input_file, state)
        teams = result["teams"]
        # Team without target
        kong1 = next(t for t in teams if t["label"] == "Kongsberg 1")
        assert "target_tournament_count" not in kong1 or kong1.get("target_tournament_count") is None
        # Team with target=2
        kong2 = next(t for t in teams if t["label"] == "Kongsberg 2")
        assert kong2["target_tournament_count"] == 2
        # Team with target=6
        jar1 = next(t for t in teams if t["label"] == "Jar 1")
        assert jar1["target_tournament_count"] == 6

    def test_run_preserves_per_age_group_target_tournament_counts(self, tmp_path):
        """The per-age-group target columns in the Aldersgrupper sheet are preserved."""
        raw = _make_valid_raw()
        raw["age_groups"] = ["U7", "U10"]
        raw["parallel_games"] = {"U7": 4, "U10": 3}
        raw["target_tournament_counts_by_age_group"] = {
            "U7": {"before_christmas": 3, "after_christmas": 5},
            "U10": {"before_christmas": 4, "after_christmas": 6},
        }
        input_file = tmp_path / "input.xlsx"
        _write_input_workbook(input_file, raw)

        state = PipelineState(tmp_path / "pipeline")
        result = run(input_file, state)
        effective = load_effective_config(state, input_path=input_file)

        assert result["target_tournament_counts_by_age_group"] == raw["target_tournament_counts_by_age_group"]
        assert effective["target_tournament_counts_by_age_group"] == raw["target_tournament_counts_by_age_group"]
        checkpoint = state.read_stage(StageName.CONFIG)
        assert checkpoint["target_tournament_counts_by_age_group"] == raw["target_tournament_counts_by_age_group"]

    def test_run_rejects_json_input(self, tmp_path):
        input_file = tmp_path / "legacy.json"
        input_file.write_text("{}", encoding="utf-8")

        state = PipelineState(tmp_path / "pipeline")
        with pytest.raises(ValueError) as exc_info:
            run(input_file, state)

        assert "Excel" in str(exc_info.value)

    def test_run_does_not_inject_fallback_age_groups_when_input_omits_them(self, tmp_path):
        raw = _make_valid_raw()
        input_file = tmp_path / "input.xlsx"
        _write_input_workbook(input_file, raw)
        state = PipelineState(tmp_path / "pipeline")
        result = run(input_file, state)

        assert all(not key.startswith("derived_") for key in result)
        effective = load_effective_config(state, input_path=input_file)
        assert effective.get("age_groups") == []
        assert effective.get("age_groups_from_input") is False

    def test_run_raises_on_invalid_config(self, tmp_path):
        raw = {"start_date": "bad", "end_date": "also-bad", "teams": []}
        input_file = tmp_path / "input.xlsx"
        _write_input_workbook(input_file, raw)

        state = PipelineState(tmp_path / "pipeline")
        with pytest.raises(Stage1Error) as exc_info:
            run(input_file, state)
        assert len(exc_info.value.errors) > 0

    def test_run_raises_on_missing_field_no_checkpoint(self, tmp_path):
        raw = _make_valid_raw()
        del raw["start_date"]
        input_file = tmp_path / "input.xlsx"
        _write_input_workbook(input_file, raw)

        state = PipelineState(tmp_path / "pipeline")
        with pytest.raises(Stage1Error):
            run(input_file, state)
        assert not state.checkpoint_path(StageName.CONFIG).exists()

    def test_run_raises_on_missing_file(self, tmp_path):
        state = PipelineState(tmp_path / "pipeline")
        with pytest.raises(FileNotFoundError):
            run(tmp_path / "nonexistent.xlsx", state)

    def test_run_strict_false_does_not_raise(self, tmp_path):
        raw = {}  # invalid but strict=False
        input_file = tmp_path / "input.xlsx"
        _write_input_workbook(input_file, raw)

        state = PipelineState(tmp_path / "pipeline")
        # Should not raise
        result = run(input_file, state, strict=False)
        # Result may be empty or partial
        assert isinstance(result, dict)
