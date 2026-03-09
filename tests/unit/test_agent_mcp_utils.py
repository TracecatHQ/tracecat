from __future__ import annotations

from tracecat.agent.mcp.utils import (
    decode_legacy_tool_name_to_canonical,
    decode_sdk_tool_name_to_canonical,
    encode_canonical_tool_name_to_sdk,
    parse_canonical_user_mcp_tool_name,
)


def test_decode_sdk_tool_name_to_canonical_for_registry_tool() -> None:
    assert (
        decode_sdk_tool_name_to_canonical("mcp__tracecat-registry__core__http_request")
        == "core.http_request"
    )


def test_decode_sdk_tool_name_to_canonical_for_user_mcp_tool() -> None:
    assert (
        decode_sdk_tool_name_to_canonical("mcp__tracecat-registry__mcp__jira__getIssue")
        == "mcp.jira.getIssue"
    )


def test_decode_legacy_tool_name_to_canonical_supports_registry_alias() -> None:
    assert (
        decode_legacy_tool_name_to_canonical("mcp.tracecat_registry.core.http_request")
        == "core.http_request"
    )


def test_encode_canonical_tool_name_to_sdk_for_registry_tool() -> None:
    assert (
        encode_canonical_tool_name_to_sdk("core.http_request") == "core__http_request"
    )


def test_encode_canonical_tool_name_to_sdk_for_user_mcp_tool() -> None:
    assert (
        encode_canonical_tool_name_to_sdk("mcp.jira.getIssue") == "mcp__jira__getIssue"
    )


def test_parse_canonical_user_mcp_tool_name() -> None:
    assert parse_canonical_user_mcp_tool_name("mcp.jira.getIssue") == (
        "jira",
        "getIssue",
    )
