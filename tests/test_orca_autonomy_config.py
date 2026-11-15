"""Keep hockey operator-controlled even when shared Orca gains a Campsty product manager."""

from __future__ import annotations

import json
from pathlib import Path


def test_hockey_orca_product_autonomy_is_explicitly_controlled() -> None:
    path = Path(__file__).resolve().parents[1] / "orca.config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["autonomy"] == {"profile": "controlled-execution"}
    assert config["gate"] == {"command": "make", "args": ["check"]}
    assert config["protectedDomains"], "Hockey must never drop protected-domain rules"
