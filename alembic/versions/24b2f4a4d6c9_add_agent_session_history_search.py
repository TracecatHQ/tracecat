"""add agent session history search

Revision ID: 24b2f4a4d6c9
Revises: 11d479597e08
Create Date: 2026-07-07 00:00:00.000000

"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "24b2f4a4d6c9"
down_revision: str | None = "11d479597e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 1000
_MAX_SEARCH_TEXT_CHARS = 8000
_WHITESPACE_RE = re.compile(r"\s+")


def _collapse_and_cap(text: str) -> str | None:
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    if not collapsed:
        return None
    return collapsed[:_MAX_SEARCH_TEXT_CHARS]


def _compact_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        try:
            return str(value)
        except Exception:
            return None


def _media_placeholder(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "[binary]"
    block_type = value.get("type") or value.get("kind")
    media_type = value.get("media_type") or value.get("mediaType")
    marker = f"{block_type or ''} {media_type or ''}".lower()
    if "image" in marker:
        return "[image]"
    if "audio" in marker:
        return "[audio]"
    if "video" in marker:
        return "[video]"
    if "document" in marker:
        return "[document]"
    return "[binary]"


def _extract_content(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_extract_content_item(item))
        return parts
    if isinstance(value, Mapping):
        return _extract_content_item(value)
    return []


def _extract_content_item(item: Any) -> list[str]:
    if isinstance(item, str):
        return [item]
    if not isinstance(item, Mapping):
        return []

    block_type = item.get("type") or item.get("kind") or item.get("part_kind")
    if block_type in {"image", "image-url", "binary"}:
        return [_media_placeholder(item)]
    if block_type in {"audio", "audio-url", "video", "video-url", "document-url"}:
        return [_media_placeholder(item)]
    if text := item.get("text"):
        return [text] if isinstance(text, str) else []
    if content := item.get("content"):
        return _extract_content(content)
    return []


def _extract_part(part: Any) -> list[str]:
    if not isinstance(part, Mapping):
        return []

    part_kind = part.get("part_kind") or part.get("type") or part.get("kind")
    match part_kind:
        case "user-prompt":
            return _extract_content(part.get("content"))
        case "text":
            text = part.get("content") or part.get("text")
            return [text] if isinstance(text, str) else []
        case "tool-call" | "tool_use":
            name = part.get("tool_name") or part.get("name")
            args = part.get("args") if "args" in part else part.get("input")
            text = " ".join(
                value
                for value in (
                    str(name) if name else None,
                    _compact_value(args),
                )
                if value
            )
            return [text] if text else []
        case "tool-return" | "tool_result":
            values: list[str] = []
            if name := part.get("tool_name"):
                values.append(str(name))
            values.extend(_extract_content(part.get("content")))
            return values
        case _:
            return _extract_content_item(part)


def _extract_message_payload(payload: Mapping[str, Any]) -> list[str]:
    if message := payload.get("message"):
        if isinstance(message, Mapping):
            return _extract_message_payload(message)

    if parts := payload.get("parts"):
        if isinstance(parts, list):
            extracted: list[str] = []
            for part in parts:
                extracted.extend(_extract_part(part))
            return extracted

    if "content" in payload:
        return _extract_content(payload.get("content"))

    return []


def _extract_search_text(content: Any) -> str | None:
    if not isinstance(content, Mapping):
        return None
    try:
        parts = _extract_message_payload(content)
    except Exception:
        return None
    return _collapse_and_cap(" ".join(parts))


def _backfill_search_text() -> None:
    connection = op.get_bind()
    last_surrogate_id = 0

    while True:
        rows = (
            connection.execute(
                sa.text("""
                    SELECT surrogate_id, content
                    FROM agent_session_history
                    WHERE surrogate_id > :last_surrogate_id
                      AND search_text IS NULL
                    ORDER BY surrogate_id
                    LIMIT :limit
                """),
                {
                    "last_surrogate_id": last_surrogate_id,
                    "limit": _BATCH_SIZE,
                },
            )
            .mappings()
            .all()
        )
        if not rows:
            break

        updates = [
            {
                "surrogate_id": row["surrogate_id"],
                "search_text": search_text,
            }
            for row in rows
            if (search_text := _extract_search_text(row["content"])) is not None
        ]
        if updates:
            connection.execute(
                sa.text("""
                    UPDATE agent_session_history
                    SET search_text = :search_text
                    WHERE surrogate_id = :surrogate_id
                """),
                updates,
            )

        last_surrogate_id = rows[-1]["surrogate_id"]


def upgrade() -> None:
    op.add_column(
        "agent_session_history",
        sa.Column("search_text", sa.Text(), nullable=True),
    )
    op.execute("""
        ALTER TABLE agent_session_history
        ADD COLUMN search_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(search_text, ''))
        ) STORED
    """)
    _backfill_search_text()
    op.create_index(
        "ix_agent_session_history_search_tsv",
        "agent_session_history",
        ["search_tsv"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_session_history_search_tsv",
        table_name="agent_session_history",
    )
    op.drop_column("agent_session_history", "search_tsv")
    op.drop_column("agent_session_history", "search_text")
