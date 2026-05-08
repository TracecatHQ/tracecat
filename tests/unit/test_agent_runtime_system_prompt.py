"""Tests for ClaudeAgentRuntime._build_system_prompt.

Covers the matrix of:
- legacy ``instructions`` only
- ``output_type`` structured-output instruction injection
- ``system_prompt_replace`` overriding the Tracecat baseline
- ``system_prompt_append`` cumulating after the baseline / replacement
- the legacy ``instructions`` field appended last (kept for backward
  compatibility)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tracecat.agent.runtime.claude_code.runtime import ClaudeAgentRuntime

DEFAULT_BASELINE = (
    "If asked about your identity, you are a Tracecat automation assistant."
)
STRUCTURED_OUTPUT_MARKER = "You MUST produce structured output"


@pytest.fixture
def runtime() -> ClaudeAgentRuntime:
    """Minimal ClaudeAgentRuntime fixture for pure-method testing."""
    event_writer = MagicMock()
    return ClaudeAgentRuntime(event_writer, transport_factory=lambda _: MagicMock())


class TestBaseline:
    """Without any override the existing Tracecat baseline is preserved."""

    def test_no_args_returns_baseline(self, runtime: ClaudeAgentRuntime) -> None:
        result = runtime._build_system_prompt(instructions=None, output_type=None)
        assert result == DEFAULT_BASELINE

    def test_baseline_plus_legacy_instructions(
        self, runtime: ClaudeAgentRuntime
    ) -> None:
        result = runtime._build_system_prompt(
            instructions="Be terse.", output_type=None
        )
        assert result == f"{DEFAULT_BASELINE}\n\nBe terse."

    def test_baseline_plus_output_type_marker(
        self, runtime: ClaudeAgentRuntime
    ) -> None:
        result = runtime._build_system_prompt(instructions=None, output_type="int")
        assert result.startswith(DEFAULT_BASELINE)
        assert STRUCTURED_OUTPUT_MARKER in result


class TestSystemPromptReplace:
    """``system_prompt_replace`` swaps the entire baseline."""

    def test_replace_drops_default_baseline(self, runtime: ClaudeAgentRuntime) -> None:
        result = runtime._build_system_prompt(
            instructions=None,
            output_type=None,
            system_prompt_replace="You are Qwen by Alibaba.",
        )
        assert result == "You are Qwen by Alibaba."
        assert DEFAULT_BASELINE not in result

    def test_replace_with_empty_string_yields_no_baseline(
        self, runtime: ClaudeAgentRuntime
    ) -> None:
        """An empty string is a deliberate "no baseline" override.
        It must NOT silently fall back to the Tracecat default."""
        result = runtime._build_system_prompt(
            instructions=None,
            output_type=None,
            system_prompt_replace="",
        )
        assert result == ""
        assert DEFAULT_BASELINE not in result

    def test_replace_keeps_output_type_marker(
        self, runtime: ClaudeAgentRuntime
    ) -> None:
        """``output_type`` instructions still kick in even when the
        baseline is replaced — they describe a runtime contract, not an
        identity."""
        result = runtime._build_system_prompt(
            instructions=None,
            output_type="int",
            system_prompt_replace="You are Qwen.",
        )
        assert result.startswith("You are Qwen.")
        assert STRUCTURED_OUTPUT_MARKER in result


class TestSystemPromptAppend:
    """``system_prompt_append`` adds text after the resolved baseline."""

    def test_append_after_default_baseline(self, runtime: ClaudeAgentRuntime) -> None:
        result = runtime._build_system_prompt(
            instructions=None,
            output_type=None,
            system_prompt_append="Always respond in French.",
        )
        assert result == f"{DEFAULT_BASELINE}\n\nAlways respond in French."

    def test_append_after_replace(self, runtime: ClaudeAgentRuntime) -> None:
        result = runtime._build_system_prompt(
            instructions=None,
            output_type=None,
            system_prompt_replace="You are Qwen.",
            system_prompt_append="Be concise.",
        )
        assert result == "You are Qwen.\n\nBe concise."


class TestLegacyInstructionsCompatibility:
    """The legacy per-action ``instructions`` field is appended last."""

    def test_legacy_instructions_appended_last_after_replace_and_append(
        self, runtime: ClaudeAgentRuntime
    ) -> None:
        result = runtime._build_system_prompt(
            instructions="Legacy text.",
            output_type=None,
            system_prompt_replace="You are Qwen.",
            system_prompt_append="Be concise.",
        )
        assert result == "You are Qwen.\n\nBe concise.\n\nLegacy text."

    def test_legacy_instructions_alone_keeps_default_baseline(
        self, runtime: ClaudeAgentRuntime
    ) -> None:
        result = runtime._build_system_prompt(
            instructions="Legacy text.", output_type=None
        )
        assert result == f"{DEFAULT_BASELINE}\n\nLegacy text."
