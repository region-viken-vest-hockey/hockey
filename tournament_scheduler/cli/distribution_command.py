"""Read-only ``version`` and ``capabilities`` commands.

``version`` reports the installed version and, with a local manifest, its verified update classification.
``capabilities`` reports the machine-readable compatibility contract (see ``tournament_scheduler.capabilities``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import distribution, distribution_update
from ..capabilities import build_capabilities


def _cmd_version(args: argparse.Namespace) -> int:
    metadata = distribution.version_metadata()
    result: dict[str, object] = dict(metadata)

    if args.manifest:
        manifest_path = Path(args.manifest)
        signature_path = Path(args.signature) if args.signature else manifest_path.with_name(
            manifest_path.name + distribution_update.SIGNATURE_SUFFIX
        )
        try:
            manifest = distribution_update.parse_verified_manifest(
                manifest_path.read_bytes(), signature_path.read_bytes()
            )
        except distribution_update.UntrustedManifestError as exc:
            print(f"Release manifest {args.manifest} is untrusted: {exc}", file=sys.stderr)
            return 2
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


def _top_level_commands() -> list[str]:
    """Command names as the canonical parser registers them."""
    import argparse

    from .args import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    raise RuntimeError("the CLI parser has no command group")


def _cmd_capabilities(args: argparse.Namespace) -> int:
    report = build_capabilities(_top_level_commands())
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
        return 0

    protocol = report["agent_protocol_supported"]
    schema = report["canonical_schema_version"]
    runtime = report["claude_runtime_supported"]
    print(f"{report['name']} {report['application_version']} ({report['distribution']})")
    print(f"Agent protocol: {report['agent_protocol_version']} (supported {protocol['min']} to {protocol['max']})")
    print(
        "Canonical schema: schedule "
        f"{schema['schedule']}, decisions {schema['decisions']}, layout {schema['physical_layout']}"
    )
    print(f"Claude runtime: >= {runtime['min_version']} and < {runtime['max_version_exclusive']}")
    print(f"Commands: {', '.join(report['commands'])}")
    return 0
