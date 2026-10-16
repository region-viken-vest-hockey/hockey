"""Offline tests for scripts/software-release-metadata.py (epic #364, issue #622).

The release helper is exercised with fake packaged-build outputs. The signing
key is an in-memory test signer, never a project key.
"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from tests.distribution_signing_support import TEST_KEY_ID, TEST_PUBLIC_B64, TEST_SIGNER
from tournament_scheduler import distribution_update as du

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "software-release-metadata.py"
COMMIT = "a" * 40


def _load_script():
    spec = importlib.util.spec_from_file_location("software_release_metadata", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


release = _load_script()


def _version(version: str = "2.1.0", distribution: str = "packaged") -> dict:
    return {"distribution": distribution, "name": "rvv-miniputt", "version": version}


def _capabilities(version: str = "2.1.0", protocol: int = 1) -> dict:
    return {
        "name": "rvv-miniputt",
        "application_version": version,
        "distribution": "packaged",
        "agent_protocol_version": protocol,
        "agent_protocol_supported": {"min": 1, "max": 1},
        "canonical_schema_version": {"schedule": 1, "decisions": 1, "physical_layout": 2},
        "claude_runtime_supported": {"min_version": "2.1.0", "max_version_exclusive": "3.0.0"},
        "commands": ["status", "version"],
    }


def _write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def built_app(tmp_path: Path) -> Path:
    app = tmp_path / "dist" / "rvv-miniputt"
    (app / "_internal").mkdir(parents=True)
    (app / "rvv-miniputt.exe").write_bytes(b"MZ-fake-exe")
    (app / "_internal" / "lib.bin").write_bytes(b"x" * 4096)
    return app


@pytest.fixture
def candidate(tmp_path: Path, built_app: Path) -> dict:
    """Run archive + describe the way the workflow does and return the paths."""
    release_dir = tmp_path / "release"
    version_json = _write_json(tmp_path / "version-built.json", _version())
    release.archive(built_app, version_json, release_dir)
    identity = _write_json(
        release_dir / "source-identity.json",
        {"requested_ref": "main", "resolved_kind": "branch", "commit": COMMIT, "clean": True},
    )
    artifact = next(release_dir.glob("rvv-miniputt-2.1.0-windows-x64.zip"))
    release.describe(
        _ns(
            version_json=_write_json(tmp_path / "smoke-version.json", _version()),
            capabilities_json=_write_json(tmp_path / "smoke-capabilities.json", _capabilities()),
            source_identity=identity,
            artifact=artifact,
            repository="owner/repo",
            minimum_write_version="2.0.0",
            minimum_supported_version="1.9.0",
            out_dir=release_dir,
        )
    )
    return {"dir": release_dir, "artifact": artifact}


def _ns(**kwargs):
    import argparse

    return argparse.Namespace(**kwargs)


def test_archive_puts_application_under_one_top_level_folder_with_fixed_timestamps(candidate):
    with zipfile.ZipFile(candidate["artifact"]) as archive:
        names = archive.namelist()
        assert "rvv-miniputt/rvv-miniputt.exe" in names
        assert all(name.startswith("rvv-miniputt/") for name in names)
        assert names == sorted(names)
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_describe_writes_a_manifest_the_client_parser_accepts(candidate):
    manifest_bytes = (candidate["dir"] / "update-manifest.json").read_bytes()
    manifest = du.parse_manifest(manifest_bytes)
    assert manifest.latest_version == "2.1.0"
    assert manifest.minimum_write_version == "2.0.0"
    assert manifest.minimum_supported_version == "1.9.0"
    assert manifest.artifact.url == (
        "https://github.com/owner/repo/releases/download/rvv-miniputt-app-v2.1.0/rvv-miniputt-2.1.0-windows-x64.zip"
    )
    body = json.loads(manifest_bytes)
    assert body["release"]["tag"] == "rvv-miniputt-app-v2.1.0"
    assert body["release"]["source_commit"] == COMMIT
    assert body["release"]["agent_protocol_version"] == 1


def test_checksums_cover_artifact_manifest_and_metadata(candidate):
    lines = (candidate["dir"] / "checksums.txt").read_text(encoding="utf-8").splitlines()
    names = [line.split("  ")[1] for line in lines]
    assert names == sorted(names)
    assert set(names) == {candidate["artifact"].name, "update-manifest.json", "release-metadata.json"}
    for line in lines:
        digest, name = line.split("  ")
        assert release._sha256(candidate["dir"] / name) == digest


def test_signed_manifest_verifies_and_rejects_tampering(candidate, monkeypatch):
    manifest_path = candidate["dir"] / "update-manifest.json"
    sig = du.sign_manifest_bytes(TEST_SIGNER, TEST_KEY_ID, manifest_path.read_bytes())
    sig_path = candidate["dir"] / "update-manifest.json.sig"
    sig_path.write_bytes(sig)
    release.verify(
        _ns(manifest=str(manifest_path), signature=str(sig_path), public_key_b64=TEST_PUBLIC_B64, key_id=TEST_KEY_ID)
    )
    manifest_path.write_bytes(manifest_path.read_bytes().replace(b'"2.0.0"', b'"1.0.0"'))
    with pytest.raises(du.UntrustedManifestError):
        release.verify(
            _ns(manifest=str(manifest_path), signature=str(sig_path), public_key_b64=TEST_PUBLIC_B64, key_id="")
        )


def test_verify_refuses_artifact_that_differs_from_signed_manifest(candidate):
    manifest_path = candidate["dir"] / "update-manifest.json"
    sig_path = candidate["dir"] / "update-manifest.json.sig"
    sig_path.write_bytes(du.sign_manifest_bytes(TEST_SIGNER, TEST_KEY_ID, manifest_path.read_bytes()))
    candidate["artifact"].write_bytes(candidate["artifact"].read_bytes() + b"tamper")
    with pytest.raises(release.ReleaseError, match="size|sha256"):
        release.verify(
            _ns(manifest=str(manifest_path), signature=str(sig_path), public_key_b64=TEST_PUBLIC_B64, key_id="")
        )


def test_archive_refuses_a_source_tree_run_instead_of_a_packaged_build(tmp_path, built_app):
    version_json = _write_json(tmp_path / "v.json", _version(distribution="source"))
    with pytest.raises(release.ReleaseError, match="packaged"):
        release.archive(built_app, version_json, tmp_path / "out")


def test_describe_refuses_dirty_source_identity(tmp_path, built_app):
    release_dir = tmp_path / "release"
    version_json = _write_json(tmp_path / "v.json", _version())
    artifact = release.archive(built_app, version_json, release_dir)
    identity = _write_json(tmp_path / "id.json", {"commit": COMMIT, "clean": False})
    with pytest.raises(release.ReleaseError, match="clean"):
        release.describe(_describe_args(tmp_path, artifact, identity, release_dir))


def test_describe_refuses_mismatched_capabilities_and_bad_floor_order(tmp_path, built_app):
    release_dir = tmp_path / "release"
    version_json = _write_json(tmp_path / "v.json", _version())
    artifact = release.archive(built_app, version_json, release_dir)
    identity = _write_json(tmp_path / "id.json", {"commit": COMMIT, "clean": True})
    args = _describe_args(tmp_path, artifact, identity, release_dir)
    args.capabilities_json = str(_write_json(tmp_path / "cap-other.json", _capabilities(version="9.9.9")))
    with pytest.raises(release.ReleaseError, match="does not match"):
        release.describe(args)
    args = _describe_args(tmp_path, artifact, identity, release_dir)
    args.minimum_write_version = "3.0.0"
    with pytest.raises(release.ReleaseError, match="minimum_supported <= minimum_write <= latest"):
        release.describe(args)


def test_describe_refuses_protocol_outside_supported_range(tmp_path, built_app):
    release_dir = tmp_path / "release"
    version_json = _write_json(tmp_path / "v.json", _version())
    artifact = release.archive(built_app, version_json, release_dir)
    identity = _write_json(tmp_path / "id.json", {"commit": COMMIT, "clean": True})
    args = _describe_args(tmp_path, artifact, identity, release_dir)
    args.capabilities_json = str(_write_json(tmp_path / "cap-proto.json", _capabilities(protocol=7)))
    with pytest.raises(release.ReleaseError, match="protocol"):
        release.describe(args)


def _describe_args(tmp_path: Path, artifact: Path, identity: Path, out_dir: Path):
    return _ns(
        version_json=str(_write_json(tmp_path / "smoke-version.json", _version())),
        capabilities_json=str(_write_json(tmp_path / "smoke-capabilities.json", _capabilities())),
        source_identity=str(identity),
        artifact=str(artifact),
        repository="owner/repo",
        minimum_write_version="2.0.0",
        minimum_supported_version="1.9.0",
        out_dir=str(out_dir),
    )


def test_private_key_is_never_needed_by_the_metadata_helper():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "RVV_RELEASE_SIGNING_KEY" not in text
    assert "load_signing_key" not in text
