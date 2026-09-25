"""Child session events on a parent stream, rendered by the Vercel adapter."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tracecat.agent.adapter.vercel import sse_vercel
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.stream.events import (
    StreamDelta,
    StreamEnd,
    StreamEvent,
    StreamSessionEvent,
)

CHILD_A = uuid.UUID("0b5c6f7e-1d2a-4c3b-9e8f-7a6b5c4d3e2f")
CHILD_B = uuid.UUID("5f0d3c2b-8a7e-4f61-b0c9-1e2d3c4b5a69")


def _text(event_type: StreamEventType, text: str | None = None) -> UnifiedStreamEvent:
    return UnifiedStreamEvent(type=event_type, part_id=0, text=text)


def _child(
    redis_id: str, session_id: uuid.UUID, event_id: str, event: UnifiedStreamEvent
) -> StreamSessionEvent:
    return StreamSessionEvent(
        id=redis_id, session_id=session_id, event_id=event_id, event=event
    )


def _interleaved_events() -> list[StreamEvent]:
    """Root runs a subagent call while two children stream on part index 0."""
    return [
        StreamDelta(
            id="1-0",
            event=UnifiedStreamEvent(
                type=StreamEventType.TOOL_CALL_START,
                part_id=0,
                tool_call_id="call_a",
                tool_name="subagent",
                tool_input={"alias": "triage", "task": "Summarize"},
            ),
        ),
        StreamDelta(
            id="2-0",
            event=UnifiedStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                tool_call_id="call_a",
                tool_name="subagent",
                tool_output={"session_id": str(CHILD_A), "status": "running"},
                preliminary=True,
            ),
        ),
        _child(
            "3-0",
            CHILD_A,
            "a-0",
            UnifiedStreamEvent(type=StreamEventType.MESSAGE_START),
        ),
        _child("4-0", CHILD_A, "a-1", _text(StreamEventType.TEXT_START, "alpha")),
        _child("5-0", CHILD_B, "b-1", _text(StreamEventType.TEXT_START, "beta")),
        StreamDelta(id="6-0", event=_text(StreamEventType.TEXT_START, "root")),
        _child("7-0", CHILD_A, "a-2", _text(StreamEventType.TEXT_DELTA, " one")),
        _child("8-0", CHILD_B, "b-2", _text(StreamEventType.TEXT_DELTA, " two")),
        _child("9-0", CHILD_A, "a-3", _text(StreamEventType.TEXT_STOP)),
        _child("10-0", CHILD_B, "b-3", _text(StreamEventType.TEXT_STOP)),
        _child(
            "11-0",
            CHILD_A,
            "a-4",
            UnifiedStreamEvent(type=StreamEventType.MESSAGE_STOP),
        ),
        _child("12-0", CHILD_A, "a-5", UnifiedStreamEvent(type=StreamEventType.DONE)),
        StreamDelta(id="13-0", event=_text(StreamEventType.TEXT_STOP)),
        StreamDelta(
            id="14-0",
            event=UnifiedStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                tool_call_id="call_a",
                tool_name="subagent",
                tool_output={"session_id": str(CHILD_A), "summary": "done"},
            ),
        ),
        StreamEnd(id="15-0"),
    ]


async def _iterate(events: list[StreamEvent]) -> AsyncIterator[StreamEvent]:
    for event in events:
        yield event


async def _frames(events: list[StreamEvent]) -> list[tuple[str | None, Any]]:
    """Return ``(sse_id, payload)`` for every data frame except ``[DONE]``."""
    frames: list[tuple[str | None, Any]] = []
    async for raw in sse_vercel(_iterate(events), message_id="session:run"):
        sse_id: str | None = None
        data: str | None = None
        for line in raw.splitlines():
            if line.startswith("id: "):
                sse_id = line.removeprefix("id: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if data is not None and data != "[DONE]":
            frames.append((sse_id, json.loads(data)))
    return frames


def _child_chunks(
    frames: list[tuple[str | None, Any]], session_id: uuid.UUID
) -> list[dict[str, Any]]:
    return [
        payload["data"]
        for _, payload in frames
        if payload["type"] == "data-agent-chunk"
        and payload["data"]["session_id"] == str(session_id)
    ]


@pytest.mark.anyio
async def test_child_sessions_stream_isolated_from_root_and_each_other() -> None:
    frames = await _frames(_interleaved_events())

    # Child chunks are always wrapped as transient data parts.
    for _, payload in frames:
        if payload["type"] == "data-agent-chunk":
            assert payload["transient"] is True
            assert set(payload) == {"type", "transient", "data"}
            assert set(payload["data"]) == {"session_id", "event_id", "index", "chunk"}

    # The root transcript only sees root chunks.
    root = [payload for _, payload in frames if payload["type"] != "data-agent-chunk"]
    assert [p["type"] for p in root] == [
        "start",
        "tool-input-start",
        "tool-input-available",
        "tool-output-available",
        "text-start",
        "text-delta",
        "text-end",
        "tool-output-available",
        "finish",
    ]
    assert root[0] == {"type": "start", "messageId": "session:run"}
    assert root[3]["preliminary"] is True
    assert root[3]["output"] == {"session_id": str(CHILD_A), "status": "running"}
    assert "preliminary" not in root[7]
    assert root[7]["output"] == {"session_id": str(CHILD_A), "summary": "done"}
    assert root[5]["delta"] == "root"

    chunks_a = [c["chunk"] for c in _child_chunks(frames, CHILD_A)]
    chunks_b = [c["chunk"] for c in _child_chunks(frames, CHILD_B)]
    # Each child starts its own message; lifecycle events emit nothing and no
    # child finish is sent.
    assert [c["type"] for c in chunks_a] == [
        "start",
        "text-start",
        "text-delta",
        "text-delta",
        "text-end",
    ]
    assert [c["type"] for c in chunks_b] == [
        "start",
        "text-start",
        "text-delta",
        "text-delta",
        "text-end",
    ]
    assert chunks_a[0] == {"type": "start", "messageId": str(CHILD_A)}
    assert chunks_b[0] == {"type": "start", "messageId": str(CHILD_B)}
    assert "".join(c["delta"] for c in chunks_a if c["type"] == "text-delta") == (
        "alpha one"
    )
    assert "".join(c["delta"] for c in chunks_b if c["type"] == "text-delta") == (
        "beta two"
    )

    # Same provider part index in every session, yet no shared part ids.
    part_ids_a = {c["id"] for c in chunks_a if c["type"] != "start"}
    part_ids_b = {c["id"] for c in chunks_b if c["type"] != "start"}
    root_part_ids = {p["id"] for p in root if p["type"].startswith("text-")}
    assert len(part_ids_a) == len(part_ids_b) == len(root_part_ids) == 1
    assert part_ids_a.isdisjoint(part_ids_b)
    assert part_ids_a.isdisjoint(root_part_ids)
    assert part_ids_b.isdisjoint(root_part_ids)


@pytest.mark.anyio
async def test_child_chunks_carry_composite_frame_ids() -> None:
    frames = await _frames(_interleaved_events())

    ids_by_entry: dict[str, list[int]] = {}
    for sse_id, payload in frames:
        if payload["type"] != "data-agent-chunk":
            continue
        assert sse_id is not None
        redis_id, _, frame_index = sse_id.rpartition(":")
        ids_by_entry.setdefault(redis_id, []).append(int(frame_index))

    # Frame indices count every frame fanned out from one Redis entry,
    # including the child start chunk on first sight.
    assert ids_by_entry["3-0"] == [0]
    assert ids_by_entry["4-0"] == [0, 1]
    assert ids_by_entry["5-0"] == [0, 1, 2]
    assert ids_by_entry["7-0"] == [0]
    assert "11-0" not in ids_by_entry
    assert "12-0" not in ids_by_entry


@pytest.mark.anyio
async def test_child_chunk_keys_are_stable_and_unique_across_replay() -> None:
    first = await _frames(_interleaved_events())
    replay = await _frames(_interleaved_events())

    def child_chunks(
        frames: list[tuple[str | None, Any]],
    ) -> list[tuple[str | None, dict[str, Any]]]:
        return [
            (sse_id, payload["data"])
            for sse_id, payload in frames
            if payload["type"] == "data-agent-chunk"
        ]

    # A replay from the start reproduces identical keys, chunks (including part
    # ids), and frame ids, so client-side dedupe drops exactly the seen chunks.
    assert child_chunks(first) == child_chunks(replay)

    keys = [
        (data["session_id"], data["event_id"], data["index"])
        for _, data in child_chunks(first)
    ]
    assert len(keys) == len(set(keys))
    assert keys[:2] == [(str(CHILD_A), "a-0", 0), (str(CHILD_A), "a-1", 0)]
    # Child B is first seen on a content event: its start chunk takes index 0.
    assert [key for key in keys if key[1] == "b-1"] == [
        (str(CHILD_B), "b-1", 0),
        (str(CHILD_B), "b-1", 1),
        (str(CHILD_B), "b-1", 2),
    ]
