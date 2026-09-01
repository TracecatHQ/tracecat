from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from tracecat_registry import ActionIsInterfaceError
from tracecat_registry.integrations.agents import slack
from tracecat_registry.integrations.mcp import (
    github,
    jira,
    linear,
    notion,
    runreveal,
    sentry,
    wiz,
)

from tracecat.auth.types import Role
from tracecat.dsl import action as dsl_action
from tracecat.dsl.action import BuildAgentArgsActivityInput, DSLActivities
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.enums import MCP_AGENT_ACTION_PROVIDER_IDS, PlatformAction
from tracecat.registry.lock.types import RegistryLock


@pytest.mark.parametrize("action", MCP_AGENT_ACTION_PROVIDER_IDS)
def test_mcp_agent_actions_are_platform_interfaces(action: str) -> None:
    assert PlatformAction.is_agent(action)
    assert PlatformAction.is_interface(action)
    assert PlatformAction.is_streamable(action)


def test_slackbot_is_platform_interface() -> None:
    assert PlatformAction.is_agent("ai.slackbot")
    assert PlatformAction.is_interface("ai.slackbot")
    assert PlatformAction.is_streamable("ai.slackbot")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "interface",
    [
        github.mcp,
        jira.mcp,
        linear.mcp,
        notion.mcp,
        runreveal.mcp,
        sentry.mcp,
        wiz.mcp,
    ],
)
async def test_mcp_agent_implementations_cannot_run_in_executor(interface: Any) -> None:
    assert getattr(interface, "__tracecat_udf_kwargs")["deprecated"] is None
    with pytest.raises(ActionIsInterfaceError):
        await interface(
            user_prompt="Investigate this issue",
            instructions="Return a concise result",
            model=None,
            model_name="claude-sonnet-4-5",
            model_provider="anthropic",
        )


@pytest.mark.anyio
async def test_slackbot_implementation_cannot_run_in_executor() -> None:
    assert getattr(slack.slackbot, "__tracecat_udf_kwargs")["deprecated"] is None
    with pytest.raises(ActionIsInterfaceError):
        await slack.slackbot(
            event=None,
            prompt="Investigate this issue",
            instructions="Reply in the channel",
            channel_id="C01234567",
            model=cast(
                Any,
                {
                    "model_name": "claude-sonnet-4-5",
                    "model_provider": "anthropic",
                },
            ),
        )


@pytest.mark.anyio
async def test_mcp_agent_action_resolves_workspace_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    integration = SimpleNamespace(
        id=integration_id,
        slug="github_mcp",
        oauth_integration=SimpleNamespace(
            provider_id="github_mcp",
            user_id=role.user_id,
        ),
    )
    server = {
        "type": "http",
        "name": "github",
        "url": "https://example.test/mcp",
        "id": str(integration_id),
    }
    preset_service = SimpleNamespace(
        session=object(),
        resolve_mcp_integration_refs=AsyncMock(return_value=[server]),
    )

    @asynccontextmanager
    async def with_session(*, role: Role):
        del role
        yield preset_service

    integrations_service = SimpleNamespace(
        list_mcp_integrations=AsyncMock(return_value=[integration])
    )
    monkeypatch.setattr(
        dsl_action.AgentPresetService,
        "with_session",
        with_session,
    )
    monkeypatch.setattr(
        dsl_action,
        "IntegrationService",
        lambda *_args, **_kwargs: integrations_service,
    )

    result = await dsl_action._resolve_mcp_agent_action(
        "tools.github.mcp",
        role=role,
    )

    assert result == [server]
    preset_service.resolve_mcp_integration_refs.assert_awaited_once_with(
        [str(integration_id)]
    )


@pytest.mark.anyio
async def test_mcp_agent_interface_builds_direct_agent_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = {
        "type": "http",
        "name": "github",
        "url": "https://example.test/mcp",
        "id": "00000000-0000-0000-0000-000000000001",
    }
    monkeypatch.setattr(
        dsl_action,
        "_evaluate_agent_args",
        AsyncMock(
            return_value={
                "user_prompt": "Investigate this issue",
                "instructions": "Return a concise result",
                "model": {
                    "model_name": "claude-sonnet-4-5",
                    "model_provider": "anthropic",
                },
            }
        ),
    )
    resolve = AsyncMock(return_value=[server])
    monkeypatch.setattr(dsl_action, "_resolve_mcp_agent_action", resolve)

    result = await DSLActivities.build_agent_args_activity(
        BuildAgentArgsActivityInput(
            action="tools.github.mcp",
            args={},
            operand=create_default_execution_context(),
            role=Role(type="service", service_id="tracecat-api"),
            task_environment=None,
            default_environment="default",
        )
    )

    resolve.assert_awaited_once()
    assert result.mcp_servers == [server]
    assert result.user_prompt == "Investigate this issue"
    assert result.model_name == "claude-sonnet-4-5"
    assert result.model_provider == "anthropic"


@pytest.mark.anyio
async def test_slackbot_interface_prepares_direct_agent_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dsl_action,
        "_evaluate_agent_args",
        AsyncMock(
            return_value={
                "event": None,
                "prompt": "Investigate this issue",
                "instructions": "Reply in the channel",
                "channel_id": "C01234567",
                "model": {
                    "model_name": "claude-sonnet-4-5",
                    "model_provider": "anthropic",
                },
                "actions": None,
                "model_settings": None,
                "retries": 6,
                "limit_messages": 5,
            }
        ),
    )
    monkeypatch.setattr(
        dsl_action,
        "_slackbot_secret_context",
        AsyncMock(return_value={"SLACK_BOT_TOKEN": "synthetic-token"}),
    )
    prepare = AsyncMock(
        return_value={
            "user_prompt": "Prepared Slack prompt",
            "instructions": "Prepared Slack instructions",
            "actions": ["tools.slack.post_message"],
            "interface_context": {
                "channel_id": "C01234567",
                "thread_ts": None,
                "ts": None,
            },
        }
    )
    monkeypatch.setattr(slack, "prepare_slackbot", prepare)

    result = await DSLActivities.build_agent_args_activity(
        BuildAgentArgsActivityInput(
            action="ai.slackbot",
            args={},
            operand=create_default_execution_context(),
            role=Role(type="service", service_id="tracecat-api"),
            task_environment=None,
            default_environment="default",
            registry_lock=RegistryLock(origins={}, actions={}),
        )
    )

    assert result.user_prompt == "Prepared Slack prompt"
    assert result.instructions == "Prepared Slack instructions"
    assert result.actions == ["tools.slack.post_message"]
    assert result.interface_context == {
        "channel_id": "C01234567",
        "thread_ts": None,
        "ts": None,
    }
    prepare.assert_awaited_once()
