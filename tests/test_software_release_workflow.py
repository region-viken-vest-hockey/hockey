"""Structural invariants for the manual software-release workflow (epic #364, issue #622).

These tests parse the workflow statically. They do not run GitHub Actions. They
exist so that the release rules cannot regress silently: manual dispatch only,
a software-specific tag namespace separate from season publication (#549),
publication gated on smoke tests and signing, no secret echo, and minimal
write permissions.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "software-release.yml"
METADATA_SCRIPT = ROOT / "scripts" / "software-release-metadata.py"


def _load_workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(data: dict[str, Any]) -> dict[str, Any]:
    # PyYAML applies YAML 1.1 booleans, so `on` may parse as True.
    return data.get("on", data.get(True, {}))


def _run_blocks(job: dict[str, Any]) -> list[str]:
    return [step["run"] for step in job.get("steps", []) if "run" in step]


def _metadata_tag_prefix() -> str:
    spec = importlib.util.spec_from_file_location("software_release_metadata", METADATA_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.SOFTWARE_TAG_PREFIX


def test_workflow_is_manual_dispatch_only():
    data = _load_workflow()
    assert set(_triggers(data)) == {"workflow_dispatch"}


def test_dispatch_inputs_and_mode_choices():
    inputs = _triggers(_load_workflow())["workflow_dispatch"]["inputs"]
    assert {"ref", "mode", "minimum_write_version", "minimum_supported_version", "confirm_publish"} <= set(inputs)
    assert inputs["mode"]["type"] == "choice"
    assert inputs["mode"]["options"] == ["build-only", "publish"]
    assert inputs["mode"]["default"] == "build-only"
    # Floors are maintainer decisions and must be stated explicitly at dispatch.
    assert inputs["minimum_write_version"]["required"] is True
    assert inputs["minimum_supported_version"]["required"] is True


def test_permissions_are_minimal_and_only_publish_can_write_contents():
    data = _load_workflow()
    assert data["permissions"] == {"contents": "read"}
    for name, job in data["jobs"].items():
        permissions = job.get("permissions", data["permissions"])
        if name == "publish":
            assert permissions == {"contents": "write"}
        else:
            assert permissions.get("contents") == "read", name
            assert "write" not in permissions.values(), name


def test_publish_is_gated_on_mode_smoke_and_signing():
    jobs = _load_workflow()["jobs"]
    publish = jobs["publish"]
    assert publish["if"] == "${{ inputs.mode == 'publish' }}"
    assert set(publish["needs"]) == {"build", "sign"}
    assert publish["environment"] == "software-release"
    # Smoke tests run inside the build job; signing needs the build job's output.
    assert jobs["sign"]["needs"] == "build"
    build_steps = [step.get("name", "") for step in jobs["build"]["steps"]]
    smoke_index = next(i for i, name in enumerate(build_steps) if "Smoke-test" in name)
    describe_index = next(i for i, name in enumerate(build_steps) if "Describe the release" in name)
    assert smoke_index < describe_index


def test_only_the_publish_job_creates_releases_and_publish_requires_confirmation():
    text = WORKFLOW.read_text(encoding="utf-8")
    jobs = _load_workflow()["jobs"]
    for name, job in jobs.items():
        joined = "\n".join(_run_blocks(job))
        if name != "publish":
            assert "gh release" not in joined, name
    publish_run = "\n".join(_run_blocks(jobs["publish"]))
    assert "gh release create" in publish_run
    assert "PUBLISER" in text
    assert text.index("gh release create") < text.index("gh release edit")
    assert "--latest" in publish_run and publish_run.index("--latest") > publish_run.index("gh release create")


def test_software_tag_prefix_is_namespaced_and_matches_metadata_script():
    prefix = _metadata_tag_prefix()
    assert prefix == "rvv-miniputt-app-v"
    assert not prefix.startswith("season")
    assert not re.match(r"^v?\d", prefix)
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "rvv-miniputt-app-v*" in text
    assert "season" not in " ".join(re.findall(r"tag[^\n]*", text)).lower()


def test_signing_key_is_only_an_environment_secret_and_never_echoed():
    data = _load_workflow()
    text = WORKFLOW.read_text(encoding="utf-8")
    sign = data["jobs"]["sign"]
    assert sign["environment"] == "software-release"
    sign_env = next(step["env"] for step in sign["steps"] if step.get("name") == "Sign the manifest")
    assert sign_env["RVV_RELEASE_SIGNING_KEY"] == "${{ secrets.RVV_RELEASE_SIGNING_KEY }}"
    # The secret may only reach a process through env, never through an interpolated run script.
    for job in data["jobs"].values():
        for script in _run_blocks(job):
            assert "secrets." not in script
            assert not re.search(r"echo[^\n]*RVV_RELEASE_SIGNING_KEY", script)
    assert text.count("secrets.RVV_RELEASE_SIGNING_KEY") == 1
    assert "set -x" not in text


def test_user_inputs_reach_scripts_through_env_not_interpolation():
    for job in _load_workflow()["jobs"].values():
        for step in job.get("steps", []):
            for script in [step.get("run", "")]:
                assert "${{ inputs." not in script, step.get("name")


def test_actions_use_major_version_tags_like_existing_workflows():
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert uses
    for ref in uses:
        assert re.match(r"^[\w.-]+/[\w.-]+@v\d+$", ref), ref
    for step_ref in re.findall(r"uses:\s*actions/checkout@\S+", text):
        assert "persist-credentials: false" in text


def test_build_refuses_dirty_or_ambiguous_source():
    build_steps = _load_workflow()["jobs"]["build"]["steps"]
    assert any(step.get("run") == "scripts/software-release-source.sh" for step in build_steps)
    source_text = (ROOT / "scripts" / "software-release-source.sh").read_text(encoding="utf-8")
    assert "refs/remotes/origin/" in source_text and "refs/tags/" in source_text
    assert "exactly one branch or tag" in source_text
    assert "source tree is dirty" in source_text
    assert "the build modified the source tree" in "\n".join(_run_blocks(_load_workflow()["jobs"]["build"]))
    assert "packaging/windows/rvv-miniputt.spec" in "\n".join(_run_blocks(_load_workflow()["jobs"]["build"]))


def test_release_metadata_comes_from_the_built_application_and_is_checked_before_publish():
    build_text = "\n".join(_run_blocks(_load_workflow()["jobs"]["build"]))
    assert 'rvv-miniputt.exe" version --json' in build_text
    assert '"$exe" capabilities --json' in build_text
    assert '"$exe" version --json' in build_text
    assert "software-release-metadata.py describe" in build_text
    publish_text = "\n".join(_run_blocks(_load_workflow()["jobs"]["publish"]))
    assert "sha256sum -c checksums.txt" in publish_text
    assert "update-manifest.json.sig" in publish_text
    assert "already exists" in publish_text
