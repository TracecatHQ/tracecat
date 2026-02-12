"""Tests for build_agent_args_activity, build_preset_agent_args_activity, and
resolve_agent_vars_activity.

Verifies that:
- Build activities correctly evaluate templated args against materialized context.
- resolve_agent_vars_activity fetches workspace VARS from the DB scoped to an
  environment, mirroring the enrichment done by executor/service.py for regular actions.

Note: These tests mock get_workspace_variables to isolate expression
evaluation logic from the database layer, consistent with the pattern
in test_executor_manifest_resolution.py.

The build activities are sync functions that internally use run_sync()
(which creates its own event loop). Since tests run inside an async
event loop, we dispatch via asyncio.to_thread — the same mechanism
Temporal uses for sync activity execution.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from tracecat_ee.agent.schemas import AgentActionArgs, PresetAgentActionArgs

from tracecat.auth.types import Role
from tracecat.dsl.action import (
    BuildAgentArgsActivityInput,
    BuildPresetAgentArgsActivityInput,
    DSLActivities,
    ResolveAgentVarsActivityInput,
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
    vars: dict[str, object] | None = None,
) -> ExecutionContext:
    """Build a minimal ExecutionContext for testing."""
    ctx = ExecutionContext(
        ACTIONS=actions or {},
        TRIGGER=_inline({}),
    )
    if vars is not None:
        ctx["VARS"] = vars
    return ctx


class TestBuildAgentArgsActivity:
    """Tests for DSLActivities.build_agent_args_activity."""

    @pytest.mark.anyio
    async def test_resolves_vars_in_model_name(self):
        """VARS expressions like ${{ VARS.models.claude }} should resolve
        when VARS are pre-injected into the operand."""
        args = {
            "user_prompt": "Hello",
            "model_name": "${{ VARS.models.claude }}",
            "model_provider": "anthropic",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(
                vars={"models": {"claude": "claude-sonnet-4-5-20250929"}},
            ),
        )

        result = await asyncio.to_thread(DSLActivities.build_agent_args_activity, input)

        assert isinstance(result, AgentActionArgs)
        assert result.model_name == "claude-sonnet-4-5-20250929"
        assert result.model_provider == "anthropic"
        assert result.user_prompt == "Hello"

    @pytest.mark.anyio
    async def test_resolves_multiple_vars(self):
        """Multiple VARS references across different fields should all resolve."""
        args = {
            "user_prompt": "${{ VARS.prompts.default }}",
            "model_name": "${{ VARS.models.claude }}",
            "model_provider": "${{ VARS.providers.default }}",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(
                vars={
                    "models": {"claude": "claude-sonnet-4-5-20250929"},
                    "providers": {"default": "anthropic"},
                    "prompts": {"default": "You are a helpful assistant"},
                },
            ),
        )

        result = await asyncio.to_thread(DSLActivities.build_agent_args_activity, input)

        assert result.model_name == "claude-sonnet-4-5-20250929"
        assert result.model_provider == "anthropic"
        assert result.user_prompt == "You are a helpful assistant"

    @pytest.mark.anyio
    async def test_no_vars_works(self):
        """When no VARS expressions are present, static values pass through."""
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
        )

        result = await asyncio.to_thread(DSLActivities.build_agent_args_activity, input)

        assert result.model_name == "claude-sonnet-4-5-20250929"

    @pytest.mark.anyio
    async def test_vars_with_action_context(self):
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
                vars={"models": {"claude": "claude-sonnet-4-5-20250929"}},
            ),
        )

        result = await asyncio.to_thread(DSLActivities.build_agent_args_activity, input)

        assert result.user_prompt == "What is 2+2?"
        assert result.model_name == "claude-sonnet-4-5-20250929"

    @pytest.mark.anyio
    async def test_strips_whitespace_around_expressions(self):
        """Leading/trailing whitespace on arg values should be stripped so
        expressions like '  ${{ VARS.x }}  ' are treated as template-only."""
        args = {
            "user_prompt": "Hello",
            "model_name": "  ${{ VARS.models.claude }}  ",
            "model_provider": "  anthropic  ",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(
                vars={"models": {"claude": "claude-sonnet-4-5-20250929"}},
            ),
        )

        result = await asyncio.to_thread(DSLActivities.build_agent_args_activity, input)

        assert result.model_name == "claude-sonnet-4-5-20250929"
        assert result.model_provider == "anthropic"


class TestBuildPresetAgentArgsActivity:
    """Tests for DSLActivities.build_preset_agent_args_activity."""

    @pytest.mark.anyio
    async def test_resolves_vars_in_preset_args(self):
        """VARS expressions should resolve in preset agent args."""
        args = {
            "preset": "my-preset",
            "user_prompt": "${{ VARS.prompts.default }}",
        }
        input = BuildPresetAgentArgsActivityInput(
            args=args,
            operand=_make_context(
                vars={"prompts": {"default": "Analyze this alert"}},
            ),
        )

        result = await asyncio.to_thread(
            DSLActivities.build_preset_agent_args_activity, input
        )

        assert isinstance(result, PresetAgentActionArgs)
        assert result.preset == "my-preset"
        assert result.user_prompt == "Analyze this alert"

    @pytest.mark.anyio
    async def test_no_vars_works(self):
        """When no VARS are present, static values pass through."""
        args = {
            "preset": "my-preset",
            "user_prompt": "Hello",
        }
        input = BuildPresetAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
        )

        result = await asyncio.to_thread(
            DSLActivities.build_preset_agent_args_activity, input
        )

        assert result.user_prompt == "Hello"


class TestResolveAgentVarsActivity:
    """Tests for DSLActivities.resolve_agent_vars_activity."""

    @pytest.mark.anyio
    async def test_fetches_vars(self, role: Role):
        """Should fetch workspace variables scoped to the given environment."""
        args = {
            "user_prompt": "Hello",
            "model_name": "${{ VARS.models.claude }}",
            "model_provider": "anthropic",
        }
        input = ResolveAgentVarsActivityInput(
            args=args,
            role=role,
            environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
            return_value={"models": {"claude": "claude-sonnet-4-5-20250929"}},
        ):
            result = await DSLActivities.resolve_agent_vars_activity(input)

        assert result == {"models": {"claude": "claude-sonnet-4-5-20250929"}}

    @pytest.mark.anyio
    async def test_passes_environment_to_get_workspace_variables(self, role: Role):
        """The environment should be forwarded to get_workspace_variables."""
        args = {
            "model_name": "${{ VARS.models.claude }}",
        }
        input = ResolveAgentVarsActivityInput(
            args=args,
            role=role,
            environment="staging",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
            return_value={"models": {"claude": "claude-sonnet-4-5-20250929"}},
        ) as mock_get_vars:
            await DSLActivities.resolve_agent_vars_activity(input)

        mock_get_vars.assert_called_once_with(
            variable_exprs={"models"},
            environment="staging",
            role=role,
        )

    @pytest.mark.anyio
    async def test_no_vars_skips_db_call(self, role: Role):
        """When no VARS expressions are present, get_workspace_variables is not called."""
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
        }
        input = ResolveAgentVarsActivityInput(
            args=args,
            role=role,
            environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
        ) as mock_get_vars:
            result = await DSLActivities.resolve_agent_vars_activity(input)

        mock_get_vars.assert_not_called()
        assert result == {}
