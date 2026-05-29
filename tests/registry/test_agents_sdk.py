"""Tests for the Agents SDK client."""

from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.sdk.agents import AgentConfig, AgentsClient


@pytest.fixture
def mock_tracecat_client() -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock()
    client.patch = AsyncMock()
    client.post = AsyncMock()
    return client


@pytest.fixture
def agents_client(mock_tracecat_client: MagicMock) -> AgentsClient:
    return AgentsClient(mock_tracecat_client)


@pytest.mark.anyio
async def test_run_serializes_config_catalog_id(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    catalog_id = uuid.uuid4()
    mock_tracecat_client.post.return_value = {
        "output": "ok",
        "duration": 0.1,
        "usage": {},
        "session_id": str(uuid.uuid4()),
    }

    await agents_client.run(
        user_prompt="Summarize this",
        config=AgentConfig(
            model_name="gpt-4.1",
            model_provider="openai",
            catalog_id=catalog_id,
        ),
    )

    mock_tracecat_client.post.assert_awaited_once()
    _, kwargs = mock_tracecat_client.post.await_args
    assert kwargs["json"]["config"]["catalog_id"] == str(catalog_id)


@pytest.mark.anyio
async def test_run_omits_null_config_fields(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    mock_tracecat_client.post.return_value = {
        "output": "ok",
        "duration": 0.1,
        "usage": {},
        "session_id": str(uuid.uuid4()),
    }

    await agents_client.run(
        user_prompt="Summarize this",
        config=AgentConfig(
            model_name="gpt-4.1",
            model_provider="openai",
        ),
    )

    mock_tracecat_client.post.assert_awaited_once()
    _, kwargs = mock_tracecat_client.post.await_args
    config = kwargs["json"]["config"]
    assert config["model_name"] == "gpt-4.1"
    assert config["model_provider"] == "openai"
    assert "agents" not in config
    assert "catalog_id" not in config


def test_agent_config_rejects_agents_option() -> None:
    config_kwargs = cast(
        Any,
        {
            "model_name": "gpt-4.1",
            "model_provider": "openai",
            "agents": {"enabled": True},
        },
    )

    with pytest.raises(TypeError, match="agents"):
        AgentConfig(**config_kwargs)


@pytest.mark.anyio
async def test_list_skill_versions_builds_cursor_params(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    mock_tracecat_client.get.return_value = {
        "items": [],
        "next_cursor": None,
        "has_more": False,
    }

    result = await agents_client.list_skill_versions(
        skill_id="skill-id",
        limit=50,
        cursor="cursor-1",
        reverse=True,
    )

    assert result == {"items": [], "next_cursor": None, "has_more": False}
    mock_tracecat_client.get.assert_awaited_once_with(
        "/agent/skills/skill-id/versions",
        params={"limit": 50, "reverse": True, "cursor": "cursor-1"},
    )


@pytest.mark.anyio
async def test_list_skill_versions_prefers_skill_uuid(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    skill_uuid = uuid.uuid4()
    mock_tracecat_client.get.return_value = {
        "items": [],
        "next_cursor": None,
        "has_more": False,
    }

    await agents_client.list_skill_versions(
        skill_id="skill-id",
        skill_uuid=skill_uuid,
    )

    mock_tracecat_client.get.assert_awaited_once_with(
        f"/agent/skills/{skill_uuid}/versions",
        params={"limit": 20, "reverse": False},
    )


@pytest.mark.anyio
async def test_get_skill_version_uses_version_endpoint(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    mock_tracecat_client.get.return_value = {"id": "version-id"}

    result = await agents_client.get_skill_version(
        skill_id="skill-id", version_id="version-id"
    )

    assert result == {"id": "version-id"}
    mock_tracecat_client.get.assert_awaited_once_with(
        "/agent/skills/skill-id/versions/version-id"
    )


@pytest.mark.anyio
async def test_restore_skill_version_uses_restore_endpoint(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    mock_tracecat_client.post.return_value = {"current_version_id": "version-id"}

    result = await agents_client.restore_skill_version(
        skill_id="skill-id", version_id="version-id"
    )

    assert result == {"current_version_id": "version-id"}
    mock_tracecat_client.post.assert_awaited_once_with(
        "/agent/skills/skill-id/versions/version-id/restore"
    )


@pytest.mark.anyio
async def test_create_preset_omits_model_fields_when_not_provided(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    await agents_client.create_preset(
        name="Case Triage",
        actions=["core.cases.create_case"],
        enable_internet_access=True,
    )

    mock_tracecat_client.post.assert_awaited_once_with(
        "/agent/presets",
        json={
            "name": "Case Triage",
            "actions": ["core.cases.create_case"],
            "enable_internet_access": True,
        },
    )


@pytest.mark.anyio
async def test_create_preset_accepts_canonical_catalog_id_without_legacy_model_fields(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    await agents_client.create_preset(
        name="Case Triage",
        catalog_id="catalog_123",
    )

    mock_tracecat_client.post.assert_awaited_once_with(
        "/agent/presets",
        json={
            "name": "Case Triage",
            "catalog_id": "catalog_123",
        },
    )


@pytest.mark.anyio
async def test_update_preset_serializes_authoring_fields(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    await agents_client.update_preset(
        "case-triage",
        instructions="Triage cases.",
        tool_approvals={"core.cases.update_case": True},
        agents={"enabled": True, "subagents": []},
        skills=[{"slug": "triage", "settings": {}}],
    )

    mock_tracecat_client.patch.assert_awaited_once_with(
        "/agent/presets/by-slug/case-triage",
        json={
            "instructions": "Triage cases.",
            "tool_approvals": {"core.cases.update_case": True},
            "agents": {"enabled": True, "subagents": []},
            "skills": [{"slug": "triage", "settings": {}}],
        },
    )
