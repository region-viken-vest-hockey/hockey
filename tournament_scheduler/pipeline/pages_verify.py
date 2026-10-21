"""Verify GitHub Pages publication reachability (issue #20).

``pages_publish.publish()`` reports a successful ``git push`` — that is not
the same thing as the content actually being reachable at its public URL:
GitHub Pages builds and propagates asynchronously, a CDN edge can serve a
stale cached page, and a broken relative link would only be discovered by a
human clicking around after the fact. This module closes that gap with a
bounded-retry HTTP check that:

- polls the published ``/latest/_meta.json`` (written by ``publish()``)
  until it responds, and confirms its ``bundle_fingerprint``/``run_id``
  match what was just published — so a stale, cached response with an
  *older* bundle's content is never mistaken for a successful verification;
- then fetches the ``/latest/`` page itself and every relative link/asset
  it references, confirming each one resolves under the same base path
  (this is what actually proves the ``/hockey/`` project-subpath links
  work, not just that the root page loaded).

HTTP calls are made through an injectable ``fetch`` callable (default
:func:`_default_fetch`, a thin ``requests.get`` wrapper) so tests never hit
the network; ``sleep`` is similarly injectable so retry-window tests don't
actually wait.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin

import requests

from .capability_result import CapabilityResult
from .pages_history import HISTORY_DIRECTORY

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_RETRY_DELAY_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 10

_LINK_PATTERN = re.compile(r'(?:href|src)="([^"]+)"')
_SKIP_LINK_PREFIXES = ("http://", "https://", "//", "#", "mailto:", "tel:")


@dataclass
class FetchResponse:
    status_code: int
    text: str
    error: str | None = None


FetchFunc = Callable[[str], FetchResponse]
SleepFunc = Callable[[float], None]


def _default_fetch(url: str) -> FetchResponse:
    try:
        response = requests.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
        return FetchResponse(status_code=response.status_code, text=response.text)
    except requests.RequestException as exc:
        return FetchResponse(status_code=0, text="", error=str(exc))


def _check_linked_pages(base_url: str, fetch: FetchFunc) -> str | None:
    """Fetch *base_url* and every relative link/asset it references.

    Returns ``None`` if everything resolved, or a description of the first
    failure otherwise.
    """
    index_response = fetch(base_url)
    if index_response.error or index_response.status_code != 200:
        return f"hovedsiden {base_url} svarte ikke OK: {index_response.error or index_response.status_code}"

    for href in sorted(set(_LINK_PATTERN.findall(index_response.text))):
        if href.startswith(_SKIP_LINK_PREFIXES):
            continue
        target = urljoin(base_url, href)
        response = fetch(target)
        if response.error or response.status_code != 200:
            return f"lenke '{href}' ({target}) svarte ikke OK: {response.error or response.status_code}"
    return None


def _history_url(latest_url: str) -> str:
    """``https://<site>/<repo>/latest/`` -> ``https://<site>/<repo>/history/``."""
    base = latest_url.rstrip("/").removesuffix("/latest")
    return f"{base}/{HISTORY_DIRECTORY}/"


def _check_history_page(history_url: str, run_id: str, fetch: FetchFunc) -> str | None:
    """Check ``/history/`` loads, its run links resolve, and it marks *run_id* current.

    Returns ``None`` when the history page is consistent with the publication,
    or a description of the first failure otherwise.
    """
    response = fetch(history_url)
    if response.error or response.status_code != 200:
        return f"historikksiden {history_url} svarte ikke OK: {response.error or response.status_code}"
    current_marker = f'data-run-id="{run_id}" data-current="true"'
    if current_marker not in response.text:
        return f"historikksiden {history_url} markerer ikke kjøring {run_id} som gjeldende"
    return _check_linked_pages(history_url, fetch)


def verify_publication(
    *,
    latest_url: str,
    run_url: str | None = None,
    bundle_fingerprint: str,
    run_id: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    fetch: FetchFunc | None = None,
    sleep: SleepFunc | None = None,
) -> CapabilityResult:
    """Poll *latest_url* until the just-published content is verifiably reachable.

    Distinguishes push success (already known — this is only called after
    a successful push) from Pages availability: retries up to
    *max_attempts* times, waiting *retry_delay_seconds* between attempts,
    for ``<latest_url>/_meta.json`` to report the expected
    ``bundle_fingerprint``/``run_id`` — an older, cached page (a different
    fingerprint) never counts as success, it's just another retry — then
    confirms the actual page and its links/assets load.

    Returns ``ok`` once verified, or ``warning`` (not ``blocked`` —
    propagation delay isn't a decision a human needs to make, just a
    "check again shortly") if the window elapses first, with the last
    failure detail in ``problems``.
    """
    fetch = fetch or _default_fetch
    sleep = sleep or time.sleep
    meta_url = latest_url.rstrip("/") + "/_meta.json"

    last_detail = "ingen forsøk utført"
    for attempt in range(1, max_attempts + 1):
        response = fetch(meta_url)
        if response.error:
            last_detail = f"tilkoblingsfeil mot {meta_url}: {response.error}"
        elif response.status_code != 200:
            last_detail = f"{meta_url} svarte HTTP {response.status_code}"
        else:
            try:
                meta = json.loads(response.text)
            except json.JSONDecodeError:
                meta = None
                last_detail = f"{meta_url} inneholdt ikke gyldig JSON"
            if meta is not None:
                found_fingerprint = meta.get("bundle_fingerprint")
                found_run_id = meta.get("run_id")
                if found_fingerprint != bundle_fingerprint:
                    last_detail = (
                        f"funnet fingeravtrykk {found_fingerprint!r} != forventet {bundle_fingerprint!r} "
                        "— sannsynligvis en foreldet/bufret side"
                    )
                elif found_run_id != run_id:
                    last_detail = f"funnet run_id {found_run_id!r} != forventet {run_id!r}"
                else:
                    page_failure = _check_linked_pages(latest_url, fetch) or _check_history_page(
                        _history_url(latest_url), run_id, fetch
                    )
                    if page_failure is None:
                        artifacts = [latest_url] + ([run_url] if run_url else [])
                        return CapabilityResult.ok(
                            f"Publisering av kjøring {run_id} bekreftet nådd på {latest_url} "
                            f"(forsøk {attempt}/{max_attempts}).",
                            capability="pages_verify",
                            evidence=[
                                f"attempts={attempt}",
                                f"bundle_fingerprint={bundle_fingerprint}",
                                f"checked_url={meta_url}",
                            ],
                            artifacts=artifacts,
                        )
                    last_detail = page_failure

        if attempt < max_attempts:
            sleep(retry_delay_seconds)

    artifacts = [latest_url] + ([run_url] if run_url else [])
    return CapabilityResult.warning(
        f"Publisering av kjøring {run_id} kunne ikke bekreftes nådd på {latest_url} etter "
        f"{max_attempts} forsøk — sannsynligvis fortsatt under utrulling hos GitHub Pages.",
        capability="pages_verify",
        problems=[last_detail],
        evidence=[f"attempts={max_attempts}", f"bundle_fingerprint={bundle_fingerprint}", f"checked_url={meta_url}"],
        artifacts=artifacts,
        suggested_actions=["Kjør 'rvv-miniputt operator verify' på nytt om noen minutter"],
    )
