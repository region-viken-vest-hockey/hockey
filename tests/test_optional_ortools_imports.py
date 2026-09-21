"""Import smoke tests for helpers that must not require optional OR-Tools."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_pipeline_helper_imports_do_not_require_ortools():
    script = textwrap.dedent(
        """
        import builtins

        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name.startswith("ortools"):
                raise ImportError("no ortools in this test")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocked_import

        from tournament_scheduler.pipeline.activity_export import has_activity_table
        from tournament_scheduler.pipeline.registration_sync import sync_registered_teams_to_workbook
        from tournament_scheduler.pipeline.state import PipelineState
        from tournament_scheduler.pipeline import PipelineState as ExportedPipelineState
        from tournament_scheduler import game_generation

        assert has_activity_table is not None
        assert sync_registered_teams_to_workbook is not None
        assert PipelineState is ExportedPipelineState
        assert callable(game_generation.generate_round_robin_games)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
