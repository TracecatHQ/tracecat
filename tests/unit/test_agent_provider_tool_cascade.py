"""Tests for the tools allowlist cascade resolver.

Covers the action > source > default precedence rule and the
tristate ``None`` / ``[]`` / ``[...]`` semantics surfaced by
``resolve_allowed_tools``.
"""

from __future__ import annotations

import pytest

from tracecat.agent.provider.tool_cascade import (
    ResolvedAllowedTools,
    resolve_allowed_tools,
)


class TestCascadePrecedence:
    """Action > source > default."""

    def test_no_overrides_returns_default(self) -> None:
        result = resolve_allowed_tools(source_value=None, action_value=None)
        assert result.allowed_tools is None
        assert result.source == "default"

    def test_source_only(self) -> None:
        result = resolve_allowed_tools(source_value=["Read", "Grep"], action_value=None)
        assert result.allowed_tools == ["Read", "Grep"]
        assert result.source == "source"

    def test_action_only(self) -> None:
        result = resolve_allowed_tools(source_value=None, action_value=["Bash"])
        assert result.allowed_tools == ["Bash"]
        assert result.source == "action"

    def test_action_overrides_source(self) -> None:
        result = resolve_allowed_tools(source_value=["Read"], action_value=["Bash"])
        assert result.allowed_tools == ["Bash"]
        assert result.source == "action"


class TestEmptyListSemantics:
    """Empty list is a value, not absence."""

    def test_action_empty_list_overrides_source_whitelist(self) -> None:
        """Action ``[]`` (disable all) wins over source whitelist."""
        result = resolve_allowed_tools(source_value=["Read", "Grep"], action_value=[])
        assert result.allowed_tools == []
        assert result.source == "action"

    def test_source_empty_list_kept_when_no_action_override(self) -> None:
        """Source ``[]`` propagates when action is ``None``."""
        result = resolve_allowed_tools(source_value=[], action_value=None)
        assert result.allowed_tools == []
        assert result.source == "source"

    def test_action_whitelist_overrides_source_empty(self) -> None:
        """Action whitelist beats source ``[]``."""
        result = resolve_allowed_tools(source_value=[], action_value=["Read"])
        assert result.allowed_tools == ["Read"]
        assert result.source == "action"


class TestResultShape:
    """Frozen dataclass."""

    def test_result_is_immutable(self) -> None:
        result = resolve_allowed_tools(source_value=None, action_value=None)
        assert isinstance(result, ResolvedAllowedTools)
        with pytest.raises(AttributeError):
            result.allowed_tools = ["Read"]  # type: ignore[misc]
