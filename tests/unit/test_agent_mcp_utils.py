from __future__ import annotations

import pytest

from tracecat.agent.mcp.utils import (
    canonical_mcp_tool_name,
    normalize_mcp_tool_name,
)


def test_normalize_mcp_tool_name_canonical_registry_prefix() -> None:
    assert (
        normalize_mcp_tool_name("mcp__tracecat-registry__tools__slack__post_message")
        == "tools.slack.post_message"
    )


def test_normalize_mcp_tool_name_legacy_registry_prefix() -> None:
    assert (
        normalize_mcp_tool_name("mcp__tracecat_registry__tools__slack__post_message")
        == "tools.slack.post_message"
    )


def test_normalize_mcp_tool_name_canonical_registry_user_mcp_prefix() -> None:
    assert (
        normalize_mcp_tool_name("mcp__tracecat-registry__mcp__Linear__list_issues")
        == "mcp.Linear.list_issues"
    )


def test_normalize_mcp_tool_name_legacy_registry_user_mcp_prefix() -> None:
    assert (
        normalize_mcp_tool_name("mcp__tracecat_registry__mcp__Linear__list_issues")
        == "mcp.Linear.list_issues"
    )


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("core.http_request", "core.http_request"),
        ("core__http_request", "core.http_request"),
        ("mcp__tracecat-registry__core__http_request", "core.http_request"),
        ("mcp.tracecat_registry.core.http_request", "core.http_request"),
        ("internal__builder__update_preset", "internal.builder.update_preset"),
        ("mcp__Jira__deleteIssue", "mcp__Jira__deleteIssue"),
        ("mcp__tracecat-registry__mcp__Jira__deleteIssue", "mcp__Jira__deleteIssue"),
    ],
)
def test_canonical_mcp_tool_name_matches_execution_names(
    tool_name: str, expected: str
) -> None:
    assert canonical_mcp_tool_name(tool_name) == expected
