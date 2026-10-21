"""Operator action: verify the last published GitHub Pages content (issue #20).

Split out of :mod:`operator_action` to keep that module within its file-length
baseline. Helpers that read the run manifest stay in ``operator_action`` and are
imported lazily to avoid an import cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .capability_result import CapabilityResult


def _execute_verify_pages(
    *,
    work_dir: str,
    latest_url: str | None = None,
    run_url: str | None = None,
    bundle_fingerprint: str | None = None,
    run_id: str | None = None,
    published_at: str | None = None,
    max_attempts: int | None = None,
    retry_delay_seconds: float | None = None,
    fetch: "Callable[[str], Any] | None" = None,
    sleep: "Callable[[float], None] | None" = None,
) -> "CapabilityResult":
    """Verify the last published GitHub Pages content is reachable (issue #20).

    Resolves *latest_url*/*run_url*/*bundle_fingerprint*/*run_id* from the
    run manifest's last recorded ``pages_publish`` capability result when
    not given explicitly, so ``rvv-miniputt operator verify`` with no
    arguments re-checks whatever was published most recently.
    """
    from . import pages_verify
    from .operator_action import _evidence_value, _last_ok_pages_publish_capability

    if latest_url is None or bundle_fingerprint is None or run_id is None:
        last = _last_ok_pages_publish_capability(work_dir)
        if last is None:
            return CapabilityResult.failed(
                "Ingen tidligere Pages-publisering funnet å verifisere.", capability="pages_verify"
            )
        urls = [a for a in (last.get("artifacts") or []) if isinstance(a, str) and a.startswith("http")]
        latest_url = latest_url or (urls[0] if urls else None)
        run_url = run_url or (urls[1] if len(urls) > 1 else None)
        bundle_fingerprint = bundle_fingerprint or _evidence_value(last, "bundle_fingerprint")
        run_id = run_id or _evidence_value(last, "run_id")
        published_at = published_at or _evidence_value(last, "published_at")
        if not latest_url or not bundle_fingerprint or not run_id or not published_at:
            return CapabilityResult.failed(
                "Kunne ikke utlede URL, fingeravtrykk, run_id eller publiseringstidspunkt fra siste Pages-publisering.",
                capability="pages_verify",
            )

    verify_kwargs: dict[str, Any] = {}
    if max_attempts is not None:
        verify_kwargs["max_attempts"] = max_attempts
    if retry_delay_seconds is not None:
        verify_kwargs["retry_delay_seconds"] = retry_delay_seconds
    if fetch is not None:
        verify_kwargs["fetch"] = fetch
    if sleep is not None:
        verify_kwargs["sleep"] = sleep

    return pages_verify.verify_publication(
        latest_url=latest_url,
        run_url=run_url,
        bundle_fingerprint=bundle_fingerprint,
        run_id=run_id,
        published_at=published_at or "",
        **verify_kwargs,
    )
