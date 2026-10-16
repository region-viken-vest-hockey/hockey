"""Ed25519 manifest signing: verification, untrusted metadata, key rotation and the signing helper (offline)."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.distribution_signing_support import (
    TEST_KEY_ID,
    TEST_PUBLIC_B64,
    TEST_SIGNER,
    sign,
    trust,
)
from tournament_scheduler import distribution_update as du
from tournament_scheduler.cli.rvv_cli import main

@pytest.fixture(autouse=True)
def _test_signer_is_the_only_trusted_key(monkeypatch):
    trust(monkeypatch)


URL = "https://releases.example.invalid/rvv-miniputt/windows-stable.json"
SIGNATURE_URL = URL + ".sig"
SIGNER_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sign-release-manifest.py"


def _raw(**overrides) -> bytes:
    data = {
        "schema": 1,
        "package": "rvv-miniputt",
        "channel": "windows-stable",
        "latest_version": "2.1.0",
        "minimum_write_version": "2.0.0",
        "minimum_supported_version": "1.9.0",
        "artifact": {
            "filename": "rvv-miniputt-2.1.0-windows-x64.zip",
            "url": "https://releases.example.invalid/rvv-miniputt-2.1.0-windows-x64.zip",
            "sha256": "a" * 64,
            "size": 1000,
        },
    }
    data.update(overrides)
    return json.dumps(data, sort_keys=True).encode("utf-8")


class _Transport:
    def __init__(self, manifest: bytes, signature: bytes):
        self.manifest = manifest
        self.signature = signature

    def read_bytes(self, url: str, limit: int) -> bytes:
        return self.signature if url.endswith(du.SIGNATURE_SUFFIX) else self.manifest

    def download(self, url: str, dest: Path, max_bytes: int) -> None:  # pragma: no cover - not used here
        raise AssertionError("no download expected")

    def as_transport(self) -> du.Transport:
        return du.Transport(read_bytes=self.read_bytes, download=self.download)


def _other_key_pair() -> tuple[Ed25519PrivateKey, str]:
    key = Ed25519PrivateKey.generate()
    return key, du.public_key_b64(key)


def test_valid_signature_is_accepted_and_manifest_is_parsed(monkeypatch):
    raw = _raw()
    assert du.verify_manifest_signature(raw, sign(raw)) == TEST_KEY_ID
    assert du.parse_verified_manifest(raw, sign(raw)) == du.parse_manifest(raw)


def test_tampered_manifest_is_rejected_before_any_field_is_used(tmp_path):
    raw = _raw()
    signature = sign(raw)
    tampered = raw.replace(b'"minimum_write_version": "2.0.0"', b'"minimum_write_version": "1.0.0"')
    assert tampered != raw

    with pytest.raises(du.UntrustedManifestError, match="does not verify"):
        du.parse_verified_manifest(tampered, signature)
    result = du.check_for_update("1.5.0", URL, SIGNATURE_URL, _Transport(tampered, signature).as_transport(), tmp_path)
    assert result.status is du.UpdateStatus.UNTRUSTED
    assert result.manifest is None
    assert not (tmp_path / du.CACHE_FILE_NAME).exists()


def test_unknown_key_id_is_rejected(monkeypatch):
    raw = _raw()
    other_key, other_public = _other_key_pair()
    trust(monkeypatch, {"rotated-out": other_public})
    with pytest.raises(du.UntrustedManifestError, match="not trusted"):
        du.verify_manifest_signature(raw, sign(raw))


def test_signature_by_a_different_key_under_a_trusted_id_is_a_bad_signature(monkeypatch):
    raw = _raw()
    impostor, _ = _other_key_pair()
    with pytest.raises(du.UntrustedManifestError, match="does not verify"):
        du.verify_manifest_signature(raw, sign(raw, key=impostor))


def test_flipped_signature_bits_are_rejected():
    raw = _raw()
    envelope = json.loads(sign(raw))
    signature = bytearray(base64.b64decode(envelope["signature"]))
    signature[0] ^= 0x01
    envelope["signature"] = base64.b64encode(bytes(signature)).decode("ascii")
    with pytest.raises(du.UntrustedManifestError, match="does not verify"):
        du.verify_manifest_signature(raw, json.dumps(envelope).encode())


@pytest.mark.parametrize(
    "signature_bytes",
    [
        b"",
        b"not json",
        b"[]",
        b'{"schema": 1, "algorithm": "ed25519", "key_id": "test-release-key"}',
        b'{"schema": 1, "algorithm": "rsa", "key_id": "test-release-key", "signature": "AAAA"}',
        b'{"schema": 2, "algorithm": "ed25519", "key_id": "test-release-key", "signature": "AAAA"}',
        b'{"schema": 1, "algorithm": "ed25519", "key_id": "bad id!", "signature": "AAAA"}',
        b'{"schema": 1, "algorithm": "ed25519", "key_id": "test-release-key", "signature": "not base64!!"}',
        b'{"schema": 1, "algorithm": "ed25519", "key_id": "test-release-key", "signature": "AAAA"}',
        b'{"schema": 1, "algorithm": "ed25519", "key_id": "test-release-key", "signature": "AAAA", "x": 1}',
        b" " * (du.MAX_SIGNATURE_BYTES + 1),
    ],
)
def test_malformed_signature_envelopes_are_rejected(signature_bytes):
    with pytest.raises(du.UntrustedManifestError):
        du.verify_manifest_signature(_raw(), signature_bytes)


def test_wrong_length_public_key_in_trust_root_is_untrusted(monkeypatch):
    trust(monkeypatch, {TEST_KEY_ID: base64.b64encode(b"short").decode("ascii")})
    with pytest.raises(du.UntrustedManifestError):
        du.verify_manifest_signature(_raw(), sign(_raw()))


def test_additive_rotation_accepts_old_and_new_key_and_retired_key_is_refused(monkeypatch):
    new_key, new_public = _other_key_pair()
    old_public = TEST_PUBLIC_B64
    trust(monkeypatch, {"rvv-release-old": old_public, "rvv-release-new": new_public})
    raw = _raw()

    assert du.verify_manifest_signature(raw, sign(raw, key=TEST_SIGNER, key_id="rvv-release-old")) == "rvv-release-old"
    assert du.verify_manifest_signature(raw, sign(raw, key=new_key, key_id="rvv-release-new")) == "rvv-release-new"

    trust(monkeypatch, {"rvv-release-new": new_public})
    with pytest.raises(du.UntrustedManifestError, match="not trusted"):
        du.verify_manifest_signature(raw, sign(raw, key=TEST_SIGNER, key_id="rvv-release-old"))


def test_empty_trust_root_offers_no_update(monkeypatch, tmp_path):
    trust(monkeypatch, {})
    raw = _raw()
    result = du.check_for_update("1.5.0", URL, SIGNATURE_URL, _Transport(raw, sign(raw)).as_transport(), tmp_path)
    assert result.status is du.UpdateStatus.UNTRUSTED
    assert result.manifest is None
    assert not (tmp_path / du.CACHE_FILE_NAME).exists()


def test_untrusted_manifest_cannot_lower_the_write_floor(tmp_path):
    trusted = _raw(minimum_write_version="2.0.0")
    du.save_cached_manifest(tmp_path, trusted, sign(trusted))
    cached_before = (tmp_path / du.CACHE_FILE_NAME).read_bytes()
    attacker_key, _ = _other_key_pair()
    forged = _raw(minimum_write_version="1.0.0", minimum_supported_version="1.0.0")

    for signature in (sign(forged, key=attacker_key), sign(forged, key_id="unknown-key")):
        result = du.check_for_update("1.5.0", URL, SIGNATURE_URL, _Transport(forged, signature).as_transport(), tmp_path)
        assert result.status is du.UpdateStatus.UNTRUSTED
        assert result.manifest is None

    assert (tmp_path / du.CACHE_FILE_NAME).read_bytes() == cached_before
    decision = du.write_decision_from_cache("1.95.0", tmp_path)
    assert decision.allowed is False
    assert "minimum write version 2.0.0" in decision.reason


def test_locally_edited_cache_is_unusable_and_blocks_writes(tmp_path):
    trusted = _raw(minimum_write_version="2.0.0")
    du.save_cached_manifest(tmp_path, trusted, sign(trusted))
    edited = trusted.replace(b'"minimum_write_version": "2.0.0"', b'"minimum_write_version": "1.0.0"')
    (tmp_path / du.CACHE_FILE_NAME).write_bytes(edited)

    decision = du.write_decision_from_cache("1.5.0", tmp_path)
    assert decision.allowed is False
    assert "unusable" in decision.reason


def test_save_cache_refuses_untrusted_metadata(tmp_path):
    raw = _raw()
    attacker_key, _ = _other_key_pair()
    with pytest.raises(du.UntrustedManifestError):
        du.save_cached_manifest(tmp_path, raw, sign(raw, key=attacker_key))
    assert not (tmp_path / du.CACHE_FILE_NAME).exists()


def test_version_cli_refuses_untrusted_manifest(tmp_path, capsys):
    raw = _raw(latest_version="99.0.0", minimum_write_version="99.0.0")
    manifest = tmp_path / "windows-stable.json"
    manifest.write_bytes(raw)
    (tmp_path / "windows-stable.json.sig").write_bytes(sign(raw, key_id="unknown-key"))
    assert main(["version", "--manifest", str(manifest), "--json"]) == 2
    assert "untrusted" in capsys.readouterr().err


def _load_signing_script():
    spec = importlib.util.spec_from_file_location("sign_release_manifest", SIGNER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _throwaway_pem(tmp_path: Path) -> tuple[Path, str]:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "throwaway-release-key.pem"
    path.write_bytes(pem)
    return path, du.public_key_b64(key)


def test_signing_helper_signs_with_key_file_and_output_verifies_against_trust_root(tmp_path, monkeypatch):
    script = _load_signing_script()
    key_path, public_b64 = _throwaway_pem(tmp_path)
    manifest = tmp_path / "update-manifest.json"
    raw = _raw()
    manifest.write_bytes(raw)

    assert script.main(["--public-key-for-trust-root", "--private-key-file", str(key_path)]) == 0
    assert script.main(
        ["--manifest", str(manifest), "--key-id", "rvv-release-test", "--private-key-file", str(key_path)]
    ) == 0

    signature = (tmp_path / "update-manifest.json.sig").read_bytes()
    assert du.verify_manifest_signature(raw, signature, {"rvv-release-test": public_b64}) == "rvv-release-test"
    assert b"PRIVATE" not in signature


def test_signing_helper_reads_key_from_environment_and_refuses_invalid_manifest(tmp_path, monkeypatch):
    script = _load_signing_script()
    key_path, _ = _throwaway_pem(tmp_path)
    monkeypatch.setenv(script.ENV_KEY, key_path.read_text(encoding="utf-8"))
    manifest = tmp_path / "bad.json"
    manifest.write_bytes(b'{"schema": 1}')

    assert script.main(["--manifest", str(manifest), "--key-id", "rvv-release-test"]) == 2
    assert not (tmp_path / "bad.json.sig").exists()


def test_signing_helper_without_any_key_fails_cleanly(tmp_path, monkeypatch):
    script = _load_signing_script()
    monkeypatch.delenv(script.ENV_KEY, raising=False)
    with pytest.raises(SystemExit):
        script.main(["--manifest", str(tmp_path / "m.json"), "--key-id", "rvv-release-test"])
