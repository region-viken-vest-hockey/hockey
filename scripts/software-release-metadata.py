#!/usr/bin/env python3
"""Archive, describe and verify the Windows software release for the software-release workflow.

Subcommands::

    archive   zip the PyInstaller onedir application as ``rvv-miniputt/...``
    describe  write update-manifest.json, release-metadata.json, checksums.txt
              and release-notes.md from the *built* application's own JSON
              (``version --json`` and ``capabilities --json`` run on the
              extracted artifact), never from a second source
    verify    check a detached signature and the artifact identity against the
              manifest before publication

Nothing here decides scheduling, verification or publication behaviour. The
manifest format and signature rules belong to ``tournament_scheduler.distribution_update``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "rvv-miniputt"
CHANNEL = "windows-stable"
ARCHIVE_PLATFORM = "windows-x64"
# Software tags are namespaced so they cannot collide with season publication
# snapshots (#549), which use no tags, or with the maintenance tags in this repository.
SOFTWARE_TAG_PREFIX = "rvv-miniputt-app-v"
MANIFEST_NAME = "update-manifest.json"
METADATA_NAME = "release-metadata.json"
CHECKSUMS_NAME = "checksums.txt"
NOTES_NAME = "release-notes.md"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class ReleaseError(Exception):
    """A release input is missing, inconsistent or unsafe to publish."""


def _du():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tournament_scheduler import distribution_update

    return distribution_update


def _du_error_types() -> tuple[type[Exception], ...]:
    from tournament_scheduler.distribution_update.errors import UpdateError

    return (UpdateError,)


def _signature_module():
    _du()
    from tournament_scheduler.distribution_update import signature

    return signature


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read JSON from {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReleaseError(f"{path} must contain a JSON object")
    return data


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_packaged_version(meta: dict[str, Any]) -> str:
    """Accept only the output of the frozen build, so a source-tree run cannot be published."""
    if meta.get("name") != PACKAGE_NAME:
        raise ReleaseError(f"application name is {meta.get('name')!r}, expected {PACKAGE_NAME!r}")
    if meta.get("distribution") != "packaged":
        raise ReleaseError(f"application distribution is {meta.get('distribution')!r}; release needs 'packaged'")
    version = meta.get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseError("application version is missing")
    return version


def _artifact_name(version: str) -> str:
    return f"{PACKAGE_NAME}-{version}-{ARCHIVE_PLATFORM}.zip"


def archive(app_dir: Path, version_json: Path, out_dir: Path) -> Path:
    """Zip the onedir application folder. Entries sort by path and use a fixed timestamp."""
    version = _require_packaged_version(_load_json(version_json))
    if not (app_dir / f"{PACKAGE_NAME}.exe").is_file():
        raise ReleaseError(f"{app_dir} does not contain {PACKAGE_NAME}.exe")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / _artifact_name(version)
    files = sorted(path for path in app_dir.rglob("*") if path.is_file())
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive_file:
        for path in files:
            arcname = PurePosixPath(PACKAGE_NAME, *path.relative_to(app_dir).parts).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            with archive_file.open(info, "w") as dst, path.open("rb") as src:
                shutil.copyfileobj(src, dst)
    print(f"Archived {len(files)} files into {target}")
    return target


def _floors(write: str, supported: str, latest: str) -> tuple[str, str]:
    du = _du()
    supported_v = du.parse_version(supported)
    write_v = du.parse_version(write)
    latest_v = du.parse_version(latest)
    if not supported_v <= write_v <= latest_v:
        raise ReleaseError(
            "compatibility floors must satisfy minimum_supported <= minimum_write <= latest "
            f"(got {supported}, {write}, {latest})"
        )
    return write, supported


def _verify_identity(identity: dict[str, Any]) -> str:
    commit = identity.get("commit")
    if not isinstance(commit, str) or not _SHA_RE.match(commit):
        raise ReleaseError("source identity does not carry a full commit SHA")
    if identity.get("clean") is not True:
        raise ReleaseError("source identity is not marked clean; refusing to release")
    return commit


def describe(args: argparse.Namespace) -> None:
    du = _du()
    version_meta = _load_json(Path(args.version_json))
    capabilities = _load_json(Path(args.capabilities_json))
    version = _require_packaged_version(version_meta)
    if capabilities.get("application_version") != version or capabilities.get("name") != PACKAGE_NAME:
        raise ReleaseError("capabilities report does not match the version report of the same build")
    if capabilities.get("distribution") != "packaged":
        raise ReleaseError("capabilities report is not from the packaged application")

    protocol = capabilities.get("agent_protocol_version")
    supported = capabilities.get("agent_protocol_supported") or {}
    if not isinstance(protocol, int) or not supported.get("min", 0) <= protocol <= supported.get("max", -1):
        raise ReleaseError(f"agent protocol {protocol!r} is outside the supported range {supported}")
    schema = capabilities.get("canonical_schema_version")
    if not isinstance(schema, dict) or not all(isinstance(v, int) for v in schema.values()):
        raise ReleaseError("canonical schema versions are missing from the capabilities report")

    identity = _load_json(Path(args.source_identity))
    commit = _verify_identity(identity)
    artifact = Path(args.artifact)
    if artifact.name != _artifact_name(version) or not artifact.is_file():
        raise ReleaseError(f"artifact must be {_artifact_name(version)} and exist")
    write, minimum = _floors(args.minimum_write_version, args.minimum_supported_version, version)

    tag = f"{SOFTWARE_TAG_PREFIX}{version}"
    artifact_block = {
        "filename": artifact.name,
        "url": f"https://github.com/{args.repository}/releases/download/{tag}/{artifact.name}",
        "sha256": _sha256(artifact),
        "size": artifact.stat().st_size,
    }
    manifest = {
        "schema": du.MANIFEST_SCHEMA_VERSION,
        "package": PACKAGE_NAME,
        "channel": CHANNEL,
        "latest_version": version,
        "minimum_write_version": write,
        "minimum_supported_version": minimum,
        "artifact": artifact_block,
        "release": {
            "tag": tag,
            "source_commit": commit,
            "agent_protocol_version": protocol,
            "canonical_schema_version": schema,
        },
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    du.parse_manifest(manifest_bytes)  # the client parser is the acceptance test for the manifest

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / MANIFEST_NAME).write_bytes(manifest_bytes)
    metadata = {
        "name": PACKAGE_NAME,
        "application_version": version,
        "distribution": "packaged",
        "tag": tag,
        "source_commit": commit,
        "source_ref_requested": identity.get("requested_ref"),
        "source_ref_kind": identity.get("resolved_kind"),
        "agent_protocol_version": protocol,
        "agent_protocol_supported": supported,
        "canonical_schema_version": schema,
        "claude_runtime_supported": capabilities.get("claude_runtime_supported"),
        "commands": capabilities.get("commands", []),
        "artifact": artifact_block,
        "smoke": {"version_json_matches_capabilities": True, "exit_status": "passed"},
    }
    (out / METADATA_NAME).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum_lines = [
        f"{artifact_block['sha256']}  {artifact.name}",
        f"{_sha256(out / MANIFEST_NAME)}  {MANIFEST_NAME}",
        f"{_sha256(out / METADATA_NAME)}  {METADATA_NAME}",
    ]
    checksum_lines.sort(key=lambda line: line.split("  ")[1])
    (out / CHECKSUMS_NAME).write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    (out / NOTES_NAME).write_text(
        f"# RVV Miniputt {version} (Windows x64)\n\n"
        f"- Source commit: `{commit}`\n"
        f"- Artifact: `{artifact.name}` ({artifact_block['size']} bytes, sha256 `{artifact_block['sha256']}`)\n"
        f"- Minimum version that may write canonical state: {write}\n"
        f"- Minimum supported version: {minimum}\n"
        f"- Agent protocol: {protocol}\n\n"
        f"Integrity: `{CHECKSUMS_NAME}`. Update trust: `{MANIFEST_NAME}` with detached signature `{MANIFEST_NAME}.sig`.\n",
        encoding="utf-8",
    )
    print(f"Described release {tag} from commit {commit}")


def verify(args: argparse.Namespace) -> None:
    du = _du()
    sig = _signature_module()
    manifest_path = Path(args.manifest)
    manifest_bytes = manifest_path.read_bytes()
    signature_bytes = Path(args.signature).read_bytes()
    key_id, _ = sig.parse_signature_envelope(signature_bytes)
    if args.key_id and key_id != args.key_id:
        raise ReleaseError(f"signature key id {key_id!r} does not match expected {args.key_id!r}")
    manifest = du.parse_verified_manifest(manifest_bytes, signature_bytes, {key_id: args.public_key_b64})
    artifact_path = manifest_path.parent / manifest.artifact.filename
    if not artifact_path.is_file() or artifact_path.stat().st_size != manifest.artifact.size:
        raise ReleaseError("artifact is missing or its size differs from the signed manifest")
    if not _HEX64_RE.match(manifest.artifact.sha256) or _sha256(artifact_path) != manifest.artifact.sha256:
        raise ReleaseError("artifact sha256 differs from the signed manifest")
    print(f"Signature verified with key id {key_id}; artifact identity matches the signed manifest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_archive = sub.add_parser("archive", help="zip the packaged application folder")
    p_archive.add_argument("--app-dir", required=True)
    p_archive.add_argument("--version-json", required=True)
    p_archive.add_argument("--out-dir", required=True)
    p_archive.set_defaults(func=lambda a: archive(Path(a.app_dir), Path(a.version_json), Path(a.out_dir)))

    p_describe = sub.add_parser("describe", help="write manifest, metadata, checksums and notes")
    p_describe.add_argument("--version-json", required=True)
    p_describe.add_argument("--capabilities-json", required=True)
    p_describe.add_argument("--source-identity", required=True)
    p_describe.add_argument("--artifact", required=True)
    p_describe.add_argument("--repository", required=True, help="owner/name used for download URLs")
    p_describe.add_argument("--minimum-write-version", required=True)
    p_describe.add_argument("--minimum-supported-version", required=True)
    p_describe.add_argument("--out-dir", required=True)
    p_describe.set_defaults(func=describe)

    p_verify = sub.add_parser("verify", help="verify a detached manifest signature and the artifact")
    p_verify.add_argument("--manifest", required=True)
    p_verify.add_argument("--signature", required=True)
    p_verify.add_argument("--public-key-b64", required=True)
    p_verify.add_argument("--key-id", default="")
    p_verify.set_defaults(func=verify)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (ReleaseError, _du_error_types()) as exc:
        print(f"Refusing release: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
