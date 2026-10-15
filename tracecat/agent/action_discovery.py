"""Compact, paginated registry action discovery shared by authoring surfaces."""

from __future__ import annotations

import base64
import binascii
import hashlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Literal

import orjson

from tracecat.registry.actions.service import (
    RegistryActionsService,
    action_browse_sort_key,
    action_search_sort_key,
)
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


def _encode_cursor(
    direction: Literal["after", "before"], action_name: str, fingerprint: str
) -> str:
    payload = orjson.dumps(
        {"direction": direction, "action_name": action_name, "fingerprint": fingerprint}
    )
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(
    cursor: str, fingerprint: str
) -> tuple[Literal["after", "before"], str]:
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
    direction = payload.get("direction")
    action_name = payload.get("action_name")
    if direction not in ("after", "before") or not isinstance(action_name, str):
        raise ValueError("Invalid cursor format")
    if "." not in action_name:
        raise ValueError("Invalid cursor format")
    return direction, action_name


def _action_name(item: tuple[IndexEntry, str]) -> str:
    entry = item[0]
    return f"{entry.namespace}.{entry.name}"


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
    else:
        entries = await service.list_actions_from_index(namespace=namespace)
    entries = [
        item
        for item in entries
        if item[0].options.get("include_in_schema") is True
        and (namespace is None or item[0].namespace.startswith(namespace))
    ]

    fingerprint = _cursor_fingerprint(query=query, namespace=namespace, limit=limit)
    keys = [
        action_search_sort_key(_action_name(item), query)
        if query
        else action_browse_sort_key(_action_name(item))
        for item in entries
    ]
    if cursor is None:
        start, end = 0, min(limit, len(entries))
    else:
        direction, anchor_name = _decode_cursor(cursor, fingerprint)
        anchor_key = (
            action_search_sort_key(anchor_name, query)
            if query
            else action_browse_sort_key(anchor_name)
        )
        if direction == "after":
            start = bisect_right(keys, anchor_key)
            end = min(start + limit, len(entries))
        else:
            end = bisect_left(keys, anchor_key)
            start = max(0, end - limit)

    selected = entries[start:end]
    next_cursor = (
        _encode_cursor("after", _action_name(selected[-1]), fingerprint)
        if selected and end < len(entries)
        else None
    )
    prev_cursor = (
        _encode_cursor("before", _action_name(selected[0]), fingerprint)
        if selected and start > 0
        else None
    )
    return ActionEntryPage(
        entries=selected,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
    )
