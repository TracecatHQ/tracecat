from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
from tracecat.contexts import ctx_run
from tracecat.dsl import action as dsl_action
from tracecat.dsl.action import BuildAgentArgsActivityInput, DSLActivities
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import RunContext
from tracecat.exceptions import ScopeDeniedError
from tracecat.identifiers.workflow import WorkflowUUID
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
                "environment": "default",
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
@pytest.mark.parametrize(
    "action",
    [
        "ai.agent",
        "ai.action",
        "ai.preset_agent",
        "ai.slackbot",
    ],
)
async def test_agent_interface_evaluation_enforces_exact_action_scope(
    action: str,
) -> None:
    input = BuildAgentArgsActivityInput(
        action=action,
        args={},
        operand=create_default_execution_context(),
        role=Role(
            type="user",
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
            scopes=frozenset({"action:some.other.action:execute"}),
        ),
        task_environment=None,
        default_environment="default",
    )

    with pytest.raises(ScopeDeniedError):
        await dsl_action._evaluate_agent_args(input)

    input = input.model_copy(
        update={
            "role": input.role.model_copy(
                update={"scopes": frozenset({f"action:{action}:execute"})}
            )
        }
    )
    assert await dsl_action._evaluate_agent_args(input) == {"environment": "default"}


@pytest.mark.anyio
async def test_agent_interface_scope_denial_is_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        await DSLActivities.build_agent_args_activity(
            BuildAgentArgsActivityInput(
                action="ai.agent",
                args={"user_prompt": "hi"},
                operand=create_default_execution_context(),
                role=Role(
                    type="user",
                    user_id=uuid.uuid4(),
                    service_id="tracecat-api",
                    scopes=frozenset(),
                ),
                task_environment=None,
                default_environment="default",
            )
        )

    assert exc_info.value.non_retryable


@pytest.mark.anyio
async def test_slackbot_secrets_use_resolved_run_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    wf_id = WorkflowUUID.from_legacy("wf-" + "0" * 32)
    run_context = RunContext(
        wf_id=wf_id,
        wf_exec_id="wf-" + "0" * 32 + ":exec-" + "0" * 32,
        wf_run_id=uuid.uuid4(),
        environment="staging",
        logical_time=datetime.now(UTC),
    )
    monkeypatch.setattr(
        dsl_action.registry_resolver,
        "prefetch_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        dsl_action.registry_resolver,
        "collect_action_secrets_from_manifest",
        AsyncMock(return_value=set()),
    )

    async def get_action_secrets(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        assert ctx_run.get() == run_context
        return {"slack": {"SLACK_BOT_TOKEN": "synthetic-token"}}

    monkeypatch.setattr(
        dsl_action.secrets_manager,
        "get_action_secrets",
        get_action_secrets,
    )
    previous_run_context = ctx_run.get()

    result = await dsl_action._slackbot_secret_context(
        role=role,
        registry_lock=RegistryLock(origins={}, actions={}),
        run_context=run_context,
    )

    assert result == {"SLACK_BOT_TOKEN": "synthetic-token"}
    assert ctx_run.get() is previous_run_context


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
                "environment": "default",
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
