from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.preset.activities import (
    ResolveAgentPresetVersionRefActivityInput,
    resolve_agent_preset_version_ref_activity,
)
from tracecat.auth.types import Role


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.fixture(scope="session")
def minio_server() -> Iterator[None]:
    yield


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.mark.anyio
async def test_resolve_agent_preset_version_ref_activity_returns_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(id=uuid.uuid4(), preset_id=uuid.uuid4())
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version)
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agent_preset_version_ref_activity(
        ResolveAgentPresetVersionRefActivityInput(
            role=role,
            preset_slug="triage-agent",
            preset_version=3,
        )
    )

    service.resolve_agent_preset_version.assert_awaited_once_with(
        slug="triage-agent",
        preset_version=3,
    )
    assert result.preset_id == version.preset_id
    assert result.preset_version_id == version.id
