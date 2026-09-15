from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError
from tracecat_registry import ActionIsInterfaceError
from tracecat_registry.integrations.agents import slack
from tracecat_registry.integrations.agents.slack import (
    PreparedSlackbotPrompt,
    SlackbotContext,
)

from tracecat.auth.types import Role
from tracecat.dsl import action as dsl_action
from tracecat.dsl.action import BuildAgentArgsActivityInput, DSLActivities
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.enums import PlatformAction
from tracecat.registry.lock.types import RegistryLock


@pytest.mark.parametrize(
    "action",
    [
        "tools.github.mcp",
        "tools.jira.mcp",
        "tools.linear.mcp",
        "tools.notion.mcp",
        "tools.runreveal.mcp",
        "tools.sentry.mcp",
        "tools.wiz.mcp",
    ],
)
def test_removed_mcp_actions_are_not_platform_interfaces(action: str) -> None:
    assert not PlatformAction.is_agent(action)
    assert not PlatformAction.is_interface(action)
    assert not PlatformAction.is_streamable(action)


def test_slackbot_is_platform_interface() -> None:
    assert PlatformAction.is_agent("ai.slackbot")
    assert PlatformAction.is_interface("ai.slackbot")
    assert PlatformAction.is_streamable("ai.slackbot")


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
        return_value=PreparedSlackbotPrompt(
            user_prompt="Prepared Slack prompt",
            instructions="Prepared Slack instructions",
            actions=["tools.slack.post_message"],
            context=SlackbotContext(channel_id="C01234567"),
        )
    )
    monkeypatch.setattr(dsl_action, "prepare_slackbot", prepare)

    result = await DSLActivities.prepare_slackbot_activity(
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

    assert result.args.user_prompt == "Prepared Slack prompt"
    assert result.args.instructions == "Prepared Slack instructions"
    assert result.args.actions == ["tools.slack.post_message"]
    assert result.context == SlackbotContext(channel_id="C01234567")
    prepare.assert_awaited_once()


@pytest.mark.anyio
async def test_slackbot_prepare_failure_after_ack_clears_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dsl_action,
        "_evaluate_agent_args",
        AsyncMock(
            return_value={
                "event": None,
                "prompt": "p",
                "instructions": None,
                "channel_id": "C01234567",
                "model": {"model_name": "m", "model_provider": "anthropic"},
                "actions": None,
                "model_settings": None,
                "retries": 6,
                "limit_messages": 5,
            }
        ),
    )
    monkeypatch.setattr(
        dsl_action, "_slackbot_secret_context", AsyncMock(return_value={})
    )
    context = SlackbotContext(channel_id="C01234567", ts="1.2")
    monkeypatch.setattr(
        dsl_action,
        "prepare_slackbot",
        AsyncMock(
            return_value=PreparedSlackbotPrompt(
                user_prompt="p", instructions="i", actions=[], context=context
            )
        ),
    )
    monkeypatch.setattr(
        dsl_action, "_apply_mcp_servers", AsyncMock(side_effect=RuntimeError("boom"))
    )
    finalize = AsyncMock()
    monkeypatch.setattr(dsl_action, "finalize_slackbot", finalize)

    with pytest.raises(ApplicationError):
        await DSLActivities.prepare_slackbot_activity(
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

    finalize.assert_awaited_once_with(context, succeeded=False)


@pytest.mark.anyio
async def test_build_agent_args_activity_does_not_prepare_slackbot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic agent path must contain no Slack preparation."""
    monkeypatch.setattr(
        dsl_action,
        "_evaluate_agent_args",
        AsyncMock(
            return_value={
                "user_prompt": "Investigate this issue",
                "model": {
                    "model_name": "claude-sonnet-4-5",
                    "model_provider": "anthropic",
                },
            }
        ),
    )
    prepare = AsyncMock()
    monkeypatch.setattr(dsl_action, "prepare_slackbot", prepare)

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

    assert result.user_prompt == "Investigate this issue"
    prepare.assert_not_awaited()
