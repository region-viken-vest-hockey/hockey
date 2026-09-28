r"""Real Pi prompt discovery/expansion smoke check for the shared operate adapter.

A filename matching ``rvv-miniputt:operate`` is not proof that Pi exposes the
command or forwards the request, so this check drives Pi's own prompt loader
(the same ``loadPromptTemplates``/``expandPromptTemplate`` implementation the
TUI uses) through Node against the project ``.pi/prompts`` directory.

This is a harness-lane check: it requires a local Pi installation and Node. It
skips with an explicit missing prerequisite rather than reporting covered
registration.

Reproducible manual check (observed on Pi 0.87.1, Node v22.23.3) from the
repository root:

    pi_pkg=$(node -e "…resolve '@earendil-works/pi-coding-agent' from \$(which pi)…")
    node --input-type=module -e "<probe>" \\
        "$pi_pkg/dist/core/prompt-templates.js" "$PWD" "$PWD/.pi/prompts"

    -> names: ["implement-issue", "rvv-miniputt:operate"]
    -> expanded request contains: move rvv-0037 til 24 januar og bekreft
    -> expanded text references the shared operate procedure and instructions
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.harness

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / ".pi" / "prompts"
PI_PACKAGE = "@earendil-works/pi-coding-agent"

SHARED_INSTRUCTIONS = (
    ".agents/commands/rvv-miniputt/operate.md",
    ".agents/commands/rvv-miniputt/handover.md",
    "AGENTS.md",
    ".agents/skills/rvv/SKILL.md",
)
MULTIWORD_REQUEST = "move rvv-0037 til 24 januar og bekreft"

_RESOLVE_PI_PACKAGE = r"""
const fs = require("fs");
const path = require("path");
let p = fs.realpathSync(process.argv[1]);
while (p !== path.dirname(p)) {
  const manifest = path.join(p, "package.json");
  if (fs.existsSync(manifest)) {
    const data = JSON.parse(fs.readFileSync(manifest, "utf8"));
    if (data.name === process.argv[2]) {
      console.log(p);
      process.exit(0);
    }
  }
  p = path.dirname(p);
}
process.exit(1);
"""

_PROBE = r"""
const { loadPromptTemplates, expandPromptTemplate } = await import(process.argv[1]);
const result = loadPromptTemplates({
  cwd: process.argv[2],
  agentDir: "/nonexistent-agent-dir",
  includeDefaults: false,
  promptPaths: [process.argv[3]],
});
const request = '/rvv-miniputt:operate ' + process.argv[4];
console.log(JSON.stringify({
  names: result.templates.map((template) => template.name),
  diagnostics: result.diagnostics.map((diagnostic) => diagnostic.message),
  expanded: expandPromptTemplate(request, result.templates),
}));
"""


def _require_tool(name: str) -> str:
    location = shutil.which(name)
    if location is None:
        pytest.skip(f"Pi prompt smoke prerequisite missing: {name!r} is not on PATH")
    return location


def _pi_package_root(node: str, pi: str) -> Path:
    result = subprocess.run(
        [node, "-e", _RESOLVE_PI_PACKAGE, os.path.realpath(pi), PI_PACKAGE],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        pytest.skip(f"Pi prompt smoke prerequisite missing: cannot locate {PI_PACKAGE}")
    return Path(result.stdout.strip())


@functools.lru_cache(maxsize=1)
def _probe() -> dict[str, object]:
    node = _require_tool("node")
    pi = _require_tool("pi")
    loader = _pi_package_root(node, pi) / "dist" / "core" / "prompt-templates.js"
    if not loader.exists():
        pytest.skip(f"Pi prompt smoke prerequisite missing: {loader} not found")

    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            _PROBE,
            str(loader),
            str(ROOT),
            str(PROMPTS_DIR),
            MULTIWORD_REQUEST,
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_pi_discovers_the_shared_operate_prompt_from_the_project_tree() -> None:
    probe = _probe()
    assert probe["diagnostics"] == []
    assert "rvv-miniputt:operate" in probe["names"]
    # The generic coding command stays available but separate from the RVV entry point.
    assert "implement-issue" in probe["names"]


def test_pi_expands_the_operate_prompt_and_forwards_the_complete_request() -> None:
    expanded = _probe()["expanded"]
    assert isinstance(expanded, str)
    # A missing template would return the literal request unchanged.
    assert expanded != f"/rvv-miniputt:operate {MULTIWORD_REQUEST}"
    assert MULTIWORD_REQUEST in expanded
    for reference in SHARED_INSTRUCTIONS:
        assert reference in expanded, reference
