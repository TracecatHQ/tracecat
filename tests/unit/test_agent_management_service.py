"""Tests for AgentManagementService credential context behavior."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from tracecat_registry._internal import secrets as registry_secrets

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.secrets import secrets_manager


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:read", "org:secret:read"}),
    )


@pytest.mark.anyio
async def test_with_model_config_sets_registry_and_env_context(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_default_model = AsyncMock(return_value="claude-opus-4-5-20251101")
    service.get_provider_credentials = AsyncMock(
        return_value={"ANTHROPIC_API_KEY": "test-key"}
    )

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None

    async with service.with_model_config(use_workspace_credentials=False):
        assert registry_secrets.get("ANTHROPIC_API_KEY") == "test-key"
        assert secrets_manager.get("ANTHROPIC_API_KEY") == "test-key"

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None


@pytest.mark.anyio
async def test_with_preset_config_sets_registry_and_env_context(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.presets = cast(
        AgentPresetService,
        SimpleNamespace(
            resolve_agent_preset_config=AsyncMock(
                return_value=AgentConfig(
                    model_name="claude-opus-4-5-20251101",
                    model_provider="anthropic",
                )
            )
        ),
    )
    service.get_workspace_provider_credentials = AsyncMock(
        return_value={"ANTHROPIC_API_KEY": "workspace-key"}
    )

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None

    async with service.with_preset_config(
        preset_id=uuid.uuid4(),
        use_workspace_credentials=True,
    ):
        assert registry_secrets.get("ANTHROPIC_API_KEY") == "workspace-key"
        assert secrets_manager.get("ANTHROPIC_API_KEY") == "workspace-key"

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None
