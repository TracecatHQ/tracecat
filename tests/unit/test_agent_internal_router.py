import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

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
        model_name="ignored",
        model_provider="ignored",
        model_catalog_ref="builtin:openai:test:gpt-5",
        base_url="https://override.example/v1",
    )
    agent_svc = cast(
        AgentManagementService,
        SimpleNamespace(
            _resolve_catalog_agent_config=AsyncMock(
                return_value=(
                    AgentConfig(
                        model_name="gpt-5",
                        model_provider="openai",
                        base_url="https://catalog.example/v1",
                    ),
                    {"OPENAI_API_KEY": "test-key"},
                )
            )
        ),
    )

    async with _provider_secrets_context(agent_svc, config):
        assert config.model_name == "gpt-5"
        assert config.model_provider == "openai"
        assert config.base_url == "https://override.example/v1"


@pytest.mark.anyio
async def test_provider_secrets_context_loads_custom_source_credentials_without_catalog_ref() -> (
    None
):
    source_id = uuid.uuid4()
    config = AgentConfig(
        model_name="qwen2.5:0.5b",
        model_provider="openai_compatible_gateway",
        model_source_type="openai_compatible_gateway",
        model_source_id=source_id,
        base_url=None,
    )
    agent_svc = cast(
        AgentManagementService,
        SimpleNamespace(
            get_model_source=AsyncMock(
                return_value=SimpleNamespace(
                    encrypted_config=b"encrypted",
                    base_url="http://localhost:11434/v1",
                )
            ),
            _deserialize_sensitive_config=Mock(return_value={"api_key": "test-key"}),
        ),
    )

    async with _provider_secrets_context(agent_svc, config):
        assert config.base_url == "http://localhost:11434/v1"
