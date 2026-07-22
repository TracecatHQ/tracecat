from __future__ import annotations

from tracecat.agent.mcp.utils import _agent_action_description, normalize_mcp_tool_name


def test_run_python_agent_description_disallows_registry_imports() -> None:
    description = _agent_action_description(
        "core.script.run_python", "Execute a Python script."
    )

    assert description == (
        "Execute a Python script. Registry imports (`tracecat_registry`) are "
        "unavailable in agent sessions; write plain Python."
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
