"""Local, loopback-only ``operate`` application: typed API over the canonical use cases.

An interface adapter (see docs/application-architecture.md): it owns HTTP, security
and session transport only. Domain verdicts and every canonical write remain owned by
``CanonicalSeasonService``; AI provider (#965) and intent router (#966) plug in
through ``tools.IntentRouterPort`` and ``tools.ToolRegistry`` only.
"""
