"""Display-only projection of Pi history; native records remain untouched."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import AgentSessionHistory


def pi_display_message(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Adapt opaque Pi content to the existing chat renderer's message shape.

    The renderer uses SDK-shaped dictionaries for display. These projections
    must never be written as native history or sent back to either harness.
    """
    if entry.get("type") != "message":
        return None
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    content = message.get("content", [])
    if role == "toolResult":
        return {
            "type": "user",
            "message": {
                "type": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": message["toolCallId"],
                        "content": content,
                        "is_error": message.get("isError", False),
                    }
                ],
            },
        }
    if role not in ("user", "assistant"):
        return None
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    else:
        blocks = []
        for block in content:
            match block:
                case {"type": "text", "text": str(text)}:
                    blocks.append({"type": "text", "text": text})
                case {"type": "thinking", "thinking": str(thinking)}:
                    blocks.append(
                        {"type": "thinking", "thinking": thinking, "signature": ""}
                    )
                case {
                    "type": "toolCall",
                    "id": str(call_id),
                    "name": str(name),
                    "arguments": arguments,
                }:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call_id,
                            "name": name,
                            "input": arguments,
                        }
                    )
    result = {"type": role, "content": blocks}
    if role == "assistant":
        result["model"] = message.get("model", "pi")
    return {"type": role, "message": result}


async def load_pi_display_history(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    current_run_id: uuid.UUID | None,
    approval_tool_call_ids: set[str],
    include_active: bool,
) -> list[AgentSessionHistory]:
    """Render the active native branch, excluding superseded approval results."""
    rows = list(
        (
            await db.scalars(
                select(AgentSessionHistory)
                .where(AgentSessionHistory.session_id == session_id)
                .order_by(AgentSessionHistory.surrogate_id)
            )
        ).all()
    )
    branch = active_pi_branch(rows)
    boundary = max(
        (
            row.surrogate_id
            for row in branch
            if row.curr_run_id == current_run_id
            and (message := pi_display_message(row.content)) is not None
            and message["type"] == "assistant"
            and any(
                block.get("id") in approval_tool_call_ids
                for block in message["message"]["content"]
            )
        ),
        default=-1,
    )
    visible = (
        branch
        if include_active or current_run_id is None
        else [
            row
            for row in branch
            if row.curr_run_id != current_run_id
            or row.surrogate_id <= boundary
            or row.content.get("message", {}).get("role") == "user"
        ]
    )
    return merge_pi_display_rows(rows, visible)


def merge_pi_display_rows(
    rows: list[AgentSessionHistory], branch: list[AgentSessionHistory]
) -> list[AgentSessionHistory]:
    """Retain pending user bubbles and cancellation markers across reloads."""
    native_user_turns = {
        row.curr_run_id
        for row in branch
        if row.content.get("message", {}).get("role") == "user"
    }
    supplemental = [
        row
        for row in rows
        if row.kind == "cancelled"
        or (row.kind == "pi-input" and row.curr_run_id not in native_user_turns)
    ]
    return sorted([*branch, *supplemental], key=lambda row: row.surrogate_id)


def active_pi_branch(rows: list[AgentSessionHistory]) -> list[AgentSessionHistory]:
    """Follow parent IDs from the latest native leaf without modifying audit rows."""
    states = [row for row in rows if row.kind == "pi-state"]
    if not states:
        return []
    leaf = states[-1].content.get("leaf_id")
    native = {
        row.content["id"]: row
        for row in rows
        if row.kind == "pi-native" and isinstance(row.content.get("id"), str)
    }
    branch: list[AgentSessionHistory] = []
    seen: set[str] = set()
    while isinstance(leaf, str) and leaf in native and leaf not in seen:
        seen.add(leaf)
        row = native[leaf]
        branch.append(row)
        leaf = row.content.get("parentId")
    branch.reverse()
    return branch
