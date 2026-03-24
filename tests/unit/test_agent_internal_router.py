"""Tests for the internal agent execution router."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tracecat.agent.internal_router import (
    _provider_secrets_context_for_config,
    run_agent_endpoint,
)
from tracecat.agent.schemas import AgentOutput, InternalRunAgentRequest
from tracecat.agent.types import AgentConfig, AgentModelConfig
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role


@pytest.fixture
def executor_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-agent-executor"],
    )


@pytest.mark.anyio
async def test_run_agent_endpoint_preserves_fallback_models(
    executor_role: Role,
) -> None:
    params = InternalRunAgentRequest.model_validate(
        {
            "user_prompt": "Investigate this alert",
            "config": {
                "model_name": "gpt-5.2",
                "model_provider": "openai",
                "fallback_models": [
                    {
                        "model_name": "claude-3-7-sonnet",
                        "model_provider": "anthropic",
                        "base_url": None,
                    }
                ],
            },
        }
    )

    @asynccontextmanager
    async def noop_provider_context(*args, **kwargs):
        yield

    with (
        patch("tracecat.agent.internal_router.AgentManagementService"),
        patch(
            "tracecat.agent.internal_router._provider_secrets_context_for_config",
            noop_provider_context,
        ),
        patch(
            "tracecat.agent.internal_router.runtime_run_agent",
            AsyncMock(
                return_value=AgentOutput(
                    output={"ok": True},
                    duration=0.1,
                    session_id=uuid.uuid4(),
                )
            ),
        ) as mock_runtime_run_agent,
    ):
        role_token = ctx_role.set(executor_role)
        try:
            result = await run_agent_endpoint(
                role=executor_role,
                session=MagicMock(),
                params=params,
            )
        finally:
            ctx_role.reset(role_token)

    assert result["output"] == {"ok": True}
    assert mock_runtime_run_agent.await_args is not None
    fallback_models = mock_runtime_run_agent.await_args.kwargs["fallback_models"]
    assert fallback_models is not None
    assert len(fallback_models) == 1
    assert fallback_models[0].model_name == "claude-3-7-sonnet"
    assert fallback_models[0].model_provider == "anthropic"
    assert fallback_models[0].base_url is None


@pytest.mark.anyio
async def test_provider_secrets_context_for_config_merges_disjoint_provider_keys() -> (
    None
):
    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
        fallback_models=[
            AgentModelConfig(
                model_name="claude-3-7-sonnet",
                model_provider="anthropic",
            ),
            AgentModelConfig(
                model_name="llama3.2",
                model_provider="ollama",
            ),
        ],
    )
    agent_svc = MagicMock()
    agent_svc.get_runtime_provider_credentials = AsyncMock(
        side_effect=lambda provider: {
            "openai": {"OPENAI_API_KEY": "openai-key"},
            "anthropic": {"ANTHROPIC_API_KEY": "anthropic-key"},
        }.get(provider)
    )

    with (
        patch(
            "tracecat.agent.internal_router.registry_secrets.set_context",
            return_value=object(),
        ) as mock_set_context,
        patch("tracecat.agent.internal_router.registry_secrets.reset_context"),
    ):
        async with _provider_secrets_context_for_config(agent_svc, config):
            pass

    agent_svc.get_runtime_provider_credentials.assert_any_await("openai")
    agent_svc.get_runtime_provider_credentials.assert_any_await("anthropic")
    assert agent_svc.get_runtime_provider_credentials.await_count == 2
    mock_set_context.assert_called_once_with(
        {
            "OPENAI_API_KEY": "openai-key",
            "ANTHROPIC_API_KEY": "anthropic-key",
        }
    )


@pytest.mark.anyio
async def test_run_agent_endpoint_rejects_colliding_provider_secret_keys(
    executor_role: Role,
) -> None:
    params = InternalRunAgentRequest.model_validate(
        {
            "user_prompt": "Investigate this alert",
            "config": {
                "model_name": "gpt-4o",
                "model_provider": "azure_openai",
                "fallback_models": [
                    {
                        "model_name": "claude-sonnet-4-5",
                        "model_provider": "azure_ai",
                    }
                ],
            },
        }
    )
    mock_agent_svc = MagicMock()
    mock_agent_svc.get_runtime_provider_credentials = AsyncMock(
        side_effect=lambda provider: {
            "azure_openai": {
                "AZURE_API_BASE": "https://openai.example.com",
                "AZURE_API_KEY": "openai-key",
                "AZURE_API_VERSION": "2024-02-15-preview",
                "AZURE_DEPLOYMENT_NAME": "gpt-4o",
            },
            "azure_ai": {
                "AZURE_API_BASE": "https://azure-ai.example.com/anthropic",
                "AZURE_API_KEY": "azure-ai-key",
                "AZURE_AI_MODEL_NAME": "claude-sonnet-4-5",
            },
        }[provider]
    )

    with (
        patch(
            "tracecat.agent.internal_router.AgentManagementService",
            return_value=mock_agent_svc,
        ),
        patch(
            "tracecat.agent.internal_router.runtime_run_agent", AsyncMock()
        ) as mock_runtime_run_agent,
    ):
        role_token = ctx_role.set(executor_role)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await run_agent_endpoint(
                    role=executor_role,
                    session=MagicMock(),
                    params=params,
                )
        finally:
            ctx_role.reset(role_token)

    assert exc_info.value.status_code == 400
    assert "AZURE_API_BASE" in str(exc_info.value.detail)
    assert "AZURE_API_KEY" in str(exc_info.value.detail)
    assert "azure_ai" in str(exc_info.value.detail)
    assert "azure_openai" in str(exc_info.value.detail)
    mock_runtime_run_agent.assert_not_awaited()
