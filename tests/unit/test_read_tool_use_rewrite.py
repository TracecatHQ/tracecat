"""Tests for Read tool input normalization at the LLM proxy boundary."""

from __future__ import annotations

import orjson
import pytest

from tracecat.agent.common.tool_inputs import sanitize_read_tool_input
from tracecat.agent.sandbox.tool_use_rewrite import (
    ToolUseStreamRewriter,
    sanitize_messages_response_body,
)


@pytest.mark.parametrize(
    ("tool_input", "expected"),
    [
        (
            {"file_path": "/skills/demo/references/api.md", "pages": ""},
            {"file_path": "/skills/demo/references/api.md"},
        ),
        (
            {"file_path": "/skills/demo/SKILL.md", "offset": None, "limit": None},
            {"file_path": "/skills/demo/SKILL.md"},
        ),
        (
            {"file_path": "/skills/demo/references/api.md", "pages": "1-5"},
            {"file_path": "/skills/demo/references/api.md"},
        ),
        (
            {"file_path": "/skills/demo/references/guide.PDF", "pages": "1-5"},
            {"file_path": "/skills/demo/references/guide.PDF", "pages": "1-5"},
        ),
        (
            {"file_path": "/skills/demo/references/guide.pdf", "pages": "  "},
            {"file_path": "/skills/demo/references/guide.pdf"},
        ),
        (
            {"file_path": "/skills/demo/SKILL.md", "offset": 10, "limit": 50},
            {"file_path": "/skills/demo/SKILL.md", "offset": 10, "limit": 50},
        ),
    ],
)
def test_sanitize_read_tool_input(
    tool_input: dict[str, object], expected: dict[str, object]
) -> None:
    assert sanitize_read_tool_input(tool_input) == expected


def _sse(event_type: str, payload: dict[str, object]) -> bytes:
    return f"event: {event_type}\ndata: {orjson.dumps(payload).decode()}\n\n".encode()


def _read_tool_use_stream(partial_json: list[str]) -> bytes:
    frames = [
        _sse("message_start", {"type": "message_start", "message": {"id": "m"}}),
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Reading"},
            },
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Read",
                    "input": {},
                },
            },
        ),
        *(
            _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": piece},
                },
            )
            for piece in partial_json
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 1}),
        _sse("message_stop", {"type": "message_stop"}),
    ]
    return b"".join(frames)


def _parse_sse(raw: bytes) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for frame in raw.split(b"\n\n"):
        for line in frame.split(b"\n"):
            if line.startswith(b"data:"):
                events.append(orjson.loads(line[5:]))
    return events


def _tool_input_from_events(events: list[dict[str, object]]) -> dict[str, object]:
    fragments: list[str] = []
    for event in events:
        match event:
            case {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"partial_json": str(piece)},
            }:
                fragments.append(piece)
    return orjson.loads("".join(fragments))


@pytest.mark.parametrize("chunk_size", [1, 7, 64, 100_000])
def test_stream_rewriter_strips_blank_pages_from_read(chunk_size: int) -> None:
    raw = _read_tool_use_stream(
        [
            '{"file_path": "/skills/demo/',
            'refs/api.md", "pages": "", ',
            '"offset": null}',
        ]
    )
    rewriter = ToolUseStreamRewriter()
    out = b"".join(
        rewriter.feed(raw[i : i + chunk_size]) for i in range(0, len(raw), chunk_size)
    )
    out += rewriter.flush()

    events = _parse_sse(out)
    assert [event["type"] for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_stop",
    ]
    assert _tool_input_from_events(events) == {"file_path": "/skills/demo/refs/api.md"}


def test_stream_rewriter_passes_valid_read_through_verbatim() -> None:
    raw = _read_tool_use_stream(
        ['{"file_path": "/skills/demo/refs/guide.pdf", ', '"pages": "1-5"}']
    )
    rewriter = ToolUseStreamRewriter()

    assert rewriter.feed(raw) + rewriter.flush() == raw


def test_stream_rewriter_forwards_text_before_tool_block_completes() -> None:
    raw = _read_tool_use_stream(['{"file_path": "/a.md", "pages": ""}'])
    first_start = raw.index(b"event: content_block_start")
    text_stop = raw.index(b"event: content_block_start", first_start + 1)
    rewriter = ToolUseStreamRewriter()

    first = rewriter.feed(raw[:text_stop])
    assert b"Reading" in first
    second = rewriter.feed(raw[text_stop:]) + rewriter.flush()
    assert b'"pages"' not in second


def test_sanitize_non_streaming_messages_response() -> None:
    body = orjson.dumps(
        {
            "type": "message",
            "content": [
                {"type": "text", "text": "ok"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Read",
                    "input": {"file_path": "/a.md", "pages": "", "limit": None},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "Bash",
                    "input": {"command": "ls", "pages": ""},
                },
            ],
        }
    )

    rewritten = orjson.loads(sanitize_messages_response_body(body))

    assert rewritten["content"][1]["input"] == {"file_path": "/a.md"}
    assert rewritten["content"][2]["input"] == {"command": "ls", "pages": ""}
    assert sanitize_messages_response_body(b"not json") == b"not json"
