from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from tracecat.agent import internal_router
from tracecat.agent.schemas import AgentOutput, InternalRunAgentRequest, RunUsage
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role


@pytest.fixture
def agent_role() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )


@pytest.fixture
def agent_output() -> AgentOutput:
    return AgentOutput(
        output={"ok": True},
        message_history=None,
        duration=1.23,
        usage=RunUsage(requests=1, input_tokens=5, output_tokens=7),
        session_id=uuid.uuid4(),
    )


@pytest.mark.anyio
async def test_run_agent_endpoint_routes_mcp_runs_to_durable_workflow(
    agent_role: Role,
    agent_output: AgentOutput,
) -> None:
    params = InternalRunAgentRequest.model_validate(
        {
            "user_prompt": "Investigate Jira ticket",
            "config": {
                "model_name": "claude-3-5-sonnet-20241022",
                "model_provider": "anthropic",
                "mcp_servers": [
                    {"name": "jira", "url": "https://mcp.example.com/v1/mcp"}
                ],
            },
        }
    )
    mock_agent_svc = Mock()
    mock_agent_svc.presets = None

    token = ctx_role.set(agent_role)
    try:
        with (
            patch.object(
                internal_router,
                "AgentManagementService",
                return_value=mock_agent_svc,
            ),
            patch.object(
                internal_router,
                "_provider_secrets_context",
            ) as mock_provider_context,
            patch.object(
                internal_router,
                "_run_mcp_agent_workflow",
                AsyncMock(return_value=agent_output),
            ) as mock_workflow_run,
            patch.object(
                internal_router,
                "runtime_run_agent",
                AsyncMock(
                    side_effect=AssertionError("runtime_run_agent should not be called")
                ),
            ),
        ):
            mock_provider_context.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_provider_context.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await internal_router.run_agent_endpoint(
                role=agent_role,
                session=AsyncMock(),
                params=params,
            )

        assert result["output"] == {"ok": True}
        mock_workflow_run.assert_awaited_once()
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_run_agent_endpoint_routes_ollama_mcp_runs_to_pydantic_ai(
    agent_role: Role,
    agent_output: AgentOutput,
) -> None:
    params = InternalRunAgentRequest.model_validate(
        {
            "user_prompt": "Use the local MCP server",
            "config": {
                "model_name": "llama3.2",
                "model_provider": "ollama",
                "mcp_servers": [
                    {"name": "jira", "url": "https://mcp.example.com/v1/mcp"}
                ],
            },
        }
    )
    mock_agent_svc = Mock()
    mock_agent_svc.presets = None

    token = ctx_role.set(agent_role)
    try:
        with (
            patch.object(
                internal_router,
                "AgentManagementService",
                return_value=mock_agent_svc,
            ),
            patch.object(
                internal_router,
                "_provider_secrets_context",
            ) as mock_provider_context,
            patch.object(
                internal_router,
                "runtime_run_agent",
                AsyncMock(return_value=agent_output),
            ) as mock_runtime_run,
            patch.object(
                internal_router,
                "_run_mcp_agent_workflow",
                AsyncMock(
                    side_effect=AssertionError(
                        "_run_mcp_agent_workflow should not be called"
                    )
                ),
            ),
        ):
            mock_provider_context.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_provider_context.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await internal_router.run_agent_endpoint(
                role=agent_role,
                session=AsyncMock(),
                params=params,
            )

        assert result["output"] == {"ok": True}
        mock_runtime_run.assert_awaited_once()
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_run_agent_endpoint_rejects_missing_credentials_before_durable_mcp_run(
    agent_role: Role,
) -> None:
    params = InternalRunAgentRequest.model_validate(
        {
            "user_prompt": "Investigate Jira ticket",
            "config": {
                "model_name": "claude-3-5-sonnet-20241022",
                "model_provider": "anthropic",
                "mcp_servers": [
                    {"name": "jira", "url": "https://mcp.example.com/v1/mcp"}
                ],
            },
        }
    )
    mock_agent_svc = Mock()
    mock_agent_svc.presets = None

    token = ctx_role.set(agent_role)
    try:
        with (
            patch.object(
                internal_router,
                "AgentManagementService",
                return_value=mock_agent_svc,
            ),
            patch.object(
                internal_router,
                "_provider_secrets_context",
            ) as mock_provider_context,
            patch.object(
                internal_router,
                "_run_mcp_agent_workflow",
                AsyncMock(
                    side_effect=AssertionError(
                        "_run_mcp_agent_workflow should not be called"
                    )
                ),
            ),
        ):
            mock_provider_context.return_value.__aenter__ = AsyncMock(
                side_effect=ValueError("No credentials found for provider 'anthropic'.")
            )
            mock_provider_context.return_value.__aexit__ = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc_info:
                await internal_router.run_agent_endpoint(
                    role=agent_role,
                    session=AsyncMock(),
                    params=params,
                )

        assert exc_info.value.status_code == 400
        assert "No credentials found for provider 'anthropic'." in str(
            exc_info.value.detail
        )
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_run_agent_endpoint_rejects_invalid_output_type_before_durable_mcp_run(
    agent_role: Role,
) -> None:
    params = InternalRunAgentRequest.model_validate(
        {
            "user_prompt": "Investigate Jira ticket",
            "config": {
                "model_name": "claude-3-5-sonnet-20241022",
                "model_provider": "anthropic",
                "output_type": "json",
                "mcp_servers": [
                    {"name": "jira", "url": "https://mcp.example.com/v1/mcp"}
                ],
            },
        }
    )
    mock_agent_svc = Mock()
    mock_agent_svc.presets = None

    token = ctx_role.set(agent_role)
    try:
        with (
            patch.object(
                internal_router,
                "AgentManagementService",
                return_value=mock_agent_svc,
            ),
            patch.object(
                internal_router,
                "_run_mcp_agent_workflow",
                AsyncMock(
                    side_effect=AssertionError(
                        "_run_mcp_agent_workflow should not be called"
                    )
                ),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await internal_router.run_agent_endpoint(
                    role=agent_role,
                    session=AsyncMock(),
                    params=params,
                )

        assert exc_info.value.status_code == 400
        assert "Invalid output_type" in str(exc_info.value.detail)
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_run_agent_endpoint_routes_non_mcp_runs_to_pydantic_ai(
    agent_role: Role,
    agent_output: AgentOutput,
) -> None:
    params = InternalRunAgentRequest.model_validate(
        {
            "user_prompt": "Summarize the findings",
            "config": {
                "model_name": "claude-3-5-sonnet-20241022",
                "model_provider": "anthropic",
                "actions": ["core.cases.list_cases"],
            },
        }
    )
    mock_agent_svc = Mock()
    mock_agent_svc.presets = None

    token = ctx_role.set(agent_role)
    try:
        with (
            patch.object(
                internal_router,
                "AgentManagementService",
                return_value=mock_agent_svc,
            ),
            patch.object(
                internal_router,
                "_provider_secrets_context",
            ) as mock_provider_context,
            patch.object(
                internal_router,
                "runtime_run_agent",
                AsyncMock(return_value=agent_output),
            ) as mock_runtime_run,
            patch.object(
                internal_router,
                "_run_mcp_agent_workflow",
                AsyncMock(
                    side_effect=AssertionError(
                        "_run_mcp_agent_workflow should not be called"
                    )
                ),
            ),
        ):
            mock_provider_context.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_provider_context.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await internal_router.run_agent_endpoint(
                role=agent_role,
                session=AsyncMock(),
                params=params,
            )

        assert result["output"] == {"ok": True}
        mock_runtime_run.assert_awaited_once()
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_run_agent_endpoint_routes_preset_mcp_runs_to_durable_workflow(
    agent_role: Role,
    agent_output: AgentOutput,
) -> None:
    params = InternalRunAgentRequest(
        user_prompt="Open the relevant issue",
        preset_slug="jira-analyst",
    )
    mock_agent_svc = Mock()
    mock_agent_svc.presets = SimpleNamespace(
        resolve_agent_preset_config=AsyncMock(
            return_value=AgentConfig(
                model_name="claude-3-5-sonnet-20241022",
                model_provider="anthropic",
                mcp_servers=[
                    {
                        "type": "http",
                        "name": "jira",
                        "url": "https://mcp.example.com/v1/mcp",
                    }
                ],
            )
        )
    )

    token = ctx_role.set(agent_role)
    try:
        with (
            patch.object(
                internal_router,
                "AgentManagementService",
                return_value=mock_agent_svc,
            ),
            patch.object(
                internal_router,
                "_provider_secrets_context",
            ) as mock_provider_context,
            patch.object(
                internal_router,
                "_run_mcp_agent_workflow",
                AsyncMock(return_value=agent_output),
            ) as mock_workflow_run,
            patch.object(
                internal_router,
                "runtime_run_agent",
                AsyncMock(
                    side_effect=AssertionError("runtime_run_agent should not be called")
                ),
            ),
        ):
            mock_provider_context.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_provider_context.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await internal_router.run_agent_endpoint(
                role=agent_role,
                session=AsyncMock(),
                params=params,
            )

        assert result["output"] == {"ok": True}
        mock_agent_svc.presets.resolve_agent_preset_config.assert_awaited_once_with(
            slug="jira-analyst",
            preset_version=None,
        )
        mock_workflow_run.assert_awaited_once()
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_run_mcp_agent_workflow_executes_durable_workflow(
    agent_role: Role,
) -> None:
    session_id = uuid.uuid4()
    workflow_result = AgentOutput(
        output={"ok": True},
        message_history=[],
        duration=2.5,
        usage=RunUsage(requests=2, input_tokens=12, output_tokens=34),
        session_id=session_id,
    )
    config = AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        mcp_servers=[
            {"type": "http", "name": "jira", "url": "https://mcp.example.com/v1/mcp"}
        ],
    )
    mock_client = Mock()
    mock_client.execute_workflow = AsyncMock(return_value=workflow_result)

    with patch.object(
        internal_router, "get_temporal_client", AsyncMock(return_value=mock_client)
    ):
        result = await internal_router._run_mcp_agent_workflow(
            role=agent_role,
            session_id=session_id,
            user_prompt="Use Jira MCP",
            config=config,
            max_requests=12,
            max_tool_calls=6,
        )

    assert result == workflow_result
    mock_client.execute_workflow.assert_awaited_once()
    _workflow_fn, workflow_args = mock_client.execute_workflow.await_args.args[:2]
    assert workflow_args.role == agent_role
    assert workflow_args.agent_args.user_prompt == "Use Jira MCP"
    assert workflow_args.agent_args.session_id == session_id
    assert workflow_args.agent_args.max_requests == 12
    assert workflow_args.agent_args.max_tool_calls == 6
    assert workflow_args.entity_type == internal_router.AgentSessionEntity.WORKFLOW


@pytest.mark.anyio
async def test_run_mcp_agent_workflow_rejects_tool_approvals(
    agent_role: Role,
) -> None:
    config = AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        mcp_servers=[
            {"type": "http", "name": "jira", "url": "https://mcp.example.com/v1/mcp"}
        ],
        tool_approvals={"core.http_request": True},
    )

    with pytest.raises(ValueError, match="Tool approvals are not supported"):
        await internal_router._run_mcp_agent_workflow(
            role=agent_role,
            session_id=uuid.uuid4(),
            user_prompt="Use Jira MCP",
            config=config,
            max_requests=12,
            max_tool_calls=6,
        )
