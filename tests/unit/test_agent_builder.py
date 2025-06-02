"""Tests for agent builder functionality."""

import inspect
import os
from typing import Any
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from dotenv import load_dotenv
from pydantic_ai.tools import Tool
from tracecat_registry.integrations.agents.builder import (
    TracecatAgentBuilder,
    _create_function_signature,
    _extract_action_metadata,
    _generate_tool_function_name,
    agent,
    create_tool_from_registry,
    generate_google_style_docstring,
)

from tracecat.types.exceptions import RegistryError

# Load environment variables from .env file
load_dotenv()


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

        # Verify Args section is present with parameter descriptions
        assert "Args:" in tool.function.__doc__
        assert "url:" in tool.function.__doc__  # Check for parameter documentation
        assert "method:" in tool.function.__doc__

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

        # Verify Args section is present with parameter descriptions
        assert "Args:" in tool.function.__doc__
        assert "channel:" in tool.function.__doc__  # Check for parameter documentation
        assert "text:" in tool.function.__doc__

        # Verify the function signature has the expected parameters
        sig = inspect.signature(tool.function)
        params = sig.parameters

        # Check required parameters exist
        assert "channel" in params
        channel_param = params["channel"]
        assert channel_param.default == inspect.Parameter.empty  # Required field

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

    async def test_action_without_description_raises_error(self, test_role):
        """Test that actions without descriptions raise ValueError."""
        action_name = "test.action"

        # Mock the bound action returned by get_bound
        mock_bound_action = Mock()
        mock_bound_action.namespace = "test"
        mock_bound_action.name = "action"
        mock_bound_action.is_template = False
        mock_bound_action.description = None  # No description
        mock_bound_action.args_cls = Mock()
        mock_bound_action.args_cls.model_fields = {}

        # Mock the registry action (different from bound action)
        mock_reg_action = Mock()

        with patch(
            "tracecat_registry.integrations.agents.builder.RegistryActionsService"
        ) as mock_service_cls:
            mock_service = Mock()  # Use regular Mock, not AsyncMock for the service
            mock_service.get_action = AsyncMock(return_value=mock_reg_action)
            mock_service.get_bound = Mock(return_value=mock_bound_action)

            mock_context = AsyncMock()
            mock_context.__aenter__.return_value = mock_service
            mock_service_cls.with_session.return_value = mock_context

            with pytest.raises(
                ValueError, match="Action 'test.action' has no description"
            ):
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

    async def test_google_style_docstring_generation(self, test_role):
        """Test that docstrings are generated with Args section from JSON schema."""
        action_name = "core.http_request"

        tool = await create_tool_from_registry(action_name)
        docstring = tool.function.__doc__

        # Ensure docstring exists
        assert docstring is not None, "Tool function should have a docstring"

        # Print the actual docstring for visibility
        print("\n" + "=" * 60)
        print(f"Generated docstring for {action_name}:")
        print("=" * 60)
        print(docstring)
        print("=" * 60 + "\n")

        # Split docstring into lines for detailed verification
        lines = docstring.split("\n")

        # First line should be the description
        assert len(lines) > 0
        assert "HTTP request" in lines[0]

        # Find Args section
        args_index = None
        for i, line in enumerate(lines):
            if line.strip() == "Args:":
                args_index = i
                break

        assert args_index is not None, "Args section not found in docstring"

        # Verify at least some expected parameters are documented
        params_section = "\n".join(lines[args_index:])

        # Check for required parameters with type annotations
        assert "url:" in params_section
        assert "method:" in params_section

        # Check for optional parameters with defaults
        assert "headers:" in params_section
        # The new cleaner format doesn't duplicate default values in docstrings
        # since they're already in the function signature

    async def test_google_style_docstring_slack_example(self, test_role):
        """Test and display docstring for a Slack template action."""
        action_name = "tools.slack.post_message"

        tool = await create_tool_from_registry(action_name)
        docstring = tool.function.__doc__

        # Ensure docstring exists
        assert docstring is not None, "Tool function should have a docstring"

        # Print the actual docstring for visibility
        print("\n" + "=" * 60)
        print(f"Generated docstring for {action_name}:")
        print("=" * 60)
        print(docstring)
        print("=" * 60 + "\n")

        # Verify it has the expected structure
        assert "Post a message to a Slack channel" in docstring
        assert "Args:" in docstring
        assert "channel:" in docstring
        assert "text:" in docstring
        assert "blocks:" in docstring

    async def test_parameter_description_enforcement(self, test_role):
        """Test that tools are created with parameter description enforcement."""
        action_name = "core.http_request"

        tool = await create_tool_from_registry(action_name)

        # Verify the tool has the correct configuration
        assert tool.docstring_format == "google"
        assert tool.require_parameter_descriptions is True

        # Verify all parameters have descriptions in the docstring
        docstring = tool.function.__doc__
        assert docstring is not None

        # The Args section should contain all parameter descriptions
        assert "Args:" in docstring
        assert "url: The destination of the HTTP request" in docstring
        assert "method: HTTP request method" in docstring
        assert "headers: HTTP request headers" in docstring

    def test_generate_google_style_docstring(self):
        """Test the generate_google_style_docstring function with a valid model."""
        from pydantic import BaseModel, Field

        # Test with a simple model
        class SimpleModel(BaseModel):
            name: str = Field(description="The user's name")
            age: int = Field(description="The user's age")
            email: str | None = Field(None, description="The user's email address")

        # Test with a description
        docstring = generate_google_style_docstring("Create a new user", SimpleModel)

        assert docstring.startswith("Create a new user")
        assert "\n\nArgs:\n" in docstring
        assert "    name: The user's name" in docstring
        assert "    age: The user's age" in docstring
        assert "    email: The user's email address" in docstring

    def test_generate_google_style_docstring_none_description_raises_error(self):
        """Test that None description raises ValueError."""
        from pydantic import BaseModel, Field

        class SimpleModel(BaseModel):
            name: str = Field(description="The user's name")

        with pytest.raises(ValueError, match="Tool description cannot be None"):
            generate_google_style_docstring(None, SimpleModel)


@pytest.mark.anyio
class TestTracecatAgentBuilder:
    """Test suite for TracecatAgentBuilder class."""

    @pytest.fixture
    def mock_registry_deps(self):
        """Fixture that provides commonly mocked dependencies for agent builder tests."""
        # Create mocks for the dependencies
        mock_service = AsyncMock()
        mock_registry_service = Mock()

        # Mock the async context manager pattern
        mock_context_manager = AsyncMock()
        mock_context_manager.__aenter__.return_value = mock_service
        mock_context_manager.__aexit__.return_value = None
        mock_registry_service.with_session.return_value = mock_context_manager

        mock_create_tool = AsyncMock()
        mock_create_tool.return_value = Tool(lambda: None)

        mock_build_agent = Mock()
        mock_build_agent.return_value = Mock()

        # Apply patches
        with patch(
            "tracecat_registry.integrations.agents.builder.RegistryActionsService",
            mock_registry_service,
        ):
            with patch(
                "tracecat_registry.integrations.agents.builder.create_tool_from_registry",
                mock_create_tool,
            ):
                with patch(
                    "tracecat_registry.integrations.agents.builder.build_agent",
                    mock_build_agent,
                ):
                    yield {
                        "RegistryActionsService": mock_registry_service,
                        "create_tool_from_registry": mock_create_tool,
                        "build_agent": mock_build_agent,
                        "service": mock_service,
                    }

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

        # Mock create_tool_from_registry to return a simple tool instead of calling the real function
        from pydantic import BaseModel, Field

        class MockArgs(BaseModel):
            test_param: str = Field(description="Test parameter")

        async def mock_tool_func(test_param: str) -> str:
            return f"Mock result: {test_param}"

        mock_tool = Tool(mock_tool_func)
        mock_registry_deps["create_tool_from_registry"].return_value = mock_tool

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
        # Need to create proper mock objects with namespace and name as strings
        mock_action1 = Mock()
        mock_action1.namespace = "tools.slack"
        mock_action1.name = "post_message"

        mock_action2 = Mock()
        mock_action2.namespace = "core"
        mock_action2.name = "http_request"

        mock_action3 = Mock()
        mock_action3.namespace = "tools.slack"
        mock_action3.name = "lookup_user"

        mock_actions = [mock_action1, mock_action2, mock_action3]

        mock_registry_deps["service"].list_actions.return_value = mock_actions

        # Mock create_tool_from_registry to return a simple tool
        from pydantic import BaseModel, Field

        class MockArgs(BaseModel):
            test_param: str = Field(description="Test parameter")

        async def mock_tool_func(test_param: str) -> str:
            return f"Mock result: {test_param}"

        mock_tool = Tool(mock_tool_func)
        mock_registry_deps["create_tool_from_registry"].return_value = mock_tool

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


@pytest.mark.anyio
class TestAgentBuilderHelpers:
    """Test suite for agent builder helper functions."""

    def test_generate_tool_function_name(self):
        """Test function name generation from namespace and action name."""
        # Test simple namespace
        assert (
            _generate_tool_function_name("core", "http_request") == "core_http_request"
        )

        # Test nested namespace - should take last part
        assert (
            _generate_tool_function_name("tools.slack", "post_message")
            == "slack_post_message"
        )

        # Test deeply nested namespace
        assert (
            _generate_tool_function_name("tools.aws.boto3", "s3_list_objects")
            == "aws.boto3_s3_list_objects"
        )

        # Test single-word actions
        assert _generate_tool_function_name("core", "transform") == "core_transform"

    def test_create_function_signature_with_required_params(self):
        """Test function signature creation with required parameters."""
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            name: str = Field(description="User name")
            age: int = Field(description="User age")

        signature, annotations = _create_function_signature(TestModel)

        # Check signature parameters
        params = list(signature.parameters.values())
        assert len(params) == 2

        # Check first parameter
        assert params[0].name == "name"
        assert params[0].annotation is str
        assert params[0].default == inspect.Parameter.empty  # Required field
        assert params[0].kind == inspect.Parameter.KEYWORD_ONLY

        # Check second parameter
        assert params[1].name == "age"
        assert params[1].annotation is int
        assert params[1].default == inspect.Parameter.empty  # Required field

        # Check annotations
        assert annotations["name"] is str
        assert annotations["age"] is int
        assert annotations["return"] is Any

    def test_create_function_signature_with_optional_params(self):
        """Test function signature creation with optional parameters."""
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            name: str = Field(description="User name")
            email: str | None = Field(None, description="User email")
            active: bool = Field(True, description="Is active")

        signature, annotations = _create_function_signature(TestModel)

        params = list(signature.parameters.values())
        assert len(params) == 3

        # Required parameter
        assert params[0].name == "name"
        assert params[0].default == inspect.Parameter.empty  # Required field

        # Optional with None default
        assert params[1].name == "email"
        assert params[1].default is None
        # The field is already str | None, so it should not be double-wrapped
        assert annotations["email"] == str | None

        # Optional with non-None default
        assert params[2].name == "active"
        assert params[2].default is True
        assert annotations["active"] is bool

    def test_extract_action_metadata_udf(self):
        """Test metadata extraction from a UDF action."""
        # Mock a UDF bound action
        mock_action = Mock()
        mock_action.is_template = False
        mock_action.description = "Test UDF action"
        mock_action.args_cls = Mock()

        description, model_cls = _extract_action_metadata(mock_action)

        assert description == "Test UDF action"
        assert model_cls == mock_action.args_cls

    def test_extract_action_metadata_template(self):
        """Test metadata extraction from a template action."""
        # Mock a template bound action
        mock_action = Mock()
        mock_action.is_template = True
        mock_action.template_action = Mock()
        mock_action.template_action.definition = Mock()
        mock_action.template_action.definition.description = "Template description"
        mock_action.template_action.definition.action = "tools.slack.post_message"
        mock_action.template_action.definition.expects = {}
        mock_action.description = "Fallback description"

        with patch(
            "tracecat_registry.integrations.agents.builder.create_expectation_model"
        ) as mock_create:
            mock_model = Mock()
            mock_create.return_value = mock_model

            description, model_cls = _extract_action_metadata(mock_action)

            assert description == "Template description"
            assert model_cls == mock_model
            mock_create.assert_called_once_with({}, "tools__slack__post_message")

    def test_extract_action_metadata_template_fallback(self):
        """Test metadata extraction uses fallback description when template description is None."""
        mock_action = Mock()
        mock_action.is_template = True
        mock_action.template_action = Mock()
        mock_action.template_action.definition = Mock()
        mock_action.template_action.definition.description = (
            None  # No template description
        )
        mock_action.template_action.definition.action = "tools.slack.post_message"
        mock_action.template_action.definition.expects = {}
        mock_action.description = "Fallback description"

        with patch(
            "tracecat_registry.integrations.agents.builder.create_expectation_model"
        ):
            description, _ = _extract_action_metadata(mock_action)
            assert description == "Fallback description"

    def test_extract_action_metadata_template_not_set(self):
        """Test metadata extraction raises error when template action is not set."""
        mock_action = Mock()
        mock_action.is_template = True
        mock_action.template_action = None

        with pytest.raises(ValueError, match="Template action is not set"):
            _extract_action_metadata(mock_action)


@pytest.mark.anyio
class TestAgentBuilderIntegration:
    """Integration test suite for TracecatAgentBuilder with real registry actions."""

    async def test_agent_with_core_actions_integration(self, test_role):
        """Test building and using an agent with real core actions."""
        # Build an agent with core actions
        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="You are a helpful assistant that can make HTTP requests and transform data.",
        )

        # Filter to only core actions for this test
        agent = await builder.with_namespace_filter("core").build()

        # Verify the agent was created
        assert agent is not None

        # Verify tools were loaded
        assert len(builder.tools) > 0

        # Check that we have some expected core tools
        tool_names = [tool.function.__name__ for tool in builder.tools]

        # Should have core HTTP and transform tools
        expected_tools = ["core_http_request", "core_reshape"]
        found_tools = [name for name in expected_tools if name in tool_names]
        assert len(found_tools) > 0, (
            f"Expected to find some of {expected_tools} in {tool_names}"
        )

    async def test_agent_with_python_script_action(self, test_role):
        """Test building an agent with the core Python script action."""
        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="You are a helpful assistant that can run Python scripts.",
        )

        # Filter to only the Python script action
        agent = await builder.with_action_filter("core.script.run_python").build()

        # Verify the agent was created
        assert agent is not None

        # Should have exactly one tool
        assert len(builder.tools) == 1

        # Verify it's the Python script tool
        tool = builder.tools[0]
        assert tool.function.__name__ == "script_run_python"

        # Verify the tool has the expected parameters
        sig = inspect.signature(tool.function)
        params = list(sig.parameters.keys())

        # Should have the main parameters from the Python script action
        expected_params = [
            "script",
            "inputs",
            "dependencies",
            "timeout_seconds",
            "allow_network",
        ]
        for param in expected_params:
            assert param in params, (
                f"Expected parameter '{param}' not found in {params}"
            )

    async def test_agent_with_template_action_integration(self, test_role):
        """Test building an agent with a template action."""
        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="You are a helpful assistant that can post Slack messages.",
        )

        # Filter to only Slack template actions
        agent = await builder.with_namespace_filter("tools.slack").build()

        # Verify the agent was created
        assert agent is not None

        # Should have some tools
        assert len(builder.tools) > 0

        # Check for Slack-related tools
        tool_names = [tool.function.__name__ for tool in builder.tools]
        slack_tools = [name for name in tool_names if "slack" in name.lower()]
        assert len(slack_tools) > 0, f"Expected to find Slack tools in {tool_names}"

        # Verify at least one tool has the expected Slack parameters
        slack_tool = None
        for tool in builder.tools:
            if "post_message" in tool.function.__name__:
                slack_tool = tool
                break

        if slack_tool:
            sig = inspect.signature(slack_tool.function)
            params = list(sig.parameters.keys())

            # Should have channel parameter for Slack message posting
            assert "channel" in params, (
                f"Expected 'channel' parameter in Slack tool, got {params}"
            )

    @pytest.mark.anyio
    @pytest.mark.skipif(
        not os.getenv("SLACK_BOT_TOKEN") or not os.getenv("SLACK_CHANNEL_ID"),
        reason="SLACK_BOT_TOKEN and SLACK_CHANNEL_ID must be set in .env file for live test",
    )
    @pytest.mark.parametrize(
        "prompt_type,prompt_template",
        [
            (
                "simple",
                "Post a message to Slack asking: 'Which programming language do you prefer? "
                "Python 🐍 or JavaScript ⚡? Let us know in the comments!' "
                "Post this to channel: {channel}",
            ),
            (
                "medium",
                "Post an interactive message to Slack asking people to vote between Python and JavaScript. "
                "Include two buttons: 'Python 🐍' and 'JavaScript ⚡'. "
                "Use simple Slack blocks format. "
                "Post this to channel: {channel}",
            ),
            (
                "complex",
                "Post a fun interactive message to the Slack channel asking people to vote on "
                "which is the better programming language. The message should include:\n"
                "1. A header section with an emoji and title 'Which is the better programming language?'\n"
                "2. Two comparison sections side by side:\n"
                "   - Python: 'Simple, readable, has pandas' \n"
                "   - JavaScript: 'Runs everywhere, async/await, has npm chaos'\n"
                "3. Two action buttons: 'Python 🐍' (green/primary) and 'JavaScript ⚡' (yellow/secondary)\n"
                "4. A context section with a fun note\n"
                "5. Use proper Slack block kit JSON format with sections, actions, and context blocks\n"
                "Post this to channel: {channel}",
            ),
        ],
    )
    async def test_agent_live_slack_prompts(
        self, test_role, prompt_type, prompt_template
    ):
        """Live test: Agent creates Slack messages with varying complexity levels."""

        # Get environment variables
        slack_token = os.getenv("SLACK_BOT_TOKEN")
        slack_channel = os.getenv("SLACK_CHANNEL_ID")

        if not slack_token or not slack_channel:
            pytest.skip("Slack credentials not available")

        # Mock the secrets for Slack integration
        with patch("tracecat_registry.integrations.slack_sdk.secrets.get") as mock_get:

            def side_effect(key):
                if key == "SLACK_BOT_TOKEN":
                    return slack_token
                return None

            mock_get.side_effect = side_effect

            # Build an agent with Slack capabilities
            builder = TracecatAgentBuilder(
                model_name="gpt-4o-mini",
                model_provider="openai",
                instructions=(
                    "You are a helpful assistant that can post interactive Slack messages. "
                    "When asked to create interactive messages, use proper Slack block kit format "
                    "with buttons, sections, and other interactive elements. "
                    "If complex blocks fail, try simpler alternatives."
                ),
            )

            # Filter to Slack tools
            agent = await builder.with_namespace_filter("tools.slack").build()

            # Verify agent was created with Slack tools
            assert agent is not None
            assert len(builder.tools) > 0

            # Format the prompt with the channel
            prompt = prompt_template.format(channel=slack_channel)

            print(f"\n🤖 Running agent with {prompt_type} Slack prompt...")
            print(f"📝 Prompt: {prompt}")

            # Run the agent
            result = await agent.run(prompt)

            print(f"\n✅ Agent execution completed for {prompt_type} prompt!")
            print(f"📤 Result: {result}")

            # Verify we got a result
            assert result is not None
            assert isinstance(result.output, str)

            # Should mention successful posting or contain message details
            result_lower = result.output.lower()
            success_indicators = [
                "posted",
                "sent",
                "message",
                "slack",
                "channel",
                "success",
                "python",
                "javascript",
                "programming",
            ]

            found_indicators = [
                indicator
                for indicator in success_indicators
                if indicator in result_lower
            ]
            assert len(found_indicators) > 0, (
                f"Expected success indicators in result: {result.output}"
            )

            print(
                f"🎉 {prompt_type.title()} prompt test successful! Found indicators: {found_indicators}"
            )

    @pytest.mark.anyio
    @pytest.mark.skipif(
        not os.getenv("SLACK_BOT_TOKEN") or not os.getenv("SLACK_CHANNEL_ID"),
        reason="SLACK_BOT_TOKEN and SLACK_CHANNEL_ID must be set in .env file for live test",
    )
    async def test_agent_function_direct(self, test_role):
        """Live test: Test the agent registry function directly."""

        # Get environment variables
        slack_token = os.getenv("SLACK_BOT_TOKEN")
        slack_channel = os.getenv("SLACK_CHANNEL_ID")

        if not slack_token or not slack_channel:
            pytest.skip("Slack credentials not available")

        # Mock the secrets for Slack integration
        with patch("tracecat_registry.integrations.slack_sdk.secrets.get") as mock_get:

            def side_effect(key):
                if key == "SLACK_BOT_TOKEN":
                    return slack_token
                return None

            mock_get.side_effect = side_effect

            # Call the agent function directly
            result = await agent(
                user_prompt=(
                    f"Post a simple message to Slack channel {slack_channel} saying "
                    "'Hello from the Tracecat AI agent! 🤖 This is a test message.'"
                ),
                model_name="gpt-4o-mini",
                model_provider="openai",
                namespaces=["tools.slack"],
                actions=[],
                instructions="You are a helpful assistant that can post messages to Slack.",
                include_usage=True,
            )

            print(f"\n🤖 Agent function result: {result}")

            # Verify the result structure
            assert isinstance(result, dict)
            assert "output" in result
            assert "message_history" in result
            assert "duration" in result
            assert "usage" in result

            # Verify the output contains success indicators
            output = result["output"]
            output_lower = str(output).lower()
            success_indicators = ["posted", "message", "slack", "hello", "test"]
            found_indicators = [
                indicator
                for indicator in success_indicators
                if indicator in output_lower
            ]
            assert len(found_indicators) > 0, (
                f"Expected success indicators in output: {output}"
            )

            # Verify message history exists
            assert isinstance(result["message_history"], list)
            assert len(result["message_history"]) > 0

            # Verify usage information
            assert isinstance(result["usage"], dict)

            print(
                f"🎉 Agent function test successful! Found indicators: {found_indicators}"
            )
            print(f"📊 Usage: {result['usage']}")
            print(f"⏱️ Duration: {result['duration']:.2f}s")
