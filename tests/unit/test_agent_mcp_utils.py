from __future__ import annotations

from tracecat.agent.mcp.utils import (
    decode_legacy_tool_name_to_canonical,
    decode_sdk_tool_name_to_canonical,
    encode_canonical_tool_name_to_sdk,
    is_reserved_mcp_server_name,
    is_tracecat_sdk_tool_name,
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


def test_decode_sdk_tool_name_to_canonical_for_tracecat_registry_wire_name() -> None:
    assert (
        decode_sdk_tool_name_to_canonical("tools__slack__post_message")
        == "tools.slack.post_message"
    )


def test_decode_sdk_tool_name_to_canonical_preserves_raw_stdio_tool_name() -> None:
    assert decode_sdk_tool_name_to_canonical("foo__bar") == "foo__bar"


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


def test_is_reserved_mcp_server_name() -> None:
    assert is_reserved_mcp_server_name("tracecat-registry") is True
    assert is_reserved_mcp_server_name("tracecat_registry") is True
    assert is_reserved_mcp_server_name("jira") is False


def test_is_tracecat_sdk_tool_name() -> None:
    assert is_tracecat_sdk_tool_name("core__http_request") is True
    assert is_tracecat_sdk_tool_name("tools__slack__post_message") is True
    assert is_tracecat_sdk_tool_name("foo__bar") is False
