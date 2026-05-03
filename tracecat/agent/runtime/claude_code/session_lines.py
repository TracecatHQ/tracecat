"""Shared helpers for Claude Code JSONL session rows."""

from __future__ import annotations

from collections.abc import Mapping

APPROVAL_CONTINUATION_PROMPT = "Continue."


def session_line_uuid(line_data: Mapping[str, object]) -> str | None:
    """Return the Claude Code JSONL row UUID when present."""
    match line_data:
        case {"uuid": str(line_uuid)}:
            return line_uuid
        case _:
            return None


def is_meta_session_line(line_data: Mapping[str, object]) -> bool:
    """Return True for Claude Code metadata rows."""
    return line_data.get("isMeta") is True


def is_synthetic_session_line(line_data: Mapping[str, object]) -> bool:
    """Return True for Claude Code synthetic assistant placeholder rows."""
    match line_data:
        case {"message": {"model": "<synthetic>"}}:
            return True
        case _:
            return False


def is_approval_continuation_prompt_line(line_data: Mapping[str, object]) -> bool:
    """Return True for the hidden approval continuation prompt JSONL row."""
    match line_data:
        case {"type": "user", "message": {"content": str(text)}}:
            return text == APPROVAL_CONTINUATION_PROMPT
        case {
            "type": "user",
            "message": {"content": [{"type": "text", "text": str(text)}]},
        }:
            return text == APPROVAL_CONTINUATION_PROMPT
        case _:
            return False


def is_continuation_control_artifact(
    line_data: Mapping[str, object],
    internal_uuids: set[str],
) -> bool:
    """Return True for Claude Code continuation artifacts, even if mis-kind-ed.

    Claude Code can persist a short hidden continuation chain after approval
    resume: an `isMeta` user tick, a synthetic assistant row, the actual
    "Continue." prompt, and sometimes a thinking-only assistant row. The first
    two rows are intrinsically internal; later rows are internal only when they
    descend from an internal row.
    """
    if is_meta_session_line(line_data) or is_synthetic_session_line(line_data):
        return True

    match line_data:
        case {"parentUuid": str(parent_uuid)}:
            parent_is_internal = parent_uuid in internal_uuids
        case _:
            parent_is_internal = False

    if not parent_is_internal:
        return False

    if is_approval_continuation_prompt_line(line_data):
        return True

    match line_data:
        case {"type": "assistant", "message": {"content": list(parts)}}:
            return bool(parts) and all(
                isinstance(part, dict) and part.get("type") == "thinking"
                for part in parts
            )
        case _:
            return False
