from __future__ import annotations

from typing import Any

import orjson
from pydantic import Json
from pydantic_core import from_json


def try_parse_json(x: Any) -> Json[Any] | str:
    if not isinstance(x, str | bytes | bytearray):
        return x

    try:
        return orjson.loads(x)
    except orjson.JSONDecodeError:
        try:
            return from_json(x, allow_partial=True)
        except ValueError:
            return x


def resolve_agent_output(output: Any) -> Any:
    """Return a finished agent turn's output as workflows receive it.

    Workflow agent actions apply this to every backend's final output, so a
    workflow reads the same value whichever backend ran the turn. Text that parses as JSON becomes
    the parsed value, the built-in backend's long-standing behavior that
    expressions like ``ACTIONS.agent.result.output.field`` depend on. Other
    text stays a string, and structured output is returned unchanged.
    """
    return try_parse_json(output) if isinstance(output, str) else output
