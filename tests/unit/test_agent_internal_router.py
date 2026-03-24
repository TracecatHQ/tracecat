"""Tests for the internal agent execution router."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.internal_router import run_agent_endpoint
from tracecat.agent.schemas import AgentOutput, InternalRunAgentRequest
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES


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
        result = await run_agent_endpoint(
            role=executor_role,
            session=MagicMock(),
            params=params,
        )

    assert result["output"] == {"ok": True}
    assert mock_runtime_run_agent.await_args is not None
    fallback_models = mock_runtime_run_agent.await_args.kwargs["fallback_models"]
    assert fallback_models is not None
    assert len(fallback_models) == 1
    assert fallback_models[0].model_name == "claude-3-7-sonnet"
    assert fallback_models[0].model_provider == "anthropic"
    assert fallback_models[0].base_url is None
