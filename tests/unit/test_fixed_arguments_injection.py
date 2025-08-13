import pytest
from unittest.mock import AsyncMock, Mock, call

from tracecat_registry.integrations.agents.builder import build_agent_tools


@pytest.mark.anyio
async def test_build_agent_tools_injects_only_for_specified_actions(mocker):
    # Prepare two fake registry actions
    mock_ra1 = Mock()
    mock_ra1.namespace = "core.cases"
    mock_ra1.name = "create_case"

    mock_ra2 = Mock()
    mock_ra2.namespace = "tools.slack"
    mock_ra2.name = "post_message"

    # Mock the RegistryActionsService.with_session context manager
    mock_service = Mock()
    mock_service.get_actions = AsyncMock(return_value=[mock_ra1, mock_ra2])
    mock_service.list_actions = AsyncMock(return_value=[mock_ra1, mock_ra2])
    mock_service.fetch_all_action_secrets = AsyncMock(return_value=[])

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service
    mocker.patch(
        "tracecat_registry.integrations.agents.builder.RegistryActionsService.with_session",
        return_value=mock_ctx,
    )

    # Patch create_tool_from_registry to observe calls
    create_tool_stub = mocker.patch(
        "tracecat_registry.integrations.agents.builder.create_tool_from_registry",
        return_value=Mock(),
    )

    # Only define fixed args for one action
    fixed_arguments = {
        "core.cases.create_case": {"priority": "high"},
        # No fixed args for tools.slack.post_message
    }

    await build_agent_tools(fixed_arguments=fixed_arguments, action_filters=[
        "core.cases.create_case",
        "tools.slack.post_message",
    ])

    # Expect injection only for the action with overrides
    expected_calls = [
        call("core.cases.create_case", {"priority": "high"}),
        call("tools.slack.post_message"),
    ]
    create_tool_stub.assert_has_calls(expected_calls, any_order=True)


@pytest.mark.anyio
async def test_build_agent_tools_no_injection_when_no_overrides(mocker):
    # Prepare two fake registry actions
    mock_ra1 = Mock()
    mock_ra1.namespace = "core"
    mock_ra1.name = "http_request"

    mock_ra2 = Mock()
    mock_ra2.namespace = "tools.email"
    mock_ra2.name = "send_email"

    # Mock the RegistryActionsService.with_session context manager
    mock_service = Mock()
    mock_service.get_actions = AsyncMock(return_value=[mock_ra1, mock_ra2])
    mock_service.list_actions = AsyncMock(return_value=[mock_ra1, mock_ra2])
    mock_service.fetch_all_action_secrets = AsyncMock(return_value=[])

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service
    mocker.patch(
        "tracecat_registry.integrations.agents.builder.RegistryActionsService.with_session",
        return_value=mock_ctx,
    )

    # Patch create_tool_from_registry to observe calls
    create_tool_stub = mocker.patch(
        "tracecat_registry.integrations.agents.builder.create_tool_from_registry",
        return_value=Mock(),
    )

    # No overrides provided
    await build_agent_tools(fixed_arguments={}, action_filters=[
        "core.http_request",
        "tools.email.send_email",
    ])

    # Expect calls without fixed args
    expected_calls = [
        call("core.http_request"),
        call("tools.email.send_email"),
    ]
    create_tool_stub.assert_has_calls(expected_calls, any_order=True)