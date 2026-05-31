from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import orjson
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
from tracecat.artifacts.bindings import (
    ArtifactIdentityRef,
    ArtifactSideEffect,
    artifact_side_effects_for_tool_result,
)
from tracecat.artifacts.projection import (
    apply_artifact_side_effects,
    serialize_artifacts,
)
from tracecat.artifacts.resolution import resolve_artifact_side_effects
from tracecat.artifacts.schemas import ArtifactAdapter
from tracecat.auth.types import Role
from tracecat.chat import tokens
from tracecat.exceptions import TracecatNotFoundError
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
async def test_resolve_artifact_side_effects_resolves_table_name_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_table_by_name(
        _service: object,
        table_name: str,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            name=table_name,
        )

    monkeypatch.setattr(
        "tracecat.artifacts.resolution.TablesService.get_table_by_name",
        fake_get_table_by_name,
    )

    artifact = ArtifactAdapter.validate_python(
        {
            "type": "table",
            "id": "indicators",
            "title": "indicators",
        }
    )
    effects = [
        ArtifactSideEffect(
            op="upsert",
            artifact=artifact,
            identity_ref=ArtifactIdentityRef(
                artifact_type="table",
                ref="indicators",
                ref_kind="name",
            ),
        )
    ]

    resolved = await resolve_artifact_side_effects(
        effects,
        session=cast(AsyncSession, object()),
        role=Role(
            type="service",
            service_id="tracecat-api",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        ),
    )

    assert len(resolved) == 1
    assert resolved[0].op == "upsert"
    assert resolved[0].artifact.id == "11111111-1111-4111-8111-111111111111"
    assert resolved[0].artifact.title == "indicators"
    assert resolved[0].identity_ref is None


@pytest.mark.anyio
async def test_resolve_artifact_side_effects_resolves_table_id_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_id = uuid.UUID("22222222-2222-4222-8222-222222222222")

    async def fake_get_table_by_name(
        _service: object,
        _table_name: str,
    ) -> None:
        raise TracecatNotFoundError("Table not found")

    async def fake_get_table(
        _service: object,
        requested_table_id: uuid.UUID,
    ) -> SimpleNamespace:
        assert requested_table_id == table_id
        return SimpleNamespace(id=table_id, name="indicators")

    monkeypatch.setattr(
        "tracecat.artifacts.resolution.TablesService.get_table_by_name",
        fake_get_table_by_name,
    )
    monkeypatch.setattr(
        "tracecat.artifacts.resolution.TablesService.get_table",
        fake_get_table,
    )

    artifact = ArtifactAdapter.validate_python(
        {
            "type": "table",
            "id": str(table_id),
            "title": str(table_id),
        }
    )

    resolved = await resolve_artifact_side_effects(
        [
            ArtifactSideEffect(
                op="upsert",
                artifact=artifact,
                identity_ref=ArtifactIdentityRef(
                    artifact_type="table",
                    ref=str(table_id),
                    ref_kind="id",
                ),
            )
        ],
        session=cast(AsyncSession, object()),
        role=Role(
            type="service",
            service_id="tracecat-api",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        ),
    )

    assert len(resolved) == 1
    assert resolved[0].artifact.id == str(table_id)
    assert resolved[0].artifact.title == "indicators"
    assert resolved[0].identity_ref is None


@pytest.mark.anyio
async def test_resolve_artifact_side_effects_skips_invalid_table_name_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_table_by_name(
        _service: object,
        _table_name: str,
    ) -> None:
        raise ValueError("Invalid table name")

    monkeypatch.setattr(
        "tracecat.artifacts.resolution.TablesService.get_table_by_name",
        fake_get_table_by_name,
    )

    artifact = ArtifactAdapter.validate_python(
        {
            "type": "table",
            "id": "bad-name",
            "title": "bad-name",
        }
    )

    resolved = await resolve_artifact_side_effects(
        [
            ArtifactSideEffect(
                op="upsert",
                artifact=artifact,
                identity_ref=ArtifactIdentityRef(
                    artifact_type="table",
                    ref="bad-name",
                    ref_kind="name",
                ),
            )
        ],
        session=cast(AsyncSession, object()),
        role=Role(
            type="service",
            service_id="tracecat-api",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        ),
    )

    assert resolved == []


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
