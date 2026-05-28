from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import orjson
import pytest

from tracecat.agent.adapter.vercel import (
    DataEventPayload,
    VercelSSEPayload,
    VercelStreamContext,
)
from tracecat.agent.common.stream_types import StreamEventType
from tracecat.agent.stream.artifacts import (
    artifact_stream_event,
    artifact_stream_events_for_tool_result,
)
from tracecat.agent.stream.connector import AgentStream
from tracecat.artifacts.bindings import artifact_side_effects_for_tool_result
from tracecat.artifacts.projection import (
    apply_artifact_side_effects,
    serialize_artifacts,
)
from tracecat.artifacts.schemas import ArtifactAdapter
from tracecat.chat import tokens
from tracecat.redis.client import RedisClient


async def collect_frames(
    ctx: VercelStreamContext,
    events: list,
) -> list[VercelSSEPayload]:
    frames: list[VercelSSEPayload] = []
    for event in events:
        async for frame in ctx.handle_event(event):
            frames.append(frame)
    for frame in ctx.collect_current_part_end_events():
        frames.append(frame)
    return frames


def test_artifact_stream_event_uses_semantic_event_type() -> None:
    artifact = ArtifactAdapter.validate_python(
        {
            "type": "workflow",
            "id": "wf_123",
            "title": "Triage workflow",
            "color": "#64748b",
            "isPublished": True,
        }
    )

    event = artifact_stream_event("upsert", artifact)

    assert event.type is StreamEventType.ARTIFACT
    assert event.artifact_data is not None
    assert event.artifact_data.op == "upsert"
    assert event.artifact_data.artifact == {
        "type": "workflow",
        "id": "wf_123",
        "title": "Triage workflow",
        "color": "#64748b",
        "isPublished": True,
    }


def test_artifact_stream_events_for_tool_result_projects_side_effects() -> None:
    events = list(
        artifact_stream_events_for_tool_result(
            tool_name="core.cases.create_case",
            tool_input={"summary": "Suspicious login"},
            tool_output={
                "id": "case_123",
                "summary": "Suspicious login",
                "severity": "high",
                "status": "new",
            },
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event.type is StreamEventType.ARTIFACT
    assert event.artifact_data is not None
    assert event.artifact_data.op == "upsert"
    assert event.artifact_data.artifact == {
        "type": "case",
        "id": "case_123",
        "title": "Suspicious login",
        "scope": {"parentToolCallId": "toolu_123"},
        "severity": "high",
        "status": "new",
    }


@pytest.mark.anyio
async def test_tool_result_artifact_pipeline_persists_and_streams_data_part() -> None:
    """Cover tool result -> projection -> Redis event -> Vercel data-artifact."""
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.cases.create_case",
            tool_input={"summary": "Suspicious login"},
            tool_output={
                "id": "case_123",
                "summary": "Suspicious login",
                "severity": "high",
                "status": "new",
            },
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    projected = apply_artifact_side_effects([], effects)
    assert serialize_artifacts(projected) == [
        {
            "type": "case",
            "id": "case_123",
            "title": "Suspicious login",
            "scope": {"parentToolCallId": "toolu_123"},
            "severity": "high",
            "status": "new",
        }
    ]

    [effect] = effects
    event = artifact_stream_event(effect.op, effect.artifact)
    client = SimpleNamespace(xadd=AsyncMock())
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )

    await stream.append(event)

    fields = client.xadd.await_args.args[1]
    reconstructed = orjson.loads(fields[tokens.DATA_KEY])
    assert reconstructed["type"] == "artifact"
    assert reconstructed["artifact_data"] == {
        "op": "upsert",
        "artifact": {
            "type": "case",
            "id": "case_123",
            "title": "Suspicious login",
            "scope": {"parentToolCallId": "toolu_123"},
            "severity": "high",
            "status": "new",
        },
    }

    frames = await collect_frames(
        VercelStreamContext(message_id="msg_test"),
        [event],
    )
    assert frames == [
        DataEventPayload(
            type="data-artifact",
            data={
                "op": "upsert",
                "artifact": {
                    "type": "case",
                    "id": "case_123",
                    "title": "Suspicious login",
                    "scope": {"parentToolCallId": "toolu_123"},
                    "severity": "high",
                    "status": "new",
                },
            },
        )
    ]
