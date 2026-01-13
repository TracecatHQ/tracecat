"""Unit tests for agent tools module.

Tests the harness-agnostic tool creation and execution:
- Tool dataclass construction
- ToolExecutionError exception
- denormalize_tool_name utility
- _extract_action_metadata for UDFs and templates
- create_tool_from_registry
"""

import pytest
from pydantic import BaseModel, Field, TypeAdapter

from tracecat.agent.tools import (
    ToolExecutionError,
    _extract_action_metadata,
    denormalize_tool_name,
)
from tracecat.agent.types import Tool
from tracecat.expressions.expectations import ExpectedField
from tracecat.registry.actions.bound import BoundRegistryAction
from tracecat.registry.actions.schemas import (
    ActionStep,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.registry.repository import Repository


class SampleArgs(BaseModel):
    foo: int = Field(..., description="Foo argument")


def sample_udf(foo: int) -> int:
    return foo


def build_udf_action(
    description: str = "Sample UDF description",
) -> BoundRegistryAction:
    """Build a UDF action for testing."""
    repo = Repository()
    repo.register_udf(
        fn=sample_udf,
        name="sample_udf",
        type="udf",
        namespace="test",
        description=description,
        secrets=None,
        args_cls=SampleArgs,
        args_docs={"foo": "Foo argument"},
        rtype=int,
        rtype_adapter=TypeAdapter(int),
        default_title=None,
        display_group=None,
        doc_url=None,
        author="Tracecat",
        deprecated=None,
        include_in_schema=True,
    )
    return repo.get("test.sample_udf")


def build_template_action(
    *,
    template_description: str = "Template action description",
    expects_override: dict[str, ExpectedField] | None = None,
) -> BoundRegistryAction:
    """Build a template action for testing."""
    repo = Repository()
    expects = expects_override or {
        "user_id": ExpectedField(type="int", description="User identifier"),
        "message": ExpectedField(type="str", description="Message to send"),
    }
    template_def = TemplateActionDefinition(
        name="send_message",
        namespace="templates",
        title="Send Message",
        description=template_description,
        display_group="Messaging",
        doc_url="https://example.com",
        author="Tracecat",
        deprecated=None,
        secrets=None,
        expects=expects,
        steps=[
            ActionStep(
                ref="first",
                action="test.sample_udf",
                args={"foo": 1},
            )
        ],
        returns="result",
    )
    template_action = TemplateAction(type="action", definition=template_def)
    repo.register_udf(
        fn=sample_udf,
        name="sample_udf",
        type="udf",
        namespace="test",
        description="Sample UDF description",
        secrets=None,
        args_cls=SampleArgs,
        args_docs={"foo": "Foo argument"},
        rtype=int,
        rtype_adapter=TypeAdapter(int),
        default_title=None,
        display_group=None,
        doc_url=None,
        author="Tracecat",
        deprecated=None,
        include_in_schema=True,
    )
    repo.register_template_action(template_action)
    return repo.get("templates.send_message")


# =============================================================================
# Tool Dataclass Tests
# =============================================================================


class TestToolDataclass:
    """Tests for the harness-agnostic Tool dataclass."""

    def test_tool_creation_with_required_fields(self):
        """Test Tool creation with required fields."""
        tool = Tool(
            name="core.http_request",
            description="Make an HTTP request",
            parameters_json_schema={"type": "object", "properties": {}},
        )

        assert tool.name == "core.http_request"
        assert tool.description == "Make an HTTP request"
        assert tool.parameters_json_schema == {"type": "object", "properties": {}}
        assert tool.requires_approval is False

    def test_tool_creation_with_approval(self):
        """Test Tool creation with requires_approval=True."""
        tool = Tool(
            name="core.cases.delete_case",
            description="Delete a case permanently",
            parameters_json_schema={"type": "object"},
            requires_approval=True,
        )

        assert tool.name == "core.cases.delete_case"
        assert tool.requires_approval is True

    def test_tool_uses_canonical_name_with_dots(self):
        """Test that Tool stores canonical names with dots (not underscores)."""
        tool = Tool(
            name="tools.slack.post_message",
            description="Post a message to Slack",
            parameters_json_schema={},
        )

        assert "." in tool.name
        assert "__" not in tool.name

    def test_tool_with_complex_schema(self):
        """Test Tool with a complex JSON schema."""
        schema = {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to request"},
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE"],
                },
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["url", "method"],
        }

        tool = Tool(
            name="core.http_request",
            description="Make an HTTP request",
            parameters_json_schema=schema,
        )

        assert tool.parameters_json_schema == schema
        assert "required" in tool.parameters_json_schema


# =============================================================================
# ToolExecutionError Tests
# =============================================================================


class TestToolExecutionError:
    """Tests for ToolExecutionError exception."""

    def test_error_with_message(self):
        """Test ToolExecutionError with a message."""
        error = ToolExecutionError("Action execution failed: timeout")

        assert str(error) == "Action execution failed: timeout"
        assert isinstance(error, Exception)

    def test_error_can_be_raised_and_caught(self):
        """Test that ToolExecutionError can be raised and caught."""
        with pytest.raises(ToolExecutionError) as exc_info:
            raise ToolExecutionError("Something went wrong")

        assert "Something went wrong" in str(exc_info.value)

    def test_error_inheritance(self):
        """Test that ToolExecutionError inherits from Exception."""
        error = ToolExecutionError("test")
        assert isinstance(error, Exception)


# =============================================================================
# denormalize_tool_name Tests
# =============================================================================


class TestDenormalizeToolName:
    """Tests for denormalize_tool_name utility."""

    def test_converts_double_underscores_to_dots(self):
        """Test conversion of MCP format to canonical format."""
        result = denormalize_tool_name("core__http_request")
        assert result == "core.http_request"

    def test_handles_multiple_namespaces(self):
        """Test conversion with multiple namespace levels."""
        result = denormalize_tool_name("tools__slack__post_message")
        assert result == "tools.slack.post_message"

    def test_handles_canonical_name_unchanged(self):
        """Test that canonical names without __ pass through."""
        result = denormalize_tool_name("core.http_request")
        assert result == "core.http_request"

    def test_preserves_single_underscores(self):
        """Test that single underscores are preserved."""
        result = denormalize_tool_name("core__http_request")
        assert result == "core.http_request"
        # The action name part still has underscore
        assert "_" in result

    def test_complex_nested_namespace(self):
        """Test deeply nested namespaces."""
        result = denormalize_tool_name("tools__integrations__aws__s3__list_buckets")
        assert result == "tools.integrations.aws.s3.list_buckets"


# =============================================================================
# _extract_action_metadata Tests
# =============================================================================


class TestExtractActionMetadata:
    """Tests for _extract_action_metadata helper."""

    def test_udf_returns_description_and_args_model(self):
        """Test UDF extraction returns description and args class."""
        bound_action = build_udf_action()
        description, model_cls = _extract_action_metadata(bound_action)

        assert description == "Sample UDF description"
        assert model_cls is SampleArgs

    def test_template_uses_template_description(self):
        """Test template action uses template definition description."""
        bound_action = build_template_action()
        description, model_cls = _extract_action_metadata(bound_action)

        assert description == "Template action description"
        assert issubclass(model_cls, BaseModel)
        assert set(model_cls.model_fields) == {"user_id", "message"}
        assert model_cls.model_fields["user_id"].annotation is int

    def test_template_falls_back_to_bound_description(self):
        """Test template falls back to bound action description when template is empty."""
        bound_action = build_template_action(template_description="")
        bound_action.description = "Fallback description"
        assert bound_action.template_action is not None
        bound_action.template_action.definition.description = ""

        description, _ = _extract_action_metadata(bound_action)
        assert description == "Fallback description"

    def test_template_without_template_action_raises(self):
        """Test that template type without template_action raises ValueError."""
        bound_action = BoundRegistryAction(
            fn=sample_udf,
            name="template_without_body",
            namespace="tests",
            description="Template missing body",
            type="template",
            origin="unit-test",
            secrets=None,
            args_cls=SampleArgs,
            args_docs={"foo": "Foo argument"},
            rtype_cls=int,
            rtype_adapter=TypeAdapter(int),
            default_title=None,
            display_group=None,
            doc_url=None,
            author="Tracecat",
            deprecated=None,
            template_action=None,
            include_in_schema=True,
        )

        with pytest.raises(ValueError, match="Template action is not set"):
            _extract_action_metadata(bound_action)

    def test_template_generates_model_from_expects(self):
        """Test that template expects are converted to a Pydantic model."""
        expects = {
            "name": ExpectedField(type="str", description="User name"),
            "age": ExpectedField(type="int", description="User age"),
            "active": ExpectedField(type="bool", description="Is active"),
        }
        bound_action = build_template_action(expects_override=expects)

        _, model_cls = _extract_action_metadata(bound_action)

        assert set(model_cls.model_fields) == {"name", "age", "active"}
        assert model_cls.model_fields["name"].annotation is str
        assert model_cls.model_fields["age"].annotation is int
        assert model_cls.model_fields["active"].annotation is bool
