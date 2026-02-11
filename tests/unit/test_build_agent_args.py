"""Tests for build_agent_args_activity and build_preset_agent_args_activity.

Verifies that VARS expressions are resolved in agent action args,
mirroring the enrichment done by executor/service.py for regular actions.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from tracecat_ee.agent.schemas import AgentActionArgs, PresetAgentActionArgs

from tracecat.auth.types import Role
from tracecat.dsl.action import (
    BuildAgentArgsActivityInput,
    BuildPresetAgentArgsActivityInput,
    DSLActivities,
)
from tracecat.dsl.schemas import ExecutionContext, TaskResult
from tracecat.storage.object import InlineObject


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-service",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def _inline(data: object) -> InlineObject:
    """Wrap data in an InlineObject for use in ExecutionContext."""
    return InlineObject(data=data, typename=type(data).__name__)


def _make_context(
    actions: dict[str, TaskResult] | None = None,
) -> ExecutionContext:
    """Build a minimal ExecutionContext for testing."""
    return ExecutionContext(
        ACTIONS=actions or {},
        TRIGGER=_inline({}),
    )


class TestBuildAgentArgsActivity:
    """Tests for DSLActivities.build_agent_args_activity."""

    @pytest.mark.anyio
    async def test_resolves_vars_in_model_name(self, role: Role):
        """VARS expressions like ${{ VARS.models.claude }} should resolve."""
        args = {
            "user_prompt": "Hello",
            "model_name": "${{ VARS.models.claude }}",
            "model_provider": "anthropic",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
            return_value={"models": {"claude": "claude-sonnet-4-5-20250929"}},
        ):
            result = await DSLActivities.build_agent_args_activity(input)

        assert isinstance(result, AgentActionArgs)
        assert result.model_name == "claude-sonnet-4-5-20250929"
        assert result.model_provider == "anthropic"
        assert result.user_prompt == "Hello"

    @pytest.mark.anyio
    async def test_resolves_multiple_vars(self, role: Role):
        """Multiple VARS references across different fields should all resolve."""
        args = {
            "user_prompt": "${{ VARS.prompts.default }}",
            "model_name": "${{ VARS.models.claude }}",
            "model_provider": "${{ VARS.providers.default }}",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
            return_value={
                "models": {"claude": "claude-sonnet-4-5-20250929"},
                "providers": {"default": "anthropic"},
                "prompts": {"default": "You are a helpful assistant"},
            },
        ):
            result = await DSLActivities.build_agent_args_activity(input)

        assert result.model_name == "claude-sonnet-4-5-20250929"
        assert result.model_provider == "anthropic"
        assert result.user_prompt == "You are a helpful assistant"

    @pytest.mark.anyio
    async def test_no_vars_skips_resolution(self, role: Role):
        """When no VARS expressions are present, get_workspace_variables is not called."""
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
        ) as mock_get_vars:
            result = await DSLActivities.build_agent_args_activity(input)

        mock_get_vars.assert_not_called()
        assert result.model_name == "claude-sonnet-4-5-20250929"

    @pytest.mark.anyio
    async def test_vars_with_action_context(self, role: Role):
        """VARS should work alongside ACTIONS context references."""
        args = {
            "user_prompt": "${{ ACTIONS.reshape.result }}",
            "model_name": "${{ VARS.models.claude }}",
            "model_provider": "anthropic",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(
                actions={
                    "reshape": TaskResult(
                        result=_inline("What is 2+2?"),
                        result_typename="str",
                    ),
                },
            ),
            role=role,
            environment="production",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
            return_value={"models": {"claude": "claude-sonnet-4-5-20250929"}},
        ):
            result = await DSLActivities.build_agent_args_activity(input)

        assert result.user_prompt == "What is 2+2?"
        assert result.model_name == "claude-sonnet-4-5-20250929"

    @pytest.mark.anyio
    async def test_passes_environment_to_get_workspace_variables(self, role: Role):
        """The environment should be forwarded to get_workspace_variables."""
        args = {
            "user_prompt": "Hello",
            "model_name": "${{ VARS.models.claude }}",
            "model_provider": "anthropic",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            environment="staging",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
            return_value={"models": {"claude": "claude-sonnet-4-5-20250929"}},
        ) as mock_get_vars:
            await DSLActivities.build_agent_args_activity(input)

        mock_get_vars.assert_called_once_with(
            variable_exprs={"models"},
            environment="staging",
            role=role,
        )


class TestBuildPresetAgentArgsActivity:
    """Tests for DSLActivities.build_preset_agent_args_activity."""

    @pytest.mark.anyio
    async def test_resolves_vars_in_preset_args(self, role: Role):
        """VARS expressions should resolve in preset agent args."""
        args = {
            "preset": "my-preset",
            "user_prompt": "${{ VARS.prompts.default }}",
        }
        input = BuildPresetAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
            return_value={"prompts": {"default": "Analyze this alert"}},
        ):
            result = await DSLActivities.build_preset_agent_args_activity(input)

        assert isinstance(result, PresetAgentActionArgs)
        assert result.preset == "my-preset"
        assert result.user_prompt == "Analyze this alert"

    @pytest.mark.anyio
    async def test_no_vars_skips_resolution(self, role: Role):
        """When no VARS are present, get_workspace_variables is not called."""
        args = {
            "preset": "my-preset",
            "user_prompt": "Hello",
        }
        input = BuildPresetAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
        ) as mock_get_vars:
            result = await DSLActivities.build_preset_agent_args_activity(input)

        mock_get_vars.assert_not_called()
        assert result.user_prompt == "Hello"
