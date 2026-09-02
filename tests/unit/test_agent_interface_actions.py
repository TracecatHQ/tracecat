from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
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
from tracecat.integrations.enums import OAuthGrantType
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
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
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


def _mcp_integration(
    *,
    integration_id: uuid.UUID,
    grant_type: OAuthGrantType | None = None,
    user_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    """Build a github_mcp row; grant_type None means a non-OAuth integration."""
    oauth = (
        None
        if grant_type is None
        else SimpleNamespace(
            provider_id="github_mcp",
            user_id=user_id,
            grant_type=grant_type,
        )
    )
    return SimpleNamespace(
        id=integration_id,
        slug="github_mcp",
        oauth_integration=oauth,
    )


def _patch_mcp_resolution(
    monkeypatch: pytest.MonkeyPatch,
    integrations: list[SimpleNamespace],
    server: dict[str, Any],
) -> SimpleNamespace:
    preset_service = SimpleNamespace(
        session=object(),
        resolve_mcp_integration_refs=AsyncMock(return_value=[server]),
    )

    @asynccontextmanager
    async def with_session(*, role: Role):
        del role
        yield preset_service

    integrations_service = SimpleNamespace(
        list_mcp_integrations=AsyncMock(return_value=integrations)
    )
    monkeypatch.setattr(dsl_action.AgentPresetService, "with_session", with_session)
    monkeypatch.setattr(
        dsl_action,
        "IntegrationService",
        lambda *_args, **_kwargs: integrations_service,
    )
    return preset_service


_SERVER: dict[str, Any] = {
    "type": "http",
    "name": "github",
    "url": "https://example.test/mcp",
    "id": "00000000-0000-0000-0000-000000000009",
}


def _user_role(user_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=user_id,
    )


def _service_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=None,
    )


@pytest.mark.anyio
async def test_mcp_agent_action_prefers_own_auth_code_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller_id = uuid.uuid4()
    own_id = uuid.uuid4()
    preset_service = _patch_mcp_resolution(
        monkeypatch,
        [
            _mcp_integration(
                integration_id=uuid.uuid4(),
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                user_id=uuid.uuid4(),
            ),
            _mcp_integration(
                integration_id=own_id,
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                user_id=caller_id,
            ),
        ],
        _SERVER,
    )

    result = await dsl_action._resolve_mcp_agent_action(
        "tools.github.mcp", role=_user_role(caller_id)
    )

    assert result == [_SERVER]
    preset_service.resolve_mcp_integration_refs.assert_awaited_once_with([str(own_id)])


@pytest.mark.anyio
async def test_mcp_agent_action_rejects_other_users_auth_code_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mcp_resolution(
        monkeypatch,
        [
            _mcp_integration(
                integration_id=uuid.uuid4(),
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                user_id=uuid.uuid4(),
            )
        ],
        _SERVER,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await dsl_action._resolve_mcp_agent_action(
            "tools.github.mcp", role=_user_role(uuid.uuid4())
        )

    assert exc_info.value.type == "MCPIntegrationNotAuthorizedError"
    assert exc_info.value.non_retryable


@pytest.mark.anyio
async def test_mcp_agent_action_rejects_auth_code_row_for_service_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mcp_resolution(
        monkeypatch,
        [
            _mcp_integration(
                integration_id=uuid.uuid4(),
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                user_id=uuid.uuid4(),
            )
        ],
        _SERVER,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await dsl_action._resolve_mcp_agent_action(
            "tools.github.mcp", role=_service_role()
        )

    assert exc_info.value.type == "MCPIntegrationNotAuthorizedError"


@pytest.mark.anyio
async def test_mcp_agent_action_allows_client_credentials_for_service_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_id = uuid.uuid4()
    preset_service = _patch_mcp_resolution(
        monkeypatch,
        [
            _mcp_integration(
                integration_id=shared_id,
                grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
            )
        ],
        _SERVER,
    )

    result = await dsl_action._resolve_mcp_agent_action(
        "tools.github.mcp", role=_service_role()
    )

    assert result == [_SERVER]
    preset_service.resolve_mcp_integration_refs.assert_awaited_once_with(
        [str(shared_id)]
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "grant_type",
    [OAuthGrantType.CLIENT_CREDENTIALS, None],
    ids=["client_creds", "none"],
)
async def test_mcp_agent_action_falls_back_to_shared_row_for_user(
    monkeypatch: pytest.MonkeyPatch, grant_type: OAuthGrantType | None
) -> None:
    shared_id = uuid.uuid4()
    preset_service = _patch_mcp_resolution(
        monkeypatch,
        [
            _mcp_integration(
                integration_id=uuid.uuid4(),
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                user_id=uuid.uuid4(),
            ),
            _mcp_integration(integration_id=shared_id, grant_type=grant_type),
        ],
        _SERVER,
    )

    result = await dsl_action._resolve_mcp_agent_action(
        "tools.github.mcp", role=_user_role(uuid.uuid4())
    )

    assert result == [_SERVER]
    preset_service.resolve_mcp_integration_refs.assert_awaited_once_with(
        [str(shared_id)]
    )


@pytest.mark.anyio
async def test_mcp_agent_action_ambiguous_shared_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mcp_resolution(
        monkeypatch,
        [
            _mcp_integration(
                integration_id=uuid.uuid4(),
                grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
            ),
            _mcp_integration(integration_id=uuid.uuid4(), grant_type=None),
        ],
        _SERVER,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await dsl_action._resolve_mcp_agent_action(
            "tools.github.mcp", role=_service_role()
        )

    assert exc_info.value.type == "MCPIntegrationAmbiguousError"


@pytest.mark.anyio
async def test_mcp_agent_action_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_mcp_resolution(monkeypatch, [], _SERVER)

    with pytest.raises(ApplicationError) as exc_info:
        await dsl_action._resolve_mcp_agent_action(
            "tools.github.mcp", role=_service_role()
        )

    assert exc_info.value.type == "MCPIntegrationNotFoundError"
