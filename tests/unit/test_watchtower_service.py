from __future__ import annotations

import uuid

from tracecat_ee.watchtower.service import (
    _build_agent_fingerprint,
    _sanitize_error_redacted,
    normalize_agent_identity,
    redact_tool_call_args,
)


def test_normalize_agent_identity_prefers_client_info() -> None:
    agent_type, source, icon = normalize_agent_identity(
        user_agent="Mozilla/5.0",
        client_info={"name": "Claude Code", "version": "1.0.0"},
    )
    assert agent_type == "claude_code"
    assert source == "client_info"
    assert icon == "claude_code"


def test_redact_tool_call_args_does_not_store_raw_strings() -> None:
    result = redact_tool_call_args(
        {
            "workspace_id": "75a17a24-dfd6-45ef-8de0-c15af89f9a72",
            "prompt": "hello world",
            "count": 3,
        }
    )

    args = result["args"]
    assert isinstance(args, dict)
    prompt_meta = args["prompt"]
    assert isinstance(prompt_meta, dict)
    assert prompt_meta["type"] == "str"
    assert prompt_meta["length"] == 11
    assert "hello world" not in str(result)


def test_redact_tool_call_args_summarizes_nested_objects() -> None:
    result = redact_tool_call_args(
        {
            "filters": {
                "status": "active",
                "limit": 25,
            }
        }
    )

    args = result["args"]
    assert isinstance(args, dict)
    filters_meta = args["filters"]
    assert isinstance(filters_meta, dict)
    assert filters_meta["type"] == "object"
    assert filters_meta["key_count"] == 2
    assert filters_meta["keys"] == ["status", "limit"]


def test_sanitize_error_redacted_truncates_long_values() -> None:
    long_message = "x" * 2100
    sanitized = _sanitize_error_redacted(long_message)
    assert sanitized is not None
    assert len(sanitized) == 2000
    assert sanitized.endswith("...")


def test_agent_fingerprint_is_stable_when_client_info_changes() -> None:
    organization_id = uuid.uuid4()
    from_callback = _build_agent_fingerprint(
        organization_id=organization_id,
        auth_client_id="claude-code",
        agent_type="claude_code",
        user_agent="Claude-Code/1.2.3",
        client_info=None,
    )
    from_initialize = _build_agent_fingerprint(
        organization_id=organization_id,
        auth_client_id="claude-code",
        agent_type="claude_code",
        user_agent="Claude-Code/1.2.3",
        client_info={"name": "Claude Code", "version": "1.2.3"},
    )
    assert from_callback == from_initialize
