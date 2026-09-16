import pytest

from tournament_scheduler.canonical_baseline import SEASON_ROOT_ENV_VAR
from tournament_scheduler.testing.canonical_input import (
    build_canonical_planner,
    canonical_input_has_teams,
    load_canonical_input_data,
    load_canonical_roster,
    load_canonical_season_window,
)


@pytest.fixture(autouse=True)
def _isolate_ambient_canonical_season_root(monkeypatch):
    """Keep planning/verification tests independent of local canonical state.

    A developer machine may have a deliberately promoted ``season/``
    directory, and planning/verification otherwise resolve canonical state
    from that root by default -- which would make the suite's result depend
    on unrelated local operator state.  Tests that exercise canonical
    resolution pass an explicit ``root``/``canonical_season_root`` and are
    unaffected.
    """
    monkeypatch.setenv(SEASON_ROOT_ENV_VAR, "/nonexistent/rvv-canonical-season")


@pytest.fixture
def canonical_input_data(request):
    data = load_canonical_input_data()
    if request.node.get_closest_marker("slow") and not data.get("teams"):
        pytest.skip("canonical input.xlsx has no registered teams")
    return data


@pytest.fixture
def canonical_roster():
    if not canonical_input_has_teams():
        pytest.skip("canonical input.xlsx has no registered teams")
    return load_canonical_roster()


@pytest.fixture
def canonical_season_window():
    return load_canonical_season_window()


# Canonical planner fixtures exercise the real input.xlsx roster and may build
# the full Stage 3 plan. Tests that use them should opt into pytest.mark.slow;
# run explicitly with: python3 -m pytest -m slow --no-cov
@pytest.fixture
def canonical_planner():
    if not canonical_input_has_teams():
        pytest.skip("canonical input.xlsx has no registered teams")
    planner, start, end = build_canonical_planner()
    return planner, start, end


@pytest.fixture
def canonical_plan(canonical_planner):
    planner, start, end = canonical_planner
    plan = getattr(planner, "_canonical_plan", None)
    if plan is None:
        plan = planner.build_plan(start, end)
    return planner, plan, start, end
