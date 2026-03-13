import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.internal_router import (
    _merge_runtime_overrides,
    _provider_secrets_context,
)
from tracecat.agent.service import AgentManagementService
from tracecat.agent.types import AgentConfig


def test_merge_runtime_overrides_preserves_unset_catalog_fields() -> None:
    base = AgentConfig(
        model_name="gpt-5",
        model_provider="openai",
        instructions="catalog instructions",
        retries=7,
        enable_internet_access=True,
        base_url="https://catalog.example/v1",
    )
    overrides = AgentConfig(
        model_name="gpt-5",
        model_provider="openai",
        instructions="request instructions",
        retries=20,
        enable_internet_access=False,
        base_url=None,
    )

    merged = _merge_runtime_overrides(
        base,
        overrides,
        override_fields={"instructions"},
    )

    assert merged.instructions == "request instructions"
    assert merged.retries == 7
    assert merged.enable_internet_access is True
    assert merged.base_url == "https://catalog.example/v1"


@pytest.mark.anyio
async def test_provider_secrets_context_preserves_explicit_base_url_override() -> None:
    config = AgentConfig(
        model_name="gpt-5",
        model_provider="openai",
        base_url="https://override.example/v1",
    )
    get_runtime_credentials_for_config = AsyncMock(
        return_value={
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://creds.example/v1",
        }
    )
    agent_svc = cast(
        AgentManagementService,
        SimpleNamespace(
            get_runtime_credentials_for_config=get_runtime_credentials_for_config
        ),
    )

    async with _provider_secrets_context(agent_svc, config):
        assert config.base_url == "https://override.example/v1"


@pytest.mark.anyio
async def test_provider_secrets_context_uses_workspace_credentials_fallback() -> None:
    config = AgentConfig(
        model_name="gpt-5",
        model_provider="openai",
    )
    get_runtime_credentials_for_config = AsyncMock(
        return_value={"OPENAI_API_KEY": "workspace-key"}
    )
    agent_svc = cast(
        AgentManagementService,
        SimpleNamespace(
            get_runtime_credentials_for_config=get_runtime_credentials_for_config
        ),
    )

    async with _provider_secrets_context(agent_svc, config):
        pass

    get_runtime_credentials_for_config.assert_awaited_once_with(config)


@pytest.mark.anyio
async def test_provider_secrets_context_prefers_runtime_credentials_for_enabled_builtin_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AgentConfig(
        model_name="claude-3-7-sonnet",
        model_provider="bedrock",
    )
    captured: dict[str, str] = {}

    def _set_context(credentials: dict[str, str]) -> str:
        captured.update(credentials)
        return "token"

    get_runtime_credentials_for_config = AsyncMock(
        return_value={"AWS_INFERENCE_PROFILE_ID": "profile-123"}
    )
    agent_svc = cast(
        AgentManagementService,
        SimpleNamespace(
            get_runtime_credentials_for_config=get_runtime_credentials_for_config,
        ),
    )

    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.set_context",
        _set_context,
    )
    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.reset_context",
        lambda _token: None,
    )

    async with _provider_secrets_context(agent_svc, config):
        assert captured == {"AWS_INFERENCE_PROFILE_ID": "profile-123"}

    get_runtime_credentials_for_config.assert_awaited_once_with(config)


@pytest.mark.anyio
async def test_provider_secrets_context_loads_custom_source_credentials_without_catalog_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = uuid.uuid4()
    config = AgentConfig(
        model_name="claude-3-7-sonnet",
        model_provider="anthropic",
        source_id=source_id,
        base_url=None,
    )
    captured: dict[str, str] = {}

    def _set_context(credentials: dict[str, str]) -> str:
        captured.update(credentials)
        return "token"

    get_runtime_credentials_for_selection = AsyncMock(
        return_value={
            "ANTHROPIC_API_KEY": "test-key",
            "TRACECAT_SOURCE_BASE_URL": "https://anthropic.gateway.example",
        }
    )
    agent_svc = cast(
        AgentManagementService,
        SimpleNamespace(
            get_runtime_credentials_for_config=get_runtime_credentials_for_selection,
        ),
    )

    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.set_context",
        _set_context,
    )
    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.reset_context",
        lambda _token: None,
    )

    async with _provider_secrets_context(agent_svc, config):
        assert config.base_url == "https://anthropic.gateway.example"
        assert captured == {
            "ANTHROPIC_API_KEY": "test-key",
            "TRACECAT_SOURCE_BASE_URL": "https://anthropic.gateway.example",
        }

    get_runtime_credentials_for_selection.assert_awaited_once_with(config)
