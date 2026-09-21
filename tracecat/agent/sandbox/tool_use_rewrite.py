"""Normalize ``tool_use`` inputs in Anthropic Messages responses.

Claude Code validates a tool call's input (JSON schema, then the tool's own
``validateInput``) before any ``PreToolUse`` hook runs, so malformed inputs
must be fixed in the model response itself. The LLM socket proxy applies these
rewriters to ``/v1/messages`` responses on their way back into the sandbox.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import orjson

from tracecat.agent.common.tool_inputs import READ_TOOL_NAME, sanitize_read_tool_input

MESSAGES_PATH = "/v1/messages"

ToolInputSanitizer = Callable[[Mapping[str, Any]], dict[str, Any]]

TOOL_INPUT_SANITIZERS: dict[str, ToolInputSanitizer] = {
    READ_TOOL_NAME: sanitize_read_tool_input,
}


def _sanitize_tool_use_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Return a rewritten ``tool_use`` block, or None when it can pass through."""
    sanitizer = TOOL_INPUT_SANITIZERS.get(str(block.get("name")))
    if sanitizer is None:
        return None
    tool_input = block.get("input")
    if not isinstance(tool_input, dict):
        return None
    sanitized = sanitizer(tool_input)
    if sanitized == tool_input:
        return None
    return {**block, "input": sanitized}


def sanitize_messages_response(data: dict[str, Any]) -> dict[str, Any] | None:
    """Rewrite tool inputs in a non-streaming Messages response.

    Returns:
        The rewritten response, or None when nothing needed to change.
    """
    content = data.get("content")
    if not isinstance(content, list):
        return None
    changed = False
    rewritten: list[Any] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            updated = _sanitize_tool_use_block(block)
            if updated is not None:
                changed = True
                block = updated
        rewritten.append(block)
    if not changed:
        return None
    return {**data, "content": rewritten}


def sanitize_messages_response_body(body: bytes) -> bytes:
    """Rewrite a raw non-streaming Messages response body when needed."""
    try:
        data = orjson.loads(body)
    except orjson.JSONDecodeError:
        return body
    if not isinstance(data, dict):
        return body
    rewritten = sanitize_messages_response(data)
    return body if rewritten is None else orjson.dumps(rewritten)


class _PendingToolUse:
    """A streamed ``tool_use`` block whose events are held until it completes."""

    __slots__ = ("events", "partial_json", "sanitizer", "start")

    def __init__(
        self, start: dict[str, Any], events: list[bytes], sanitizer: ToolInputSanitizer
    ) -> None:
        self.start = start
        self.events = events
        self.partial_json: list[str] = []
        self.sanitizer = sanitizer


class ToolUseStreamRewriter:
    """Rewrite ``tool_use`` inputs inside a Messages SSE stream.

    Events for a ``tool_use`` block with a registered sanitizer are buffered
    from ``content_block_start`` to ``content_block_stop``. If the accumulated
    ``input_json_delta`` fragments parse and the sanitizer changes them, the
    block is re-emitted with a single delta carrying the sanitized JSON;
    otherwise the original events are forwarded verbatim. Everything else
    streams through untouched, so text deltas keep their latency.
    """

    def __init__(self) -> None:
        self._buffer = b""
        self._pending: dict[int, _PendingToolUse] = {}

    def feed(self, chunk: bytes) -> bytes:
        """Consume upstream bytes and return the bytes ready to forward."""
        self._buffer += chunk
        out: list[bytes] = []
        while True:
            end = self._buffer.find(b"\n\n")
            if end == -1:
                break
            event = self._buffer[: end + 2]
            self._buffer = self._buffer[end + 2 :]
            out.append(self._handle_event(event))
        return b"".join(out)

    def flush(self) -> bytes:
        """Return anything still buffered once the upstream stream ends."""
        out = [event for pending in self._pending.values() for event in pending.events]
        self._pending.clear()
        out.append(self._buffer)
        self._buffer = b""
        return b"".join(out)

    def _handle_event(self, event: bytes) -> bytes:
        payload = _sse_data(event)
        if payload is None:
            # Comments and pings carry no block index; forward them as-is.
            return event
        match payload:
            case {
                "type": "content_block_start",
                "index": int(index),
                "content_block": dict() as block,
            }:
                sanitizer = TOOL_INPUT_SANITIZERS.get(str(block.get("name")))
                if block.get("type") == "tool_use" and sanitizer is not None:
                    self._pending[index] = _PendingToolUse(payload, [event], sanitizer)
                    return b""
                return event
            case {
                "type": "content_block_delta",
                "index": int(index),
                "delta": dict() as delta,
            }:
                pending = self._pending.get(index)
                if pending is None:
                    return event
                pending.events.append(event)
                if delta.get("type") == "input_json_delta":
                    pending.partial_json.append(str(delta.get("partial_json", "")))
                return b""
            case {"type": "content_block_stop", "index": int(index)}:
                pending = self._pending.pop(index, None)
                if pending is None:
                    return event
                return self._finish(pending, event)
            case _:
                return event

    def _finish(self, pending: _PendingToolUse, stop_event: bytes) -> bytes:
        original = b"".join(pending.events) + stop_event
        raw_json = "".join(pending.partial_json)
        start_block = dict(pending.start["content_block"])
        if raw_json:
            try:
                tool_input = orjson.loads(raw_json)
            except orjson.JSONDecodeError:
                return original
        else:
            # Some gateways send the whole input on the start frame instead.
            tool_input = start_block.get("input")
        if not isinstance(tool_input, dict):
            return original
        sanitized = pending.sanitizer(tool_input)
        if sanitized == tool_input:
            return original
        if not raw_json:
            start_block["input"] = sanitized
            return (
                _sse_event(
                    "content_block_start",
                    {**pending.start, "content_block": start_block},
                )
                + stop_event
            )
        start_block["input"] = {}
        events = [
            _sse_event(
                "content_block_start", {**pending.start, "content_block": start_block}
            ),
            _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": pending.start["index"],
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": orjson.dumps(sanitized).decode(),
                    },
                },
            ),
            stop_event,
        ]
        return b"".join(events)


def _sse_data(event: bytes) -> dict[str, Any] | None:
    """Parse the JSON ``data:`` payload of one SSE frame, if it has one."""
    data_lines = [
        line[5:].strip() for line in event.split(b"\n") if line.startswith(b"data:")
    ]
    if not data_lines:
        return None
    try:
        payload = orjson.loads(b"\n".join(data_lines))
    except orjson.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _sse_event(event_type: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event_type}\ndata: ".encode() + orjson.dumps(payload) + b"\n\n"
