"""Tests for the agent ``actions`` list length cap.

Covers the shared ``validate_actions_length`` validator plus its wiring into
every Pydantic schema that accepts an ``actions`` field, so that exceeding
``TRACECAT__AGENT_MAX_TOOLS`` surfaces as a schema ``ValidationError`` rather
than as a late runtime failure inside the agent worker.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tracecat_ee.agent.schemas import AgentActionArgs, PresetAgentActionArgs

from tracecat import config
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetExecutionConfig,
    AgentPresetExecutionConfigWrite,
    AgentPresetUpdate,
)
from tracecat.agent.schemas import AgentConfigSchema
from tracecat.agent.validation import validate_actions_length


@pytest.fixture
def small_cap(monkeypatch: pytest.MonkeyPatch) -> int:
    """Lower the tool cap so tests don't need huge lists."""
    cap = 3
    monkeypatch.setattr(config, "TRACECAT__AGENT_MAX_TOOLS", cap)
    return cap


def _actions(n: int) -> list[str]:
    return [f"tools.pkg.action_{i}" for i in range(n)]


class TestValidateActionsLength:
    """Pure tests for the shared validator function."""

    def test_none_passes_through(self, small_cap: int) -> None:
        assert validate_actions_length(None) is None

    def test_empty_list_passes(self, small_cap: int) -> None:
        assert validate_actions_length([]) == []

    def test_at_cap_passes(self, small_cap: int) -> None:
        value = _actions(small_cap)
        assert validate_actions_length(value) == value

    def test_over_cap_raises(self, small_cap: int) -> None:
        with pytest.raises(ValueError, match="at most 3 actions, got 4"):
            validate_actions_length(_actions(small_cap + 1))

    def test_error_message_mentions_env_var(self, small_cap: int) -> None:
        with pytest.raises(ValueError, match="TRACECAT__AGENT_MAX_TOOLS"):
            validate_actions_length(_actions(small_cap + 1))

    def test_zero_cap_disables_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``max_tools == 0`` means "no limit" — keep parity with build_agent_tools."""
        monkeypatch.setattr(config, "TRACECAT__AGENT_MAX_TOOLS", 0)
        value = _actions(500)
        assert validate_actions_length(value) == value


class TestAgentActionArgsCap:
    """``ai.agent`` action args reject oversized action lists."""

    def test_accepts_list_at_cap(self, small_cap: int) -> None:
        args = AgentActionArgs(
            user_prompt="hi",
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=_actions(small_cap),
        )
        assert args.actions is not None
        assert len(args.actions) == small_cap

    def test_rejects_list_over_cap(self, small_cap: int) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AgentActionArgs(
                user_prompt="hi",
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=_actions(small_cap + 1),
            )
        assert "at most 3 actions" in str(exc_info.value)


class TestPresetAgentActionArgsCap:
    """``ai.preset_agent`` action args reject oversized action lists."""

    def test_rejects_list_over_cap(self, small_cap: int) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PresetAgentActionArgs(
                preset="triage",
                user_prompt="hi",
                actions=_actions(small_cap + 1),
            )
        assert "at most 3 actions" in str(exc_info.value)


class TestAgentConfigSchemaCap:
    """Internal ``/agent/run`` config request rejects oversized action lists."""

    def test_rejects_list_over_cap(self, small_cap: int) -> None:
        with pytest.raises(ValidationError):
            AgentConfigSchema(
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=_actions(small_cap + 1),
            )


class TestPresetSchemasCap:
    """Preset write/read schemas reject oversized action lists."""

    def test_execution_config_rejects_over_cap(self, small_cap: int) -> None:
        with pytest.raises(ValidationError):
            AgentPresetExecutionConfig(
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=_actions(small_cap + 1),
            )

    def test_execution_config_write_rejects_over_cap(self, small_cap: int) -> None:
        with pytest.raises(ValidationError):
            AgentPresetExecutionConfigWrite(
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=_actions(small_cap + 1),
            )

    def test_preset_create_rejects_over_cap(self, small_cap: int) -> None:
        with pytest.raises(ValidationError):
            AgentPresetCreate(
                name="Triage",
                slug="triage",
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=_actions(small_cap + 1),
            )

    def test_preset_update_rejects_over_cap(self, small_cap: int) -> None:
        with pytest.raises(ValidationError):
            AgentPresetUpdate(actions=_actions(small_cap + 1))
