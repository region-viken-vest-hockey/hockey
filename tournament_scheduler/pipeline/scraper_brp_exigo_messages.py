"""BRP/Exigo message enrichment through its Next.js server-action contract."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from urllib.parse import urljoin

import requests

_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="(?P<src>[^"]+)"')
_ACTION_RE = re.compile(
    r'createServerReference\)\("(?P<id>[0-9a-f]+)".{0,240}?"fetchEventMessagesAction"',
    re.DOTALL,
)


class BrpExigoMessageError(RuntimeError):
    """The source's descriptive-message contract could not be completed."""


def discover_message_action_id(
    session: requests.Session,
    page_url: str,
    page_content: str,
) -> str:
    """Discover ``fetchEventMessagesAction`` from the page's current JS chunks."""

    script_urls = [urljoin(page_url, match.group("src")) for match in _SCRIPT_SRC_RE.finditer(page_content)]
    if not script_urls:
        raise BrpExigoMessageError("BRP/Exigo-siden mangler JavaScript-kilder for meldingsoppslag.")
    errors: list[str] = []
    for script_url in script_urls:
        try:
            response = session.get(script_url, timeout=15)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 -- report source-contract degradation
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        match = _ACTION_RE.search(response.text)
        if match:
            return match.group("id")
    detail = f" ({errors[0]})" if errors else ""
    raise BrpExigoMessageError(
        "Fant ikke fetchEventMessagesAction i BRP/Exigo-sidens JavaScript" + detail
    )


def fetch_booking_messages(
    session: requests.Session,
    page_url: str,
    groups: Sequence[Mapping[str, object]],
    *,
    action_id: str,
) -> list[str]:
    """Return one normalized descriptive message per grouped source booking."""

    messages = [str(group.get("item", {}).get("message") or "").strip() for group in groups]
    request_indexes: list[int] = []
    requests_payload: list[dict[str, object]] = []
    for index, group in enumerate(groups):
        item = group.get("item")
        if not isinstance(item, Mapping) or item.get("orderId") is None:
            continue
        required = ("id", "start", "end")
        if any(not str(item.get(key) or "") for key in required):
            raise BrpExigoMessageError("BRP/Exigo-hendelse mangler id/start/end for meldingsoppslag.")
        request_indexes.append(index)
        requests_payload.append(
            {
                "orderId": item["orderId"],
                "startISO": item["start"],
                "endISO": item["end"],
                "eventId": item["id"],
            }
        )
    if not requests_payload:
        return messages

    try:
        response = session.post(
            page_url,
            headers={
                "Next-Action": action_id,
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
            },
            data=json.dumps([requests_payload], separators=(",", ":")),
            timeout=15,
        )
        response.raise_for_status()
        text = response.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrpExigoMessageError("BRP/Exigo-meldingssvar er ikke gyldig UTF-8.") from exc
    except Exception as exc:  # noqa: BLE001 -- convert transport failure to source integrity
        raise BrpExigoMessageError(
            f"BRP/Exigo-meldingsoppslag feilet: {type(exc).__name__}: {exc}"
        ) from exc

    result: object | None = None
    for line in text.splitlines():
        _, separator, payload = line.partition(":")
        if not separator:
            continue
        try:
            candidate = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(candidate, list)
            and len(candidate) == len(requests_payload)
            and all(isinstance(row, list) for row in candidate)
        ):
            result = candidate
            break
    if not isinstance(result, list):
        raise BrpExigoMessageError("BRP/Exigo-meldingssvar har ukjent format.")

    for request_index, raw_messages in zip(request_indexes, result, strict=True):
        if not all(isinstance(value, str) for value in raw_messages):
            raise BrpExigoMessageError("BRP/Exigo-meldingssvar inneholder ugyldige meldinger.")
        distinct = {value.strip() for value in raw_messages if value.strip()}
        if len(distinct) > 1:
            raise BrpExigoMessageError(
                "Én BRP/Exigo-bestilling har ulike meldinger per ressurs og kan ikke slås sammen."
            )
        if distinct:
            messages[request_index] = next(iter(distinct))
    return messages
