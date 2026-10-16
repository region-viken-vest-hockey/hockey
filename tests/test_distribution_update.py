"""Update manifest, write gate, staged download and rollback-safe activation (offline, fixture-based)."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from tests.distribution_signing_support import sign, trust
from tournament_scheduler import distribution_update as du
from tournament_scheduler.distribution_update import activation
from tournament_scheduler.cli.rvv_cli import main

URL = "https://releases.example.invalid/rvv-miniputt/windows-stable.json"
SIGNATURE_URL = URL + ".sig"
ARCHIVE_URL = "https://releases.example.invalid/rvv-miniputt/rvv-miniputt-2.1.0-windows-x64.zip"


def _manifest_dict(
    *,
    latest: str = "2.1.0",
    minimum_write: str = "2.0.0",
    minimum_supported: str = "1.9.0",
    sha256: str = "a" * 64,
    size: int = 123,
    filename: str = "rvv-miniputt-2.1.0-windows-x64.zip",
    url: str = ARCHIVE_URL,
) -> dict:
    return {
        "schema": 1,
        "package": "rvv-miniputt",
        "channel": "windows-stable",
        "latest_version": latest,
        "minimum_write_version": minimum_write,
        "minimum_supported_version": minimum_supported,
        "artifact": {"filename": filename, "url": url, "sha256": sha256, "size": size},
    }


def _manifest(**overrides) -> du.UpdateManifest:
    return du.parse_manifest(json.dumps(_manifest_dict(**overrides)).encode())


@pytest.fixture(autouse=True)
def _test_signer_is_the_only_trusted_key(monkeypatch):
    trust(monkeypatch)


class FakeTransport:
    """In-memory transport. ``manifest`` is returned for the manifest URL, its signature (test signer by default) for ``.sig`` URLs; ``archive`` is streamed for downloads."""

    def __init__(
        self,
        *,
        manifest: bytes | None = None,
        archive: bytes | None = None,
        fail: str | None = None,
        signature: bytes | None = None,
    ):
        self.manifest = manifest
        self.archive = archive
        self.fail = fail
        self.signature = signature
        self.downloads: list[str] = []

    def read_bytes(self, url: str, limit: int) -> bytes:
        if self.fail == "network":
            raise du.NetworkError(f"offline: {url}")
        assert self.manifest is not None
        if url.endswith(du.SIGNATURE_SUFFIX):
            return self.signature if self.signature is not None else sign(self.manifest)
        return self.manifest

    def download(self, url: str, dest: Path, max_bytes: int) -> None:
        self.downloads.append(url)
        if self.fail == "network-mid-download":
            dest.write_bytes(self.archive[: len(self.archive) // 2])
            raise du.NetworkError("connection reset")
        dest.write_bytes(self.archive)

    def as_transport(self) -> du.Transport:
        return du.Transport(read_bytes=self.read_bytes, download=self.download)


def _release_archive(tmp_path: Path, *, marker: str = "2.1.0", extra: dict[str, str] | None = None) -> bytes:
    path = tmp_path / f"release-{marker}.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("rvv-miniputt/rvv-miniputt", f"binary {marker}")
        bundle.writestr("rvv-miniputt/_internal/version.txt", marker)
        for name, text in (extra or {}).items():
            bundle.writestr(name, text)
    return path.read_bytes()


def _artifact_for(payload: bytes) -> du.Artifact:
    return du.Artifact(
        filename="rvv-miniputt-2.1.0-windows-x64.zip",
        url=ARCHIVE_URL,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )


# ---------------------------------------------------------------------------
# Versions and manifest format
# ---------------------------------------------------------------------------


def test_versions_compare_numerically_and_trailing_zeros_are_equal():
    assert du.parse_version("2.0") == du.parse_version("2.0.0")
    assert du.parse_version("2.10.0") > du.parse_version("2.9.9")
    with pytest.raises(du.ManifestError):
        du.parse_version("2.0.0-beta")


def test_manifest_round_trips_through_wire_format():
    manifest = _manifest()
    assert du.parse_manifest(json.dumps(du.manifest_to_dict(manifest))) == manifest


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": "ABCDEF"},
        {"sha256": "g" * 64},
        {"url": "http://releases.example.invalid/x.zip"},
        {"filename": "../evil.zip"},
        {"filename": "payload.exe"},
        {"size": 0},
        {"latest": "1.8.0"},  # minimum_write above latest
        {"minimum_supported": "2.0.1"},  # minimum_supported above minimum_write
    ],
)
def test_malformed_manifest_values_are_rejected(overrides):
    with pytest.raises(du.ManifestError):
        _manifest(**overrides)


def test_manifest_for_another_package_or_schema_is_rejected():
    wrong_package = _manifest_dict()
    wrong_package["package"] = "other-tool"
    with pytest.raises(du.ManifestError, match="not for the rvv-miniputt"):
        du.parse_manifest(json.dumps(wrong_package))
    wrong_schema = _manifest_dict()
    wrong_schema["schema"] = 2
    with pytest.raises(du.ManifestError, match="schema"):
        du.parse_manifest(json.dumps(wrong_schema))


def test_non_json_and_oversized_manifests_are_rejected():
    with pytest.raises(du.ManifestError, match="not valid JSON"):
        du.parse_manifest(b"{not json")
    with pytest.raises(du.ManifestError, match="maximum accepted size"):
        du.parse_manifest(b" " * (du.MAX_MANIFEST_BYTES + 1))


# ---------------------------------------------------------------------------
# Classification: current / optional / required / unsupported
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        ("2.1.0", du.UpdateStatus.CURRENT),
        ("2.2.0", du.UpdateStatus.CURRENT),  # newer than the channel is not an update
        ("2.0.0", du.UpdateStatus.UPDATE_AVAILABLE),  # optional: at or above minimum write
        ("1.9.5", du.UpdateStatus.UPDATE_REQUIRED),  # below minimum write, above minimum supported
        ("1.8.9", du.UpdateStatus.UNSUPPORTED),  # below minimum supported
    ],
)
def test_installed_version_is_classified_against_manifest(installed, expected):
    manifest = _manifest(latest="2.1.0", minimum_write="2.0.0", minimum_supported="1.9.0")
    assert du.classify(installed, manifest) is expected


def test_optional_update_keeps_writes_allowed_and_required_update_blocks_them():
    manifest = _manifest(latest="2.1.0", minimum_write="2.0.0", minimum_supported="1.9.0")
    assert du.write_decision("2.0.0", manifest).allowed is True
    assert du.write_decision("2.1.0", manifest).allowed is True
    required = du.write_decision("1.9.9", manifest)
    assert required.allowed is False and required.status is du.UpdateStatus.UPDATE_REQUIRED
    assert "install 2.1.0" in required.reason
    unsupported = du.write_decision("1.8.0", manifest)
    assert unsupported.allowed is False and unsupported.status is du.UpdateStatus.UNSUPPORTED


def test_write_gate_without_cached_manifest_reports_unknown_minimum():
    decision = du.write_decision("2.0.0", None)
    assert decision.allowed is True and decision.status is None
    du.require_write_allowed("2.0.0", None)


def test_require_write_allowed_raises_for_stale_client():
    with pytest.raises(du.WriteBlockedError, match="below the minimum write version"):
        du.require_write_allowed("1.9.0", _manifest(minimum_write="2.0.0"))


# ---------------------------------------------------------------------------
# Check with cache: offline, bad manifest, success
# ---------------------------------------------------------------------------


def test_successful_check_classifies_and_caches_manifest(tmp_path):
    raw = json.dumps(_manifest_dict()).encode()
    result = du.check_for_update("2.0.0", URL, SIGNATURE_URL, FakeTransport(manifest=raw).as_transport(), cache_dir=tmp_path)
    assert result.error is None
    assert result.status is du.UpdateStatus.UPDATE_AVAILABLE
    assert du.load_cached_manifest(tmp_path) == result.manifest


def test_offline_check_reports_error_without_raising_or_touching_cache(tmp_path):
    raw = json.dumps(_manifest_dict(minimum_write="2.0.0")).encode()
    du.save_cached_manifest(tmp_path, raw, sign(raw))
    before = (tmp_path / du.CACHE_FILE_NAME).read_bytes()

    result = du.check_for_update(
        "1.9.0", URL, SIGNATURE_URL, FakeTransport(fail="network").as_transport(), cache_dir=tmp_path
    )

    assert result.status is None and result.manifest is None
    assert "offline" in result.error
    assert (tmp_path / du.CACHE_FILE_NAME).read_bytes() == before
    # The previously cached minimum still gates writes after an offline check.
    assert du.write_decision_from_cache("1.9.0", tmp_path).allowed is False


def test_invalid_manifest_from_source_does_not_replace_cache(tmp_path):
    cached = json.dumps(_manifest_dict(minimum_write="2.0.0")).encode()
    du.save_cached_manifest(tmp_path, cached, sign(cached))
    bad = FakeTransport(manifest=b'{"schema": 1}').as_transport()
    result = du.check_for_update("2.0.0", URL, SIGNATURE_URL, bad, cache_dir=tmp_path)
    assert result.error is not None and result.manifest is None
    assert du.load_cached_manifest(tmp_path).minimum_write_version == "2.0.0"


def test_corrupt_cache_blocks_writes_instead_of_failing_open(tmp_path):
    (tmp_path / du.CACHE_FILE_NAME).write_text("{corrupt", encoding="utf-8")
    decision = du.write_decision_from_cache("2.5.0", tmp_path)
    assert decision.allowed is False
    assert "unusable" in decision.reason


def test_missing_cache_allows_writes_with_unknown_minimum(tmp_path):
    assert du.write_decision_from_cache("2.0.0", tmp_path).allowed is True


# ---------------------------------------------------------------------------
# Staged download, integrity and extraction
# ---------------------------------------------------------------------------


def test_verified_download_lands_under_final_name_only_after_hash_match(tmp_path):
    payload = _release_archive(tmp_path)
    transport = FakeTransport(archive=payload)
    staging = tmp_path / "staging"

    archive = du.download_verified(_artifact_for(payload), staging, transport.as_transport())

    assert archive.read_bytes() == payload
    assert not list(staging.glob("*.partial"))


def test_network_failure_mid_download_leaves_no_activatable_file(tmp_path):
    payload = _release_archive(tmp_path)
    transport = FakeTransport(archive=payload, fail="network-mid-download")
    staging = tmp_path / "staging"

    with pytest.raises(du.NetworkError):
        du.download_verified(_artifact_for(payload), staging, transport.as_transport())

    assert not (staging / "rvv-miniputt-2.1.0-windows-x64.zip").exists()
    assert not list(staging.glob("*.partial"))


def test_hash_mismatch_is_rejected_and_not_staged(tmp_path):
    payload = _release_archive(tmp_path)
    tampered = payload[:-1] + bytes([payload[-1] ^ 1])
    artifact = _artifact_for(payload)
    staging = tmp_path / "staging"

    with pytest.raises(du.IntegrityError, match="SHA-256"):
        du.download_verified(artifact, staging, FakeTransport(archive=tampered).as_transport())

    assert not (staging / artifact.filename).exists()
    assert not list(staging.glob("*.partial"))


def test_size_mismatch_is_rejected(tmp_path):
    payload = _release_archive(tmp_path)
    artifact = du.Artifact(
        filename="rvv-miniputt-2.1.0-windows-x64.zip",
        url=ARCHIVE_URL,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload) + 10,
    )
    with pytest.raises(du.IntegrityError, match="size"):
        du.download_verified(artifact, tmp_path / "staging", FakeTransport(archive=payload).as_transport())


def test_extraction_returns_application_folder(tmp_path):
    archive = tmp_path / "release.zip"
    archive.write_bytes(_release_archive(tmp_path))
    app = du.extract_release(archive, tmp_path / "extracted")
    assert app == tmp_path / "extracted" / "rvv-miniputt"
    assert (app / "rvv-miniputt").read_text() == "binary 2.1.0"


def test_extraction_rejects_path_traversal_and_missing_app_folder(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr("../escape.txt", "x")
    with pytest.raises(du.IntegrityError, match="unsafe archive member"):
        du.extract_release(evil, tmp_path / "out1")
    assert not (tmp_path / "escape.txt").exists()

    no_app = tmp_path / "noapp.zip"
    with zipfile.ZipFile(no_app, "w") as bundle:
        bundle.writestr("other/readme.txt", "x")
    with pytest.raises(du.IntegrityError, match="application folder"):
        du.extract_release(no_app, tmp_path / "out2")


# ---------------------------------------------------------------------------
# Activation, rollback and data separation
# ---------------------------------------------------------------------------


@pytest.fixture
def install(tmp_path):
    """An installed 2.0.0 release, a staged 2.1.0 release, and an operator data root outside the install."""
    install_root = tmp_path / "Programs" / "RVV Miniputt"
    slot = install_root / "app" / "rvv-miniputt"
    slot.mkdir(parents=True)
    (slot / "rvv-miniputt").write_text("binary 2.0.0")

    staged_parent = install_root / "updates" / "2.1.0" / "extracted"
    staged = staged_parent / "rvv-miniputt"
    staged.mkdir(parents=True)
    (staged / "rvv-miniputt").write_text("binary 2.1.0")

    data_root = tmp_path / "RVV Miniputt" / "data"
    (data_root / "season").mkdir(parents=True)
    (data_root / "season" / "canonical.json").write_text('{"revision": 7}')
    (data_root / "export").mkdir()
    (data_root / "export" / "plan.xlsx").write_bytes(b"xlsx")
    return {"install_root": install_root, "slot": slot, "staged": staged, "data_root": data_root}


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_activation_swaps_release_and_keeps_previous_until_confirmed(install):
    du.activate_release(
        install["staged"], install["slot"], install_root=install["install_root"], data_root=install["data_root"]
    )
    assert (install["slot"] / "rvv-miniputt").read_text() == "binary 2.1.0"
    previous = du.previous_release_path(install["slot"])
    assert (previous / "rvv-miniputt").read_text() == "binary 2.0.0"

    du.confirm_release(install["slot"])
    assert not previous.exists()
    assert (install["slot"] / "rvv-miniputt").read_text() == "binary 2.1.0"


def test_activation_never_touches_operator_data(install):
    before = _snapshot(install["data_root"])
    du.activate_release(
        install["staged"], install["slot"], install_root=install["install_root"], data_root=install["data_root"]
    )
    du.confirm_release(install["slot"])
    assert _snapshot(install["data_root"]) == before


def test_failed_swap_restores_previous_release(install, monkeypatch):
    real_rename = activation._rename

    def fail_on_staged_move(src, dst):
        if Path(src) == install["staged"]:
            raise OSError("simulated file lock on the staged release")
        return real_rename(src, dst)

    monkeypatch.setattr(activation, "_rename", fail_on_staged_move)
    with pytest.raises(du.ActivationError, match="could not activate"):
        du.activate_release(
            install["staged"], install["slot"], install_root=install["install_root"], data_root=install["data_root"]
        )
    assert (install["slot"] / "rvv-miniputt").read_text() == "binary 2.0.0"
    assert not du.previous_release_path(install["slot"]).exists()


def test_rollback_restores_previous_after_failed_post_update_check(install):
    du.activate_release(
        install["staged"], install["slot"], install_root=install["install_root"], data_root=install["data_root"]
    )
    du.rollback_release(install["slot"])
    assert (install["slot"] / "rvv-miniputt").read_text() == "binary 2.0.0"
    assert not du.previous_release_path(install["slot"]).exists()
    assert not (install["slot"].with_name("rvv-miniputt.failed")).exists()


def test_rollback_without_previous_release_fails_closed(install):
    with pytest.raises(du.ActivationError, match="no previous release"):
        du.rollback_release(install["slot"])
    assert (install["slot"] / "rvv-miniputt").read_text() == "binary 2.0.0"


def test_interrupted_swap_is_recovered_on_next_activation(install):
    # Simulate a crash after the active release moved aside but before the new one landed.
    previous = du.previous_release_path(install["slot"])
    install["slot"].rename(previous)
    assert not install["slot"].exists()

    du.activate_release(
        install["staged"], install["slot"], install_root=install["install_root"], data_root=install["data_root"]
    )
    assert (install["slot"] / "rvv-miniputt").read_text() == "binary 2.1.0"


def test_data_root_inside_install_folder_is_refused_before_any_rename(install):
    bad_data = install["slot"] / "data"
    bad_data.mkdir()
    with pytest.raises(ValueError, match="must not be inside the application directory"):
        du.activate_release(
            install["staged"], install["slot"], install_root=install["install_root"], data_root=bad_data
        )
    assert (install["slot"] / "rvv-miniputt").read_text() == "binary 2.0.0"
    assert install["staged"].exists()


# ---------------------------------------------------------------------------
# End-to-end offline flow and the read-only CLI surface
# ---------------------------------------------------------------------------


def test_full_offline_fixture_flow_installs_verified_release_only(tmp_path, install):
    payload = _release_archive(tmp_path)
    manifest_raw = json.dumps(_manifest_dict(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))).encode()
    transport = FakeTransport(manifest=manifest_raw, archive=payload).as_transport()

    check = du.check_for_update("2.0.0", URL, SIGNATURE_URL, transport, cache_dir=install["install_root"] / "updates")
    assert check.status is du.UpdateStatus.UPDATE_AVAILABLE

    flow = tmp_path / "flow"
    archive = du.download_verified(check.manifest.artifact, flow, transport)
    app = du.extract_release(archive, flow / "extracted")
    du.activate_release(app, install["slot"], install_root=install["install_root"], data_root=install["data_root"])
    assert (install["slot"] / "rvv-miniputt").read_text() == "binary 2.1.0"


def test_version_cli_reports_update_status_from_local_manifest(tmp_path, capsys):
    manifest_path = tmp_path / "windows-stable.json"
    raw = json.dumps(_manifest_dict(latest="99.0.0", minimum_write="99.0.0")).encode()
    manifest_path.write_bytes(raw)
    (tmp_path / "windows-stable.json.sig").write_bytes(sign(raw))

    assert main(["version", "--manifest", str(manifest_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "rvv-miniputt"
    assert payload["update_status"] == "update-required"
    assert payload["writes_allowed"] is False


def test_version_cli_rejects_unreadable_manifest_with_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["version", "--manifest", str(bad)]) == 2
    assert "Cannot read release manifest" in capsys.readouterr().err
