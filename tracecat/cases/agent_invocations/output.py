"""Render terminal agent output as case-comment content."""

from __future__ import annotations

import orjson
from pydantic import BaseModel

from tracecat.cases.schemas import CASE_COMMENT_MAX_LENGTH, CaseCommentCreate

_TRUNCATION_NOTICE = "\n\n[Agent output truncated]"


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"Unsupported agent output type: {type(value).__name__}")


def render_agent_output_as_comment(output: object) -> str:
    """Convert terminal agent output into valid case-comment content.

    Plain text is preserved through the normal comment validation boundary.
    Structured output is rendered as readable, deterministically ordered JSON.

    Args:
        output: Terminal ``AgentOutput.output`` value.

    Returns:
        Validated case-comment content.

    Raises:
        ValueError: If output is empty, unsupported, or invalid comment content.
    """
    if output is None:
        raise ValueError("Agent output cannot be empty")

    if isinstance(output, str):
        content = output
    else:
        try:
            content = orjson.dumps(
                output,
                default=_json_default,
                option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
            ).decode()
        except TypeError as exc:
            raise ValueError("Agent output must be text or JSON-serializable") from exc

    if len(content) > CASE_COMMENT_MAX_LENGTH:
        prefix_length = CASE_COMMENT_MAX_LENGTH - len(_TRUNCATION_NOTICE)
        content = f"{content[:prefix_length]}{_TRUNCATION_NOTICE}"

    return CaseCommentCreate(content=content).content
