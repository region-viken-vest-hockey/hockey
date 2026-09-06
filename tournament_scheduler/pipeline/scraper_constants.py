"""Shared source-type constants for Stage 2 scraping."""

from __future__ import annotations

SOURCE_OUTLOOK = "outlook"
SOURCE_HTML = "html"
SOURCE_ICAL = "ical"
SOURCE_GOOGLE = "google"
# A source whose availability is a known, deterministic fact (e.g. a club's
# fixed weekly ice-time allocation) rather than something to scrape — see
# `tournament_scheduler.sandefjord_allocation` (issue #261). Never requires
# network access or credentials; Stage 2 computes its events directly.
SOURCE_FIXED_ALLOCATION = "fixed_allocation"

_BROWSER_SOURCE_TYPES = {SOURCE_OUTLOOK, SOURCE_HTML}
_ICAL_SOURCE_TYPES = {SOURCE_ICAL, SOURCE_GOOGLE}
