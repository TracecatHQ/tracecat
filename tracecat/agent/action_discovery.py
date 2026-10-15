"""Compact, paginated registry action discovery shared by authoring surfaces."""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass

import orjson

from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.actions.types import IndexEntry


@dataclass(frozen=True, slots=True)
class ActionEntryPage:
    """One stable page of action index entries before schema enrichment."""

    entries: list[tuple[IndexEntry, str]]
    next_cursor: str | None
    prev_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None

    @property
    def has_previous(self) -> bool:
        return self.prev_cursor is not None


def _cursor_fingerprint(*, query: str | None, namespace: str | None, limit: int) -> str:
    serialized = orjson.dumps(
        {"query": query, "namespace": namespace, "limit": limit},
        option=orjson.OPT_SORT_KEYS,
    )
    return hashlib.sha256(serialized).hexdigest()


def _encode_cursor(offset: int, fingerprint: str) -> str:
    payload = orjson.dumps({"offset": offset, "fingerprint": fingerprint})
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str, fingerprint: str) -> int:
    if len(cursor) > 8192:
        raise ValueError("Invalid cursor format")
    try:
        payload = orjson.loads(base64.urlsafe_b64decode(cursor))
    except (ValueError, TypeError, binascii.Error) as e:
        raise ValueError("Invalid cursor format") from e
    if not isinstance(payload, dict):
        raise ValueError("Invalid cursor format")
    if payload.get("fingerprint") != fingerprint:
        raise ValueError(
            "Cursor no longer matches current filters. Retry without cursor."
        )
    offset = payload.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("Invalid cursor format")
    return offset


async def list_action_entry_page(
    service: RegistryActionsService,
    *,
    query: str | None = None,
    namespace: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> ActionEntryPage:
    """Search or browse actions, then page the stable index before enrichment."""
    if limit < 1 or limit > 200:
        raise ValueError("Action discovery limit must be between 1 and 200")

    if query:
        entries = await service.search_actions_from_index(query, limit=None)
        if namespace:
            entries = [
                item for item in entries if item[0].namespace.startswith(namespace)
            ]
    else:
        entries = await service.list_actions_from_index(namespace=namespace)

    fingerprint = _cursor_fingerprint(query=query, namespace=namespace, limit=limit)
    start = _decode_cursor(cursor, fingerprint) if cursor is not None else 0
    end = start + limit
    next_cursor = _encode_cursor(end, fingerprint) if end < len(entries) else None
    prev_cursor = (
        _encode_cursor(max(0, start - limit), fingerprint) if start > 0 else None
    )
    return ActionEntryPage(
        entries=entries[start:end],
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
    )
