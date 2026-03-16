"""Tests for LiteLLM monkeypatches (tool_call_id truncation)."""

from __future__ import annotations

from tracecat.agent.litellm_compat import (
    OPENAI_MAX_TOOL_CALL_ID_LENGTH,
    _truncate_tool_call_ids_in_messages,
    truncate_tool_call_id,
)


class TestTruncateToolCallId:
    def test_short_id_unchanged(self):
        """IDs within the 40-char limit should pass through unchanged."""
        short_id = "call_abc123"
        assert truncate_tool_call_id(short_id) == short_id

    def test_exact_limit_unchanged(self):
        """IDs exactly at the 40-char limit should pass through unchanged."""
        exact_id = "a" * OPENAI_MAX_TOOL_CALL_ID_LENGTH
        assert truncate_tool_call_id(exact_id) == exact_id

    def test_long_id_truncated(self):
        """IDs exceeding 40 chars should be truncated with hash suffix."""
        long_id = "toolu_01ABC" + "x" * 110  # ~121 chars, like Anthropic IDs
        result = truncate_tool_call_id(long_id)
        assert len(result) == OPENAI_MAX_TOOL_CALL_ID_LENGTH
        assert result.startswith("toolu_01ABC")
        assert "_" in result

    def test_truncation_is_deterministic(self):
        """Same input should always produce the same truncated ID."""
        long_id = "toolu_" + "a" * 120
        assert truncate_tool_call_id(long_id) == truncate_tool_call_id(long_id)

    def test_different_ids_produce_different_truncations(self):
        """Different long IDs should (almost certainly) produce different hashes."""
        id_a = "toolu_01" + "a" * 110
        id_b = "toolu_01" + "b" * 110
        assert truncate_tool_call_id(id_a) != truncate_tool_call_id(id_b)


class TestTruncateToolCallIdsInMessages:
    def test_no_tool_calls_is_noop(self):
        """Messages without tool calls should be unchanged."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        _truncate_tool_call_ids_in_messages(messages)
        assert messages[0]["content"] == "hello"
        assert messages[1]["content"] == "hi"

    def test_short_ids_unchanged(self):
        """Short tool_call IDs should not be modified."""
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_short",
                        "type": "function",
                        "function": {"name": "foo"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_short", "content": "result"},
        ]
        _truncate_tool_call_ids_in_messages(messages)
        assert messages[0]["tool_calls"][0]["id"] == "call_short"
        assert messages[1]["tool_call_id"] == "call_short"

    def test_long_ids_truncated_and_paired(self):
        """Long tool_call IDs should be truncated consistently in both messages."""
        long_id = "toolu_01ABC" + "x" * 110
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": long_id, "type": "function", "function": {"name": "foo"}},
                ],
            },
            {"role": "tool", "tool_call_id": long_id, "content": "result"},
        ]
        _truncate_tool_call_ids_in_messages(messages)

        truncated_id = messages[0]["tool_calls"][0]["id"]
        assert len(truncated_id) == OPENAI_MAX_TOOL_CALL_ID_LENGTH
        assert truncated_id != long_id
        # Tool message ID must match
        assert messages[1]["tool_call_id"] == truncated_id

    def test_multiple_tool_calls_in_single_message(self):
        """Multiple tool calls in one assistant message should all be truncated."""
        id_a = "toolu_" + "a" * 120
        id_b = "toolu_" + "b" * 120
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": id_a, "type": "function", "function": {"name": "foo"}},
                    {"id": id_b, "type": "function", "function": {"name": "bar"}},
                ],
            },
            {"role": "tool", "tool_call_id": id_a, "content": "result_a"},
            {"role": "tool", "tool_call_id": id_b, "content": "result_b"},
        ]
        _truncate_tool_call_ids_in_messages(messages)

        truncated_a = messages[0]["tool_calls"][0]["id"]
        truncated_b = messages[0]["tool_calls"][1]["id"]
        assert len(truncated_a) == OPENAI_MAX_TOOL_CALL_ID_LENGTH
        assert len(truncated_b) == OPENAI_MAX_TOOL_CALL_ID_LENGTH
        assert truncated_a != truncated_b
        assert messages[1]["tool_call_id"] == truncated_a
        assert messages[2]["tool_call_id"] == truncated_b

    def test_mixed_short_and_long_ids(self):
        """Only long IDs should be truncated; short ones left alone."""
        short_id = "call_short"
        long_id = "toolu_" + "x" * 120
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": short_id, "type": "function", "function": {"name": "foo"}},
                    {"id": long_id, "type": "function", "function": {"name": "bar"}},
                ],
            },
            {"role": "tool", "tool_call_id": short_id, "content": "result_short"},
            {"role": "tool", "tool_call_id": long_id, "content": "result_long"},
        ]
        _truncate_tool_call_ids_in_messages(messages)

        assert messages[0]["tool_calls"][0]["id"] == short_id
        assert len(messages[0]["tool_calls"][1]["id"]) == OPENAI_MAX_TOOL_CALL_ID_LENGTH
        assert messages[1]["tool_call_id"] == short_id
        assert messages[2]["tool_call_id"] == messages[0]["tool_calls"][1]["id"]
