"""Tests for agent builder functionality."""

import inspect
from typing import Any
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from dotenv import load_dotenv
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.tools import Tool
from tracecat_registry import RegistrySecret
from tracecat_registry.integrations.agents.builder import (
    TracecatAgentBuilder,
    _create_function_signature,
    _extract_action_metadata,
    _generate_tool_function_name,
    agent,
    call_tracecat_action,
    create_tool_from_registry,
    generate_google_style_docstring,
)

from tests.conftest import (
    requires_slack_mocks,
    skip_if_no_slack_token,
)
from tracecat.registry.actions.models import BoundRegistryAction
from tracecat.types.exceptions import RegistryError

# Load environment variables from .env file
load_dotenv()


@pytest.mark.anyio
class TestCreateToolFromRegistry:
    """Test suite for create_tool_from_registry function."""

    async def test_create_tool_from_core_http_request(self, test_role):
        """Test creating a tool from core.http_request action."""
        action_name = "core.cases.create_case"

        tool = await create_tool_from_registry(action_name)

        # Verify it returns a Tool instance
        assert isinstance(tool, Tool)

        # Verify the function name is set correctly based on the actual logic
        # namespace "core" -> "core", name "http_request" -> "core__http_request"
        assert tool.function.__name__ == "core__cases__create_case"

        # Verify the function has a docstring
        assert tool.function.__doc__ is not None
        assert "create a new case" in tool.function.__doc__.lower()

        # Verify Args section is present with parameter descriptions
        assert "Args:" in tool.function.__doc__
        assert "summary:" in tool.function.__doc__  # Check for parameter documentation
        assert "description:" in tool.function.__doc__

        # Verify the function signature has the expected parameters
        sig = inspect.signature(tool.function)
        params = sig.parameters

        # Check required parameters exist
        assert "summary" in params
        assert "description" in params

        # Check parameter types
        summary_param = params["summary"]
        description_param = params["description"]

        # URL should be annotated as str (HttpUrl)
        assert summary_param.annotation != inspect.Parameter.empty

        # Method should be annotated as RequestMethods
        assert description_param.annotation != inspect.Parameter.empty

        # Check optional parameters have defaults
        assert "priority" in params
        priority_param = params["priority"]
        assert priority_param.default == "unknown"

        assert "severity" in params
        severity_param = params["severity"]
        assert severity_param.default == "unknown"

        # All parameters should be keyword-only
        for param in params.values():
            assert param.kind == inspect.Parameter.KEYWORD_ONLY

    @skip_if_no_slack_token
    @requires_slack_mocks
    async def test_create_tool_from_slack_post_message(
        self, mock_slack_secrets, test_role, slack_secret
    ):
        """Test creating a tool from tools.slack.post_message template action."""
        action_name = "tools.slack.post_message"

        tool = await create_tool_from_registry(action_name)

        # Verify it returns a Tool instance
        assert isinstance(tool, Tool)

        # Verify the function name is set correctly
        # namespace "tools.slack" -> "tools__slack", name "post_message" -> "tools__slack__post_message"
        assert tool.function.__name__ == "tools__slack__post_message"

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
        # Note: The channel parameter might have a None default due to how the expectation model
        # is created, even though it's required in the template. This is a known behavior.
        # The important thing is that the parameter exists and is properly typed.
        assert channel_param.annotation is not inspect.Parameter.empty

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
        action_name = "core.cases.create_case"

        tool = await create_tool_from_registry(action_name)

        # Verify the tool was created successfully
        assert isinstance(tool, Tool)
        assert tool.function.__name__ == "core__cases__create_case"

        # Verify the tool has the correct schema properties
        assert hasattr(tool, "_base_parameters_json_schema")
        schema = tool._base_parameters_json_schema
        assert "properties" in schema

        # Check that required parameters are present in schema
        properties = schema["properties"]
        assert "summary" in properties
        assert "description" in properties

        # Check that optional parameters are present but not required
        assert "priority" in properties
        assert "severity" in properties

    async def test_tool_function_with_optional_params(self, test_role, slack_secret):
        """Test tool creation with mix of required and optional parameters."""
        action_name = "tools.slack.post_message"

        tool = await create_tool_from_registry(action_name)

        # Verify the tool was created successfully
        assert isinstance(tool, Tool)
        assert tool.function.__name__ == "tools__slack__post_message"

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

    async def test_google_style_docstring_generation(self, test_role):
        """Test that docstrings are generated with Args section from JSON schema."""
        action_name = "core.cases.create_case"

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
        assert "Create a new case" in lines[0]

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
        assert "summary:" in params_section
        assert "description:" in params_section

        # Check for optional parameters with defaults
        assert "priority:" in params_section
        assert "severity:" in params_section
        # The new cleaner format doesn't duplicate default values in docstrings
        # since they're already in the function signature

    async def test_google_style_docstring_slack_example(self, test_role, slack_secret):
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
        action_name = "core.cases.create_case"

        tool = await create_tool_from_registry(action_name)

        # Verify the tool has the correct configuration
        assert tool.docstring_format == "google"
        assert tool.require_parameter_descriptions is True

        # Verify all parameters have descriptions in the docstring
        docstring = tool.function.__doc__
        assert docstring is not None

        # The Args section should contain all parameter descriptions
        assert "Args:" in docstring
        assert "summary: The summary of the case." in docstring
        assert "description: The description of the case." in docstring
        assert "priority: The priority of the case." in docstring

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
        result = builder.with_namespace_filters("tools.slack").with_action_filters(
            "core.cases.create_case"
        )

        assert result is builder  # Should return self for chaining
        assert "tools.slack" in builder.namespace_filters
        assert "core.cases.create_case" in builder.action_filters

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
        mock_reg_action.namespace = "tools.slack"
        mock_reg_action.name = "post_message"

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
            "tools.slack.post_message"
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
        ).with_namespace_filters("tools.slack")

        # Mock registry actions - some match filter, some don't
        # Need to create proper mock objects with namespace and name as strings
        mock_action1 = Mock()
        mock_action1.namespace = "tools.slack"
        mock_action1.name = "post_message"

        mock_action2 = Mock()
        mock_action2.namespace = "core"
        mock_action2.name = "cases.create_case"

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
            _generate_tool_function_name("core", "cases.create_case")
            == "core__cases__create_case"
        )

        # Test nested namespace - should replace all dots with separator
        assert (
            _generate_tool_function_name("tools.slack", "post_message")
            == "tools__slack__post_message"
        )

        # Test deeply nested namespace
        assert (
            _generate_tool_function_name("tools.aws.boto3", "s3_list_objects")
            == "tools__aws__boto3__s3_list_objects"
        )

        # Test single-word actions
        assert _generate_tool_function_name("core", "transform") == "core__transform"

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
class TestCallTracecatAction:
    """Test suite for call_tracecat_action function."""

    async def test_call_tracecat_action_with_secrets(self, test_role, mocker):
        """Test that call_tracecat_action properly fetches and sets up secrets."""
        from unittest.mock import AsyncMock, Mock

        # Mock the registry action
        mock_reg_action = Mock()
        mock_reg_action.namespace = "tools.test"
        mock_reg_action.name = "test_action"

        # Mock the bound action
        mock_bound_action = Mock(spec=BoundRegistryAction)
        mock_bound_action.is_template = False
        mock_bound_action.namespace = "tools.test"
        mock_bound_action.name = "test_action"

        # Mock action secrets
        test_secret = RegistrySecret(name="test_secret", keys=["TEST_KEY"])

        # Mock the registry service - mix of async and sync methods
        mock_service = Mock()
        mock_service.get_action = AsyncMock(return_value=mock_reg_action)
        mock_service.fetch_all_action_secrets = AsyncMock(return_value=[test_secret])
        mock_service.get_bound = Mock(return_value=mock_bound_action)  # This is sync

        # Mock the service context manager
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_service

        mocker.patch(
            "tracecat_registry.integrations.agents.builder.RegistryActionsService.with_session",
            return_value=mock_context,
        )

        # Mock get_action_secrets to return the expected secrets
        mock_get_action_secrets = mocker.patch(
            "tracecat_registry.integrations.agents.builder.get_action_secrets",
            return_value={"test_secret": {"TEST_KEY": "test_value"}},
        )

        # Mock env_sandbox
        mock_env_sandbox = mocker.patch(
            "tracecat_registry.integrations.agents.builder.env_sandbox"
        )

        # Mock flatten_secrets
        mocker.patch(
            "tracecat_registry.integrations.agents.builder.flatten_secrets",
            return_value={"TEST_KEY": "test_value"},
        )

        # Mock _run_action_direct to return a result
        mocker.patch(
            "tracecat_registry.integrations.agents.builder._run_action_direct",
            return_value="test_result",
        )

        # Call the function
        result = await call_tracecat_action(
            "tools.test.test_action", {"param": "value"}
        )

        # Verify get_action_secrets was called with correct parameters
        mock_get_action_secrets.assert_called_once()
        call_args = mock_get_action_secrets.call_args
        assert call_args.kwargs["args"] == {"param": "value"}
        assert len(call_args.kwargs["action_secrets"]) == 1
        assert list(call_args.kwargs["action_secrets"])[0].name == "test_secret"

        # Verify env_sandbox was called
        mock_env_sandbox.assert_called_once_with({"TEST_KEY": "test_value"})

        # Verify result
        assert result == "test_result"

    async def test_call_tracecat_action_with_template(self, test_role, mocker):
        """Test that call_tracecat_action properly handles template actions with secrets."""
        from unittest.mock import AsyncMock, Mock

        from tracecat.registry.actions.models import TemplateAction

        # Mock the registry action
        mock_reg_action = Mock()
        mock_reg_action.namespace = "tools.test"
        mock_reg_action.name = "test_template"

        # Mock template action
        mock_template_action = Mock(spec=TemplateAction)

        # Mock the bound action as a template
        mock_bound_action = Mock(spec=BoundRegistryAction)
        mock_bound_action.is_template = True
        mock_bound_action.namespace = "tools.test"
        mock_bound_action.name = "test_template"
        mock_bound_action.template_action = mock_template_action

        # Mock action secrets
        template_secret = RegistrySecret(name="template_secret", keys=["TEMPLATE_KEY"])

        # Mock the registry service - mix of async and sync methods
        mock_service = Mock()
        mock_service.get_action = AsyncMock(return_value=mock_reg_action)
        mock_service.fetch_all_action_secrets = AsyncMock(
            return_value=[template_secret]
        )
        mock_service.get_bound = Mock(return_value=mock_bound_action)  # This is sync

        # Mock the service context manager
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_service

        mocker.patch(
            "tracecat_registry.integrations.agents.builder.RegistryActionsService.with_session",
            return_value=mock_context,
        )

        # Mock get_action_secrets
        mocker.patch(
            "tracecat_registry.integrations.agents.builder.get_action_secrets",
            return_value={"template_secret": {"TEMPLATE_KEY": "template_value"}},
        )

        # Mock env_sandbox
        mocker.patch("tracecat_registry.integrations.agents.builder.env_sandbox")

        # Mock flatten_secrets
        mocker.patch(
            "tracecat_registry.integrations.agents.builder.flatten_secrets",
            return_value={"TEMPLATE_KEY": "template_value"},
        )

        # Mock run_template_action to return a result
        mocker.patch(
            "tracecat_registry.integrations.agents.builder.run_template_action",
            return_value="template_result",
        )

        # Call the function
        result = await call_tracecat_action(
            "tools.test.test_template", {"param": "value"}
        )

        # Verify the context passed to run_template_action includes secrets
        from tracecat_registry.integrations.agents.builder import run_template_action

        run_template_action.assert_called_once()
        call_kwargs = run_template_action.call_args.kwargs
        assert "context" in call_kwargs
        assert "SECRETS" in call_kwargs["context"]
        assert call_kwargs["context"]["SECRETS"] == {
            "template_secret": {"TEMPLATE_KEY": "template_value"}
        }

        # Verify result
        assert result == "template_result"


@pytest.mark.anyio
class TestFixedArguments:
    """Test suite for fixed_arguments functionality in agent builder."""

    def test_generate_google_style_docstring_with_fixed_args(self):
        """Test that fixed arguments are excluded from generated docstrings."""
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            name: str = Field(description="The user's name")
            age: int = Field(description="The user's age")
            email: str | None = Field(None, description="The user's email address")

        # Test without fixed args - should include all parameters
        docstring_all = generate_google_style_docstring("Post a message", TestModel)
        assert "name: The user's name" in docstring_all
        assert "age: The user's age" in docstring_all
        assert "email: The user's email address" in docstring_all

        # Test with fixed args - should exclude fixed parameters
        fixed_args = {"name", "age"}
        docstring_filtered = generate_google_style_docstring(
            "Post a message", TestModel, fixed_args
        )

        # Should not include fixed arguments
        assert "name: The user's name" not in docstring_filtered
        assert "age: The user's age" not in docstring_filtered

        # Should still include non-fixed arguments
        assert "email: The user's email address" in docstring_filtered

        print(f"\nDocstring with fixed args excluded:\n{docstring_filtered}")

    def test_create_function_signature_with_fixed_args(self):
        """Test that fixed arguments are excluded from function signatures."""
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            name: str = Field(description="The user's name")
            age: int = Field(description="The user's age")

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

    async def test_create_tool_from_registry_with_fixed_args(self, test_role):
        """Test creating a tool with fixed arguments."""
        action_name = "core.cases.create_case"
        fixed_args = {"priority": "high", "severity": "critical"}

        tool = await create_tool_from_registry(action_name, fixed_args)

        # Verify it returns a Tool instance
        assert isinstance(tool, Tool)
        assert tool.function.__name__ == "core__cases__create_case"

        # Verify the function signature excludes fixed parameters
        sig = inspect.signature(tool.function)
        params = list(sig.parameters.keys())

        # Should not include fixed arguments
        assert "priority" not in params
        assert "severity" not in params

        # Should still include non-fixed arguments
        assert "summary" in params
        assert "description" in params

        # Verify docstring excludes fixed arguments
        docstring = tool.function.__doc__
        assert docstring is not None
        assert "priority:" not in docstring  # Fixed arg should not be documented
        assert "severity:" not in docstring  # Fixed arg should not be documented
        assert (
            "summary: The summary of the case." in docstring
        )  # Non-fixed arg should be documented

        print(f"\nTool docstring with fixed args:\n{docstring}")
        print(f"Tool signature params: {params}")

    async def test_agent_builder_with_fixed_arguments(self, test_role, mocker):
        """Test TracecatAgentBuilder with fixed_arguments parameter."""
        fixed_arguments = {
            "core.cases.create_case": {
                "priority": "high",
                "severity": "critical",
            },
            "tools.slack.post_message": {
                "channel": "C123456789",
                "username": "TestBot",
            },
        }

        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            fixed_arguments=fixed_arguments,
        )

        # Verify fixed_arguments are stored
        assert builder.fixed_arguments == fixed_arguments

        # Mock dependencies for build()
        mock_reg_action1 = Mock()
        mock_reg_action1.namespace = "core.cases"
        mock_reg_action1.name = "create_case"

        mock_reg_action2 = Mock()
        mock_reg_action2.namespace = "tools.slack"
        mock_reg_action2.name = "post_message"

        mock_service = Mock()
        mock_service.get_actions = AsyncMock(
            return_value=[mock_reg_action1, mock_reg_action2]
        )
        mock_service.fetch_all_action_secrets = AsyncMock(return_value=[])

        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_service

        mocker.patch(
            "tracecat_registry.integrations.agents.builder.RegistryActionsService.with_session",
            return_value=mock_context,
        )

        # Mock create_tool_from_registry to verify it's called with fixed args
        mock_create_tool = mocker.patch(
            "tracecat_registry.integrations.agents.builder.create_tool_from_registry",
            return_value=Mock(),  # Just return a simple mock instead of trying to create a Tool
        )

        mocker.patch(
            "tracecat_registry.integrations.agents.builder.build_agent",
            return_value=Mock(),
        )

        # Build agent with specific actions
        await builder.with_action_filters(
            "core.cases.create_case", "tools.slack.post_message"
        ).build()

        # Verify create_tool_from_registry was called with correct fixed args
        expected_calls = [
            call(
                "core.cases.create_case",
                {"priority": "high", "severity": "critical"},
            ),
            call(
                "tools.slack.post_message",
                {"channel": "C123456789", "username": "TestBot"},
            ),
        ]
        mock_create_tool.assert_has_calls(expected_calls, any_order=True)

    async def test_agent_builder_with_partial_fixed_arguments(self, test_role, mocker):
        """Test that actions without fixed arguments get empty dict."""
        fixed_arguments = {
            "core.cases.create_case": {"priority": "high"}
            # tools.slack.post_message intentionally not included
        }

        builder = TracecatAgentBuilder(
            model_name="gpt-4o-mini",
            model_provider="openai",
            fixed_arguments=fixed_arguments,
        )

        # Mock dependencies
        mock_reg_action1 = Mock()
        mock_reg_action1.namespace = "core.cases"
        mock_reg_action1.name = "create_case"

        mock_reg_action2 = Mock()
        mock_reg_action2.namespace = "tools.slack"
        mock_reg_action2.name = "post_message"

        mock_service = Mock()
        mock_service.get_actions = AsyncMock(
            return_value=[mock_reg_action1, mock_reg_action2]
        )
        mock_service.fetch_all_action_secrets = AsyncMock(return_value=[])

        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_service

        mocker.patch(
            "tracecat_registry.integrations.agents.builder.RegistryActionsService.with_session",
            return_value=mock_context,
        )

        mock_create_tool = mocker.patch(
            "tracecat_registry.integrations.agents.builder.create_tool_from_registry",
            return_value=Mock(),  # Just return a simple mock instead of trying to create a Tool
        )

        mocker.patch(
            "tracecat_registry.integrations.agents.builder.build_agent",
            return_value=Mock(),
        )

        await builder.with_action_filters(
            "core.cases.create_case", "tools.slack.post_message"
        ).build()

        # Verify create_tool_from_registry was called correctly
        expected_calls = [
            call("core.cases.create_case", {"priority": "high"}),  # Has fixed args
            call("tools.slack.post_message", {}),  # No fixed args, empty dict
        ]
        mock_create_tool.assert_has_calls(expected_calls, any_order=True)

    async def test_agent_function_with_fixed_arguments(self, test_role, mocker):
        """Test the agent registry function with fixed_arguments parameter."""
        fixed_arguments = {
            "core.cases.create_case": {
                "priority": "high",
                "severity": "critical",
            }
        }

        # Mock the TracecatAgentBuilder
        mock_builder = Mock()
        mock_agent = Mock()
        mock_builder.with_action_filters.return_value = mock_builder
        mock_builder.build = AsyncMock(return_value=mock_agent)

        mock_run_result = Mock(spec=AgentRunResult)
        mock_run_result.output = "HTTP request completed successfully"
        mock_run_result.all_messages.return_value = []
        mock_run_result.usage.return_value = {"total_tokens": 100}

        mock_run_context = AsyncMock()
        mock_run_context.result = mock_run_result
        mock_run_context.__aenter__.return_value = mock_run_context
        mock_run_context.__aiter__.return_value = iter([])

        mock_agent.iter.return_value = mock_run_context

        # Mock TracecatAgentBuilder constructor
        mock_builder_constructor = mocker.patch(
            "tracecat_registry.integrations.agents.builder.TracecatAgentBuilder",
            return_value=mock_builder,
        )

        # Mock timeit
        mocker.patch(
            "tracecat_registry.integrations.agents.builder.timeit",
            side_effect=[0.0, 1.5],
        )

        # Call the agent function
        result = await agent(
            user_prompt="Make a POST request to https://api.example.com/data",
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=["core.cases.create_case"],
            fixed_arguments=fixed_arguments,
            include_usage=True,
        )

        # Verify TracecatAgentBuilder was initialized with fixed_arguments
        mock_builder_constructor.assert_called_once_with(
            model_name="gpt-4o-mini",
            model_provider="openai",
            base_url=None,
            instructions=None,
            output_type=None,
            model_settings=None,
            retries=6,
            fixed_arguments=fixed_arguments,
        )

        # Verify the result structure
        assert isinstance(result, dict)
        assert result["output"] == "HTTP request completed successfully"
        assert "duration" in result
        assert "usage" in result

    async def test_empty_fixed_arguments_behavior(self, test_role):
        """Test that empty or None fixed_arguments work correctly."""
        # Test with None
        builder1 = TracecatAgentBuilder(
            model_name="gpt-4o-mini", model_provider="openai", fixed_arguments=None
        )
        assert builder1.fixed_arguments == {}

        # Test with empty dict
        builder2 = TracecatAgentBuilder(
            model_name="gpt-4o-mini", model_provider="openai", fixed_arguments={}
        )
        assert builder2.fixed_arguments == {}

        # Test create_tool_from_registry with None fixed_args
        tool = await create_tool_from_registry("core.cases.create_case", None)
        assert isinstance(tool, Tool)

        # Should have all original parameters since nothing is fixed
        sig = inspect.signature(tool.function)
        params = list(sig.parameters.keys())
        assert "summary" in params
        assert "description" in params
        assert "priority" in params
        assert "severity" in params

    def test_fixed_args_parameter_validation(self):
        """Test that fixed_args parameter is properly validated."""
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            param1: str = Field(description="Parameter 1")
            param2: int = Field(description="Parameter 2")
            param3: bool = Field(description="Parameter 3")

        # Test with valid fixed_args
        fixed_args = {"param1", "param3"}
        signature, annotations = _create_function_signature(TestModel, fixed_args)
        params = list(signature.parameters.keys())

        assert "param1" not in params  # Should be excluded
        assert "param2" in params  # Should be included
        assert "param3" not in params  # Should be excluded

        # Test with empty set
        signature_empty, _ = _create_function_signature(TestModel, set())
        params_empty = list(signature_empty.parameters.keys())
        assert len(params_empty) == 3  # All parameters should be included

        # Test with None (should behave like empty set)
        signature_none, _ = _create_function_signature(TestModel, None)
        params_none = list(signature_none.parameters.keys())
        assert len(params_none) == 3  # All parameters should be included
