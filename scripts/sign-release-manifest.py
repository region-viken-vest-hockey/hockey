#!/usr/bin/env python3
"""Sign a release update manifest for the software-release workflow.

The private Ed25519 key is read from a file (``--private-key-file``) or from the
``RVV_RELEASE_SIGNING_KEY`` environment variable holding the PEM text (for a
protected GitHub Actions secret). It is never written to the repository or to
the signature output. The manifest is parsed before signing so an invalid
manifest is never signed.

Examples::

    scripts/sign-release-manifest.py --manifest dist/update-manifest.json \\
        --key-id rvv-release-2026 --private-key-file "$RUNNER_TEMP/release.pem"

    RVV_RELEASE_SIGNING_KEY="$SIGNING_PEM" scripts/sign-release-manifest.py \\
        --manifest dist/update-manifest.json --key-id rvv-release-2026

    scripts/sign-release-manifest.py --public-key-for-trust-root --private-key-file key.pem
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_KEY = "RVV_RELEASE_SIGNING_KEY"


def _load_pem(args: argparse.Namespace) -> bytes:
    if args.private_key_file:
        return Path(args.private_key_file).read_bytes()
    value = os.environ.get(ENV_KEY, "")
    if not value.strip():
        raise SystemExit(f"no signing key: pass --private-key-file or set {ENV_KEY}")
    return value.encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(ROOT))
    from tournament_scheduler import distribution_update as du

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--manifest", help="update manifest JSON to sign (exact bytes are signed)")
    parser.add_argument("--key-id", help="key ID embedded in the client trust root for this key")
    parser.add_argument("--private-key-file", help="PEM Ed25519 private key file")
    parser.add_argument("--output", help="signature output path (default: <manifest>.sig)")
    parser.add_argument(
        "--public-key-for-trust-root",
        action="store_true",
        help="print the base64 public key for the given private key and exit (no signing)",
    )
    args = parser.parse_args(argv)

    try:
        private_key = du.load_signing_key(_load_pem(args))
    except (ValueError, OSError) as exc:
        print(f"Cannot load signing key: {exc}", file=sys.stderr)
        return 2

    if args.public_key_for_trust_root:
        print(du.public_key_b64(private_key))
        return 0

    if not args.manifest or not args.key_id:
        parser.error("--manifest and --key-id are required when signing")
    manifest_path = Path(args.manifest)
    manifest_bytes = manifest_path.read_bytes()
    try:
        du.parse_manifest(manifest_bytes)
        envelope = du.sign_manifest_bytes(private_key, args.key_id, manifest_bytes)
    except (du.ManifestError, ValueError) as exc:
        print(f"Refusing to sign: {exc}", file=sys.stderr)
        return 2

    output = Path(args.output) if args.output else manifest_path.with_name(
        manifest_path.name + du.SIGNATURE_SUFFIX
    )
    output.write_bytes(envelope)
    print(f"Wrote signature {output} (key id {args.key_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
