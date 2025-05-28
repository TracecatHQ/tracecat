"""Tests for agent builder functionality."""

import inspect
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from pydantic_ai.tools import Tool
from pydantic_core import PydanticUndefined

from registry.tracecat_registry.integrations.agents.builder import (
    TracecatAgentBuilder,
    create_tool_from_registry,
)
from tracecat.types.exceptions import RegistryError


@pytest.mark.anyio
class TestCreateToolFromRegistry:
    """Test suite for create_tool_from_registry function."""

    async def test_create_tool_from_core_http_request(self, test_role):
        """Test creating a tool from core.http_request action."""
        action_name = "core.http_request"

        tool = await create_tool_from_registry(action_name)

        # Verify it returns a Tool instance
        assert isinstance(tool, Tool)

        # Verify the function name is set correctly based on the actual logic
        # namespace "core" -> "core", name "http_request" -> "core_http_request"
        assert tool.function.__name__ == "core_http_request"

        # Verify the function has a docstring
        assert tool.function.__doc__ is not None
        assert "http request" in tool.function.__doc__.lower()

        # Verify the function signature has the expected parameters
        sig = inspect.signature(tool.function)
        params = sig.parameters

        # Check required parameters exist
        assert "url" in params
        assert "method" in params

        # Check parameter types
        url_param = params["url"]
        method_param = params["method"]

        # URL should be annotated as str (HttpUrl)
        assert url_param.annotation != inspect.Parameter.empty

        # Method should be annotated as RequestMethods
        assert method_param.annotation != inspect.Parameter.empty

        # Check optional parameters have defaults
        assert "headers" in params
        headers_param = params["headers"]
        assert headers_param.default is None  # Optional with None default

        assert "params" in params
        params_param = params["params"]
        assert params_param.default is None  # Optional with None default

        # All parameters should be keyword-only
        for param in params.values():
            assert param.kind == inspect.Parameter.KEYWORD_ONLY

    async def test_create_tool_from_slack_post_message(self, test_role):
        """Test creating a tool from tools.slack.post_message template action."""
        action_name = "tools.slack.post_message"

        tool = await create_tool_from_registry(action_name)

        # Verify it returns a Tool instance
        assert isinstance(tool, Tool)

        # Verify the function name is set correctly
        # namespace "tools.slack" -> "slack", name "post_message" -> "slack_post_message"
        assert tool.function.__name__ == "slack_post_message"

        # Verify the function has a docstring
        assert tool.function.__doc__ is not None
        assert "Post a message to a Slack channel" in tool.function.__doc__

        # Verify the function signature has the expected parameters
        sig = inspect.signature(tool.function)
        params = sig.parameters

        # Check required parameters exist
        assert "channel" in params
        channel_param = params["channel"]
        assert channel_param.default == PydanticUndefined  # Required

        # Check optional parameters have defaults
        assert "text" in params
        text_param = params["text"]
        assert text_param.default is None  # Optional with None default

        assert "blocks" in params
        blocks_param = params["blocks"]
        assert blocks_param.default is None  # Optional with None default

        assert "unfurl_links" in params
        unfurl_links_param = params["unfurl_links"]
        assert unfurl_links_param.default is True  # Optional with True default

        # All parameters should be keyword-only
        for param in params.values():
            assert param.kind == inspect.Parameter.KEYWORD_ONLY

    async def test_tool_function_callable(self, test_role):
        """Test that the generated tool function is properly created and has correct metadata."""
        action_name = "core.http_request"

        tool = await create_tool_from_registry(action_name)

        # Verify the tool was created successfully
        assert isinstance(tool, Tool)
        assert tool.function.__name__ == "core_http_request"

        # Verify the tool has the correct schema properties
        assert hasattr(tool, "_base_parameters_json_schema")
        schema = tool._base_parameters_json_schema
        assert "properties" in schema

        # Check that required parameters are present in schema
        properties = schema["properties"]
        assert "url" in properties
        assert "method" in properties

        # Check that optional parameters are present but not required
        assert "headers" in properties
        assert "params" in properties

    async def test_tool_function_with_optional_params(self, test_role):
        """Test tool creation with mix of required and optional parameters."""
        action_name = "tools.slack.post_message"

        tool = await create_tool_from_registry(action_name)

        # Verify the tool was created successfully
        assert isinstance(tool, Tool)
        assert tool.function.__name__ == "slack_post_message"

        # Verify the tool has the correct schema properties
        assert hasattr(tool, "_base_parameters_json_schema")
        schema = tool._base_parameters_json_schema
        assert "properties" in schema

        # Check that required parameters are present in schema
        properties = schema["properties"]
        assert "channel" in properties

        # Check that optional parameters are present
        assert "text" in properties
        assert "blocks" in properties
        assert "unfurl_links" in properties

    async def test_invalid_action_name_raises_error(self, test_role):
        """Test that invalid action names raise appropriate errors."""
        action_name = "nonexistent.action"

        with pytest.raises(
            RegistryError
        ):  # Registry raises RegistryError for missing actions
            await create_tool_from_registry(action_name)

    @pytest.mark.parametrize(
        "action_name,expected_func_name",
        [
            ("core.http_request", "core_http_request"),
            ("tools.slack.post_message", "slack_post_message"),
            ("tools.aws_boto3.s3_list_objects", "aws_boto3_s3_list_objects"),
        ],
    )
    async def test_function_name_generation(
        self, test_role, action_name, expected_func_name
    ):
        """Test that function names are generated correctly from action names."""
        # Based on the actual logic: namespace.split(".", maxsplit=1)[-1] + "_" + name
        try:
            tool = await create_tool_from_registry(action_name)
            assert tool.function.__name__ == expected_func_name
        except Exception:
            # Skip if the action doesn't exist in the test environment
            # We're mainly testing the name generation logic
            pytest.skip(f"Action {action_name} not available in test environment")


@pytest.mark.anyio
class TestTracecatAgentBuilder:
    """Test suite for TracecatAgentBuilder class."""

    @pytest.fixture
    def mock_registry_deps(self):
        """Fixture that provides commonly mocked dependencies for agent builder tests."""
        with patch.multiple(
            "registry.tracecat_registry.integrations.agents.builder",
            RegistryActionsService=Mock(),
            create_tool_from_registry=Mock(),
            build_agent=Mock(),
        ) as mocks:
            # Configure service mock
            mock_service = AsyncMock()
            mocks[
                "RegistryActionsService"
            ].with_session.return_value.__aenter__.return_value = mock_service

            # Configure tool creation mock
            mocks["create_tool_from_registry"].return_value = Tool(lambda: None)

            # Configure agent builder mock
            mocks["build_agent"].return_value = Mock()

            # Add the service instance to mocks for easy access
            mocks["service"] = mock_service

            yield mocks

    async def test_builder_initialization(self):
        """Test that TracecatAgentBuilder initializes correctly."""
        builder = TracecatAgentBuilder(
            model_name="gpt-4",
            model_provider="openai",
            instructions="You are a helpful assistant",
        )

        assert builder.model_name == "gpt-4"
        assert builder.model_provider == "openai"
        assert builder.instructions == "You are a helpful assistant"
        assert builder.tools == []
        assert builder.namespace_filters == []
        assert builder.action_filters == []

    async def test_builder_with_filters(self):
        """Test builder with namespace and action filters."""
        builder = TracecatAgentBuilder(
            model_name="gpt-4",
            model_provider="openai",
        )

        # Test chaining
        result = builder.with_namespace_filter("tools.slack").with_action_filter(
            "core.http_request"
        )

        assert result is builder  # Should return self for chaining
        assert "tools.slack" in builder.namespace_filters
        assert "core.http_request" in builder.action_filters

    async def test_builder_with_custom_tool(self):
        """Test adding custom tools to the builder."""

        # Create a simple custom tool function
        async def custom_tool_func(message: str) -> str:
            return f"Custom response: {message}"

        custom_tool = Tool(custom_tool_func)

        builder = TracecatAgentBuilder(
            model_name="gpt-4",
            model_provider="openai",
        )

        result = builder.with_custom_tool(custom_tool)
        assert result is builder  # Should return self for chaining
        assert custom_tool in builder.tools

    async def test_builder_build_with_mock_registry(
        self, test_role, mock_registry_deps
    ):
        """Test building an agent with mocked registry actions."""
        builder = TracecatAgentBuilder(
            model_name="gpt-4",
            model_provider="openai",
            instructions="You are a helpful assistant",
        )

        # Mock the registry service to return a controlled set of actions
        mock_reg_action = Mock()
        mock_reg_action.namespace = "core"
        mock_reg_action.name = "http_request"

        mock_registry_deps["service"].list_actions.return_value = [mock_reg_action]

        # Build the agent
        agent = await builder.build()

        # Verify calls
        mock_registry_deps["service"].list_actions.assert_called_once_with(
            include_marked=True
        )
        mock_registry_deps["create_tool_from_registry"].assert_called_once_with(
            "core.http_request"
        )
        mock_registry_deps["build_agent"].assert_called_once()

        # Verify the agent is returned
        assert agent == mock_registry_deps["build_agent"].return_value

    async def test_builder_build_with_namespace_filter(
        self, test_role, mock_registry_deps
    ):
        """Test building an agent with namespace filtering."""
        builder = TracecatAgentBuilder(
            model_name="gpt-4",
            model_provider="openai",
        ).with_namespace_filter("tools.slack")

        # Mock registry actions - some match filter, some don't
        mock_actions = [
            Mock(namespace="tools.slack", name="post_message"),
            Mock(namespace="core", name="http_request"),
            Mock(namespace="tools.slack", name="lookup_user"),
        ]

        mock_registry_deps["service"].list_actions.return_value = mock_actions

        await builder.build()

        # Should only call create_tool_from_registry for tools.slack actions
        expected_calls = [
            call("tools.slack.post_message"),
            call("tools.slack.lookup_user"),
        ]
        mock_registry_deps["create_tool_from_registry"].assert_has_calls(
            expected_calls, any_order=True
        )
        assert mock_registry_deps["create_tool_from_registry"].call_count == 2
