"""Tests for the system-prompt cascade resolver.

Covers the action > source > default precedence rules and the cumulative
append behaviour exposed by ``resolve_system_prompt_overrides``.
"""

from __future__ import annotations

import pytest

from tracecat.agent.provider.cascade import (
    ResolvedSystemPromptOverrides,
    resolve_system_prompt_overrides,
)


class TestReplaceCascade:
    """Action ``replace`` wins over source ``replace`` wins over default."""

    def test_no_overrides_returns_default(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace=None,
            source_append=None,
            action_replace=None,
            action_append=None,
        )
        assert result.replace is None
        assert result.replace_source == "default"

    def test_source_replace_only(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace="You are an Ollama-backed assistant.",
            source_append=None,
            action_replace=None,
            action_append=None,
        )
        assert result.replace == "You are an Ollama-backed assistant."
        assert result.replace_source == "source"

    def test_action_replace_only(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace=None,
            source_append=None,
            action_replace="You are Qwen by Alibaba.",
            action_append=None,
        )
        assert result.replace == "You are Qwen by Alibaba."
        assert result.replace_source == "action"

    def test_action_replace_wins_over_source_replace(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace="source baseline",
            source_append=None,
            action_replace="action baseline",
            action_append=None,
        )
        assert result.replace == "action baseline"
        assert result.replace_source == "action"

    def test_action_empty_string_replace_is_honoured(self) -> None:
        """An empty string is a deliberate "no baseline" override —
        not the same as ``None``. The cascade must propagate it."""
        result = resolve_system_prompt_overrides(
            source_replace="source baseline",
            source_append=None,
            action_replace="",
            action_append=None,
        )
        assert result.replace == ""
        assert result.replace_source == "action"


class TestAppendCascade:
    """Source and action ``append`` contributions cumulate in order."""

    def test_no_appends(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace=None,
            source_append=None,
            action_replace=None,
            action_append=None,
        )
        assert result.append is None
        assert result.append_count == 0

    def test_source_append_only(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace=None,
            source_append="Always reply in JSON.",
            action_replace=None,
            action_append=None,
        )
        assert result.append == "Always reply in JSON."
        assert result.append_count == 1

    def test_action_append_only(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace=None,
            source_append=None,
            action_replace=None,
            action_append="Be concise.",
        )
        assert result.append == "Be concise."
        assert result.append_count == 1

    def test_source_then_action_append_concatenate(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace=None,
            source_append="Always reply in JSON.",
            action_replace=None,
            action_append="Be concise.",
        )
        assert result.append == "Always reply in JSON.\n\nBe concise."
        assert result.append_count == 2

    def test_empty_string_append_is_skipped(self) -> None:
        """An empty append contributes nothing — falsy strings are
        treated as absent so users can clear them via expressions."""
        result = resolve_system_prompt_overrides(
            source_replace=None,
            source_append="",
            action_replace=None,
            action_append="kept",
        )
        assert result.append == "kept"
        assert result.append_count == 1


class TestReplaceAndAppendCombined:
    """Replace and append work independently and compose freely."""

    def test_replace_and_append_combined(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace="source baseline",
            source_append="source notes",
            action_replace="action baseline",
            action_append="action notes",
        )
        assert result.replace == "action baseline"
        assert result.replace_source == "action"
        assert result.append == "source notes\n\naction notes"
        assert result.append_count == 2


class TestResultShape:
    """The return type is a frozen dataclass (immutable, hashable)."""

    def test_result_is_immutable(self) -> None:
        result = resolve_system_prompt_overrides(
            source_replace=None,
            source_append=None,
            action_replace=None,
            action_append=None,
        )
        assert isinstance(result, ResolvedSystemPromptOverrides)
        with pytest.raises(AttributeError):
            result.replace = "should not work"  # type: ignore[misc]
