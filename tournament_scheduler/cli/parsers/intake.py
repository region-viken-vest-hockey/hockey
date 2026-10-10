"""Source intake commands: registrations, scrape troubleshooting and recovery."""

from __future__ import annotations

import argparse


def add_intake_parsers(sub: argparse._SubParsersAction) -> None:
    # registrations — reviewed SharePoint List export -> controlled input.xlsx snapshot
    registrations = sub.add_parser(
        "registrations",
        help="Validate or export reviewed SharePoint registrations into the Lag sheet of input.xlsx",
    )
    registrations_sub = registrations.add_subparsers(dest="registrations_command")

    registrations_validate = registrations_sub.add_parser(
        "validate",
        help="Validate a reviewed SharePoint CSV/XLSX export without writing a workbook",
    )
    registrations_validate.add_argument("source", help="Reviewed SharePoint List export (.csv/.xlsx)")
    registrations_validate.add_argument(
        "--input",
        required=True,
        help="Controlled pipeline input workbook to validate against (input.xlsx)",
    )

    registrations_export = registrations_sub.add_parser(
        "export",
        help="Create an updated input workbook with only Lag replaced from approved registrations",
    )
    registrations_export.add_argument("source", help="Reviewed SharePoint List export (.csv/.xlsx)")
    registrations_export.add_argument(
        "--input",
        required=True,
        help="Controlled pipeline input workbook to copy and update",
    )
    registrations_export.add_argument(
        "--output",
        required=True,
        help="Output workbook path for the updated controlled input snapshot",
    )
    registrations_export.add_argument(
        "--dry-run",
        action="store_true",
        help="Show validation/diff summary without writing the output workbook or audit artifact",
    )

    # scrape — single-club troubleshooting
    scrape = sub.add_parser("scrape", help="Scrape a single club's calendar for troubleshooting")
    scrape.add_argument(
        "--club", required=True,
        help="Club/source name (e.g. 'Sandefjord Penguins', 'Jar', 'Jutul')",
    )
    scrape.add_argument(
        "--work-dir", default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # scrape-llm — capability-gated LLM browser guidance for blocked sources
    scrape_llm = sub.add_parser(
        "scrape-llm",
        help="Show browser-tool requirements for LLM-guided single-club recovery (browser-enabled harness only)",
    )
    scrape_llm.add_argument(
        "--club", required=True,
        help="Club/source name (e.g. 'Holmen', 'Jar', 'Sandefjord')",
    )
    scrape_llm.add_argument(
        "--work-dir", default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    scrape_llm.add_argument(
        "--export-dir", default="export",
        help="Export directory for debug screenshots or recovered artifacts (default: export)",
    )
    scrape_llm.add_argument(
        "--endpoint", default="http://host.lima.internal:1234",
        help="LLM API endpoint used by the browser agent (default: http://host.lima.internal:1234)",
    )
    scrape_llm.add_argument(
        "--model", default="qwen2.5-32b-instruct",
        help="LLM model name used by the browser agent (default: qwen2.5-32b-instruct)",
    )
    scrape_llm.add_argument(
        "--max-iterations", type=int, default=20,
        help="Maximum browser interaction cycles to advertise to the browser agent (default: 20)",
    )
    scrape_llm.add_argument(
        "--cache-results", dest="cache_results", action="store_true",
        help="Advertise caching of recovered events (default: on)",
    )
    scrape_llm.add_argument(
        "--no-cache-results", dest="cache_results", action="store_false",
        help="Disable caching in the capability guidance",
    )
    scrape_llm.set_defaults(cache_results=True)
    scrape_llm.add_argument(
        "--debug-screenshots", action="store_true",
        help="Advertise saving browser debug screenshots",
    )

    recovery = sub.add_parser(
        "recovery-targets",
        help="List blocked or zero-event sources from the Stage 2 checkpoint as JSON",
    )
    recovery.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # recovery-inject — inject recovered events into the unified cache from stdin
    recovery_inject = sub.add_parser(
        "recovery-inject",
        help="Inject a JSON event list from stdin into the cache for a given source",
    )
    recovery_inject.add_argument(
        "--source",
        required=True,
        help="Source name to patch (e.g. 'Tønsberg')",
    )
    recovery_inject.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # scrape-merge — rebuild Stage 2 checkpoint from recovered cache data
    scrape_merge = sub.add_parser(
        "scrape-merge",
        help="Rebuild the Stage 2 checkpoint from recovered cache data",
    )
    scrape_merge.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
