"""Tests for build_agent_args_activity and build_preset_agent_args_activity.

Verifies that:
- Build activities correctly evaluate templated args against materialized context.
- VARS are resolved inline within each build activity (fetched from DB, scoped
  to the given environment).

Note: These tests mock get_workspace_variables to isolate expression
evaluation logic from the database layer, consistent with the pattern
in test_executor_manifest_resolution.py.

Both build activities are async and can be called directly from async tests.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from tracecat_ee.agent.schemas import AgentActionArgs, PresetAgentActionArgs

from tracecat.agent.common.types import MCPHttpServerConfig, MCPStdioServerConfig
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
        """VARS expressions like ${{ VARS.models.claude }} should resolve
        via inline workspace variable fetch."""
        args = {
            "user_prompt": "Hello",
            "model_name": "${{ VARS.models.claude }}",
            "model_provider": "anthropic",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
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
            task_environment=None,
            default_environment="default",
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
    async def test_no_vars_works(self, role: Role):
        """When no VARS expressions are present, static values pass through."""
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
            "base_url": "https://llm.example.com/v1",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
        ) as mock_get_vars:
            result = await DSLActivities.build_agent_args_activity(input)

        mock_get_vars.assert_not_called()
        assert result.model_name == "claude-sonnet-4-5-20250929"
        assert result.base_url == "https://llm.example.com/v1"

    @pytest.mark.anyio
    async def test_model_selection_overrides_deprecated_model_fields(self, role: Role):
        """When both model shapes are present, the new model selection wins."""
        catalog_id = uuid.uuid4()
        args = {
            "user_prompt": "Hello",
            "model": {
                "model_name": "claude-sonnet-4-5-20250929",
                "model_provider": "anthropic",
                "catalog_id": str(catalog_id),
            },
            "model_name": "gpt-4o-mini",
            "model_provider": "openai",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        result = await DSLActivities.build_agent_args_activity(input)

        assert result.model_name == "claude-sonnet-4-5-20250929"
        assert result.model_provider == "anthropic"
        assert result.catalog_id == catalog_id

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
            task_environment=None,
            default_environment="default",
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
    async def test_strips_whitespace_around_expressions(self, role: Role):
        """Leading/trailing whitespace on arg values should be stripped so
        expressions like '  ${{ VARS.x }}  ' are treated as template-only."""
        args = {
            "user_prompt": "Hello",
            "model_name": "  ${{ VARS.models.claude }}  ",
            "model_provider": "  anthropic  ",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
            return_value={"models": {"claude": "claude-sonnet-4-5-20250929"}},
        ):
            result = await DSLActivities.build_agent_args_activity(input)

        assert result.model_name == "claude-sonnet-4-5-20250929"
        assert result.model_provider == "anthropic"

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
            task_environment=None,
            default_environment="staging",
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

    @pytest.mark.anyio
    async def test_no_vars_skips_db_call(self, role: Role):
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
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
        ) as mock_get_vars:
            await DSLActivities.build_agent_args_activity(input)

        mock_get_vars.assert_not_called()

    @pytest.mark.anyio
    async def test_preserves_enable_thinking_flag(self, role: Role):
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
            "enable_thinking": False,
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        result = await DSLActivities.build_agent_args_activity(input)

        assert result.enable_thinking is False

    @pytest.mark.anyio
    async def test_preserves_explicit_reasoning_effort_in_model_settings(
        self, role: Role
    ):
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
            "enable_thinking": True,
            "model_settings": {"reasoning_effort": "medium"},
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        result = await DSLActivities.build_agent_args_activity(input)

        assert result.model_settings == {"reasoning_effort": "medium"}


class TestBuildAgentArgsMcpResolution:
    """Tests for mcp_integration_ids → mcp_servers resolution inside build_agent_args_activity."""

    @pytest.mark.anyio
    async def test_mcp_integration_ids_resolve_to_mcp_servers(self, role: Role):
        """mcp_integration_ids in args are resolved to partial MCPServerConfigs
        and surfaced as mcp_servers on the result; the raw IDs are not present."""
        integration_id = str(uuid.uuid4())
        resolved: MCPHttpServerConfig = {
            "type": "http",
            "name": "my-server",
            "url": "https://mcp.example.com",
            "id": integration_id,
        }
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
            "mcp_integrations": [integration_id],
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action._resolve_mcp_integrations",
            new_callable=AsyncMock,
            return_value=[resolved],
        ) as mock_resolve:
            result = await DSLActivities.build_agent_args_activity(input)

        mock_resolve.assert_awaited_once_with([integration_id], role=role)
        assert result.mcp_servers == [resolved]

    @pytest.mark.anyio
    async def test_mcp_integration_ids_not_present_on_result(self, role: Role):
        """mcp_integration_ids must not appear as a field on AgentActionArgs."""
        integration_id = str(uuid.uuid4())
        resolved: MCPHttpServerConfig = {
            "type": "http",
            "name": "my-server",
            "url": "https://mcp.example.com",
            "id": integration_id,
        }
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
            "mcp_integrations": [integration_id],
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action._resolve_mcp_integrations",
            new_callable=AsyncMock,
            return_value=[resolved],
        ):
            result = await DSLActivities.build_agent_args_activity(input)

        assert not hasattr(result, "mcp_integration_ids")

    @pytest.mark.anyio
    async def test_no_mcp_integration_ids_skips_resolution(self, role: Role):
        """When mcp_integration_ids is absent, resolution is never called and
        mcp_servers is None."""
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action._resolve_mcp_integrations",
            new_callable=AsyncMock,
        ) as mock_resolve:
            result = await DSLActivities.build_agent_args_activity(input)

        mock_resolve.assert_not_called()
        assert result.mcp_servers is None

    @pytest.mark.anyio
    async def test_multiple_mcp_integration_ids_resolve(self, role: Role):
        """All IDs in the list are forwarded to the resolver as a batch."""
        id1, id2 = str(uuid.uuid4()), str(uuid.uuid4())
        resolved: list[MCPHttpServerConfig] = [
            {
                "type": "http",
                "name": "server-a",
                "url": "https://a.example.com",
                "id": id1,
            },
            {
                "type": "http",
                "name": "server-b",
                "url": "https://b.example.com",
                "id": id2,
            },
        ]
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
            "mcp_integrations": [id1, id2],
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action._resolve_mcp_integrations",
            new_callable=AsyncMock,
            return_value=resolved,
        ) as mock_resolve:
            result = await DSLActivities.build_agent_args_activity(input)

        mock_resolve.assert_awaited_once_with([id1, id2], role=role)
        assert result.mcp_servers == resolved
        assert len(result.mcp_servers) == 2  # type: ignore[arg-type]

    @pytest.mark.anyio
    async def test_stdio_mcp_integration_resolves(self, role: Role):
        """Stdio server configs are resolved the same way as HTTP ones."""
        integration_id = str(uuid.uuid4())
        resolved: MCPStdioServerConfig = {
            "type": "stdio",
            "name": "local-tools",
            "command": "python",
            "args": ["-m", "my_mcp_server"],
            "id": integration_id,
        }
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
            "mcp_integrations": [integration_id],
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action._resolve_mcp_integrations",
            new_callable=AsyncMock,
            return_value=[resolved],
        ):
            result = await DSLActivities.build_agent_args_activity(input)

        assert result.mcp_servers == [resolved]
        assert result.mcp_servers[0]["type"] == "stdio"  # type: ignore[index]

    @pytest.mark.anyio
    async def test_resolved_configs_carry_no_secrets(self, role: Role):
        """Resolved HTTP configs must not contain headers (secrets are omitted
        on the partial config that crosses Temporal boundaries)."""
        integration_id = str(uuid.uuid4())
        resolved: MCPHttpServerConfig = {
            "type": "http",
            "name": "secure-server",
            "url": "https://secure.example.com",
            "id": integration_id,
            # no 'headers' key — intentionally absent
        }
        args = {
            "user_prompt": "Hello",
            "model_name": "claude-sonnet-4-5-20250929",
            "model_provider": "anthropic",
            "mcp_integrations": [integration_id],
        }
        input = BuildAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action._resolve_mcp_integrations",
            new_callable=AsyncMock,
            return_value=[resolved],
        ):
            result = await DSLActivities.build_agent_args_activity(input)

        assert result.mcp_servers is not None
        assert "headers" not in result.mcp_servers[0]


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
            task_environment=None,
            default_environment="default",
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
    async def test_no_vars_works(self, role: Role):
        """When no VARS are present, static values pass through."""
        args = {
            "preset": "my-preset",
            "user_prompt": "Hello",
        }
        input = BuildPresetAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="default",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
        ) as mock_get_vars:
            result = await DSLActivities.build_preset_agent_args_activity(input)

        mock_get_vars.assert_not_called()
        assert result.user_prompt == "Hello"

    @pytest.mark.anyio
    async def test_passes_environment_to_get_workspace_variables(self, role: Role):
        """The environment should be forwarded to get_workspace_variables."""
        args = {
            "preset": "my-preset",
            "user_prompt": "${{ VARS.prompts.default }}",
        }
        input = BuildPresetAgentArgsActivityInput(
            args=args,
            operand=_make_context(),
            role=role,
            task_environment=None,
            default_environment="staging",
        )

        with patch(
            "tracecat.dsl.action.get_workspace_variables",
            new_callable=AsyncMock,
            return_value={"prompts": {"default": "Analyze this alert"}},
        ) as mock_get_vars:
            await DSLActivities.build_preset_agent_args_activity(input)

        mock_get_vars.assert_called_once_with(
            variable_exprs={"prompts"},
            environment="staging",
            role=role,
        )
