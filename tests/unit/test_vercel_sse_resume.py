"""SSE composite-id + resume behaviour for the Vercel adapter stream."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tracecat.agent.adapter.vercel import sse_vercel
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.stream.events import (
    StreamDelta,
    StreamEnd,
    StreamEvent,
    parse_vercel_frame_cursor,
)


def _text_start(part_id: int = 0) -> UnifiedStreamEvent:
    return UnifiedStreamEvent(type=StreamEventType.TEXT_START, part_id=part_id)


def _text_delta(text: str, part_id: int = 0) -> UnifiedStreamEvent:
    return UnifiedStreamEvent(
        type=StreamEventType.TEXT_DELTA, part_id=part_id, text=text
    )


async def _events(*events: StreamEvent) -> AsyncIterator[StreamEvent]:
    for event in events:
        yield event


def _parse_frames(raw: list[str]) -> list[tuple[str | None, str]]:
    """Return (id, data) for each SSE frame that carries a data line."""
    out: list[tuple[str | None, str]] = []
    for chunk in raw:
        sse_id: str | None = None
        data: str | None = None
        for line in chunk.splitlines():
            if line.startswith("id: "):
                sse_id = line[len("id: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if data is not None:
            out.append((sse_id, data))
    return out


def test_parse_vercel_frame_cursor() -> None:
    cursor = parse_vercel_frame_cursor("1700000000-0:3")
    assert cursor is not None
    assert cursor.redis_id == "1700000000-0"
    assert cursor.frame_index == 3

    assert parse_vercel_frame_cursor(None) is None
    assert parse_vercel_frame_cursor("") is None
    # No frame-index segment -> not a composite cursor.
    assert parse_vercel_frame_cursor("1700000000-0") is None


@pytest.mark.anyio
async def test_sse_vercel_emits_composite_ids() -> None:
    frames = [
        chunk
        async for chunk in sse_vercel(
            _events(
                StreamDelta(id="1000-0", event=_text_start()),
                StreamDelta(id="1001-0", event=_text_delta("hello")),
                StreamEnd(id="1002-0"),
            ),
            message_id="session:run",
        )
    ]
    parsed = _parse_frames(frames)

    # The start frame carries the stable bubble id.
    assert any('"messageId":"session:run"' in data for _, data in parsed)
    # Frames fanned from entry 1001-0 carry composite redis_id:frame_index ids.
    delta_ids = [
        sse_id for sse_id, _ in parsed if sse_id and sse_id.startswith("1001-0:")
    ]
    assert delta_ids, parsed
    assert delta_ids[0] == "1001-0:0"


@pytest.mark.anyio
async def test_sse_vercel_omits_start_when_no_message_id() -> None:
    frames = [
        chunk
        async for chunk in sse_vercel(
            _events(StreamEnd(id="1001-0")),
            message_id=None,
        )
    ]
    # No StartEvent (messageId) frame when there is no bubble id.
    assert not any('"type":"start"' in chunk for chunk in frames)
    # Still terminates cleanly.
    assert any("[DONE]" in chunk for chunk in frames)


@pytest.mark.anyio
async def test_sse_vercel_resume_mid_text_preserves_tail() -> None:
    """Resuming mid-text-block renders the tail (no loss) and re-sends nothing.

    The TEXT_START is in an entry before the cursor, so the fresh context never
    re-reads it. Without the self-heal, every delta after the cursor would hit
    "unknown part" and be silently dropped. The fix opens a repair part so the
    tail renders, while the already-seen delta at the cursor is still dropped.
    """
    # Original stream: start@1000-0, "world"@1001-0, "!"@1002-0. Client's cursor
    # is 1001-0:0 (it saw "world"). Resume re-reads from 1001-0 onward; the start
    # at 1000-0 is NOT re-read.
    frames = [
        chunk
        async for chunk in sse_vercel(
            _events(
                StreamDelta(id="1001-0", event=_text_delta("world")),
                StreamDelta(id="1002-0", event=_text_delta("!")),
                StreamEnd(id="1003-0"),
            ),
            message_id="session:run",
            resume_from="1001-0:0",
        )
    ]
    parsed = _parse_frames(frames)
    deltas = [data for _, data in parsed if '"type":"text-delta"' in data]
    # The already-seen "world" is dropped; only the tail "!" is re-emitted.
    assert len(deltas) == 1
    assert '"delta":"!"' in deltas[0]
    # The repair text-start carries no id (it is not a resumable position).
    text_starts = [(sse_id, data) for sse_id, data in parsed if '"text-start"' in data]
    assert text_starts
    assert all(sse_id is None for sse_id, _ in text_starts)


@pytest.mark.anyio
async def test_sse_vercel_resume_drops_seen_frames() -> None:
    # Resume from frame 0 of entry 1001-0: that frame must be dropped, later
    # entries kept.
    frames = [
        chunk
        async for chunk in sse_vercel(
            _events(
                StreamDelta(id="1000-0", event=_text_start()),
                StreamDelta(id="1001-0", event=_text_delta("a")),
                StreamDelta(id="1002-0", event=_text_delta("b")),
                StreamEnd(id="1003-0"),
            ),
            message_id="session:run",
            resume_from="1001-0:0",
        )
    ]
    parsed = _parse_frames(frames)
    emitted_ids = [sse_id for sse_id, _ in parsed if sse_id]
    # The already-seen frame 1001-0:0 is not re-emitted.
    assert "1001-0:0" not in emitted_ids
    # A later entry's frame is still emitted.
    assert any(sse_id and sse_id.startswith("1002-0:") for sse_id in emitted_ids)
