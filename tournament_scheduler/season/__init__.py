"""Canonical season domain: state, lifecycle, maintenance, approvals and requests.

The promoted season's canonical state identity (``state``) and its lifecycle
records (banned dates, holiday and ice-time exceptions, exception policy,
history summaries, replanning), the maintenance surface over a
promoted season (``maintenance``) with its accepted non-hard baseline
(``accepted_baseline``), and the durable approval, request, protection and
waiver guards (``change_protections``, ``request_constraints``, ``waivers``).
Layer: domain/application helpers; CLI and infrastructure must not be imported
from here.
"""
