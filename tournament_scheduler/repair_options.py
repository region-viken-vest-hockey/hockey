"""Repair option discovery and provider dispatch for maintenance.

This module owns the discovery of repair options for findings and the
dispatch to appropriate repair providers, including option generation,
rejection tracking, and provider family coordination.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .local_repair_options import (
    apply_local_repair_option,
    enumerate_local_repair_options,
)
from .host_team_missing_repair import search_dimensions_from_option_id
from .planning_contract import verify_candidate
from .request_constraints import active_request_constraints
from .season_state import canonical_state_revision, DEFAULT_SEASON_ROOT
from .search_coverage import (
    derive_search_coverage,
    maintenance_search_capability,
)


DEFAULT_DIMENSIONS: Tuple[str, str, str] = ("participants", "host")
SEASON_MAINTENANCE_SCHEMA_VERSION = 1



DEFAULT_DIMENSIONS: Tuple[str, str, str] = ("participants", "host")
SEASON_MAINTENANCE_SCHEMA_VERSION = 1

