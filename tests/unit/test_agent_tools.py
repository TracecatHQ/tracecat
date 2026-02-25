"""Unit tests for agent tools module.

Tests the harness-agnostic tool creation and execution:
- Tool dataclass construction
- ToolExecutionError exception
- denormalize_tool_name utility
- create_tool_from_registry
"""

import pytest

from tracecat.agent.tools import (
    ToolExecutionError,
    denormalize_tool_name,
)
from tracecat.agent.types import Tool

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
