"""Visibility rules for persisted agent session messages."""

from __future__ import annotations

from tracecat.chat.enums import MessageKind


def test_internal_kind_value_matches_expected_string() -> None:
    """The internal kind used to tag continuation prompts must stay stable."""
    assert MessageKind.INTERNAL.value == "internal"
