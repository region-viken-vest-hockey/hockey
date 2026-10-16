"""Read-only ``version`` command: installed version and update classification from a local manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import distribution, distribution_update


def _cmd_version(args: argparse.Namespace) -> int:
    metadata = distribution.version_metadata()
    result: dict[str, object] = dict(metadata)

    if args.manifest:
        try:
            manifest = distribution_update.parse_manifest(Path(args.manifest).read_bytes())
        except (OSError, distribution_update.ManifestError) as exc:
            print(f"Cannot read release manifest {args.manifest}: {exc}", file=sys.stderr)
            return 2
        decision = distribution_update.write_decision(metadata["version"], manifest)
        result.update(
            {
                "update_status": distribution_update.classify(metadata["version"], manifest).value,
                "latest_version": manifest.latest_version,
                "minimum_write_version": manifest.minimum_write_version,
                "writes_allowed": decision.allowed,
                "reason": decision.reason,
            }
        )

    if args.json:
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0

    print(f"{metadata['name']} {metadata['version']} ({metadata['distribution']})")
    if "update_status" in result:
        print(f"Update status: {result['update_status']}")
        print(f"Latest version: {result['latest_version']}")
        print(f"Canonical writes: {'allowed' if result['writes_allowed'] else 'blocked'} - {result['reason']}")
    return 0
