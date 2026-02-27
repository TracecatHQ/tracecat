"""Tests for MCP tool naming utilities."""

from tracecat.agent.mcp.user_client import UserMCPClient
from tracecat.agent.mcp.utils import (
    mcp_tool_name_to_canonical,
    normalize_mcp_tool_name,
)


def test_parse_user_mcp_tool_name_canonical_splits_on_first_dot() -> None:
    parsed = UserMCPClient.parse_user_mcp_tool_name("mcp.Linear.issues.list")
    assert parsed == ("Linear", "issues.list")


def test_parse_user_mcp_tool_name_rejects_registry_tools() -> None:
    assert (
        UserMCPClient.parse_user_mcp_tool_name(
            "mcp.tracecat-registry.core.cases.list_cases"
        )
        is None
    )
    assert (
        UserMCPClient.parse_user_mcp_tool_name(
            "mcp__tracecat-registry__core__cases__list_cases"
        )
        is None
    )


def test_mcp_tool_name_to_canonical_converts_tool_separators() -> None:
    canonical = mcp_tool_name_to_canonical("mcp__Linear__issues__list")
    assert canonical == "mcp.Linear.issues.list"


def test_canonical_matches_runtime_wrapped_normalization() -> None:
    discovered_name = "mcp__Linear__issues__list"
    wrapped_name = f"mcp__tracecat-registry__{discovered_name}"
    assert mcp_tool_name_to_canonical(discovered_name) == normalize_mcp_tool_name(
        wrapped_name
    )
