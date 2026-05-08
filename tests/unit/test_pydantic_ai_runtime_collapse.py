"""Tests for pydantic-ai runtime ``_collapse_instructions``.

The pydantic-ai harness exposes a single ``instructions`` kwarg, so the
runtime must collapse the three Tracecat contributors (replace,
legacy instructions, append) into one string while preserving cascade
semantics.
"""

from __future__ import annotations

from tracecat.agent.runtime.pydantic_ai.runtime import _collapse_instructions


class TestCollapseInstructions:
    def test_all_none_returns_none(self) -> None:
        assert (
            _collapse_instructions(replace=None, instructions=None, append=None) is None
        )

    def test_legacy_instructions_only(self) -> None:
        result = _collapse_instructions(
            replace=None, instructions="Be concise.", append=None
        )
        assert result == "Be concise."

    def test_replace_replaces_legacy_instructions(self) -> None:
        result = _collapse_instructions(
            replace="You are Qwen.",
            instructions="ignored legacy text",
            append=None,
        )
        assert result == "You are Qwen."

    def test_replace_empty_string_drops_legacy(self) -> None:
        """Empty replace = explicit "no baseline". Legacy instructions
        must be dropped (consistent with the Claude Code runtime)."""
        result = _collapse_instructions(
            replace="", instructions="ignored legacy text", append=None
        )
        assert result == ""

    def test_replace_empty_string_with_append(self) -> None:
        """When replace is empty and append is set, the result keeps
        the explicit empty baseline followed by the append text."""
        result = _collapse_instructions(
            replace="", instructions=None, append="Be terse."
        )
        assert result == "\n\nBe terse."

    def test_append_after_legacy_instructions(self) -> None:
        result = _collapse_instructions(
            replace=None, instructions="Identity.", append="Extra."
        )
        assert result == "Identity.\n\nExtra."

    def test_append_after_replace(self) -> None:
        result = _collapse_instructions(
            replace="You are Qwen.", instructions=None, append="Extra."
        )
        assert result == "You are Qwen.\n\nExtra."

    def test_append_only(self) -> None:
        result = _collapse_instructions(
            replace=None, instructions=None, append="Just append."
        )
        assert result == "Just append."

    def test_empty_string_append_is_skipped(self) -> None:
        result = _collapse_instructions(replace=None, instructions="kept", append="")
        assert result == "kept"
