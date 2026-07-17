"""Unit tests for the fused durable-agent prepare activity helpers.

Covers the config/binding resolution logic that previously ran as workflow
code (and was exercised through mocked per-step activities in the temporal
tests) and now lives inside ``prepare_agent_turn_activity``.
"""

from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError
from tracecat_ee.agent.prepare import (
    PrepareAgentTurnInput,
    _preserved_agents_binding,
    _resolve_subagents,
    _resolve_turn_config,
    internal_tool_context_for,
)

from tracecat.agent.preset.resolver import ResolvedAgentsRuntimeConfig
from tracecat.agent.session.activities import LoadSessionResult
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import (
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.auth.types import Role


def _role() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "secret:read"}),
    )


def _prepare_input(
    role: Role,
    *,
    config: AgentConfig | None = None,
    preset_slug: str | None = None,
    preset_version: int | None = None,
    agent_preset_version_id: uuid.UUID | None = None,
) -> PrepareAgentTurnInput:
    return PrepareAgentTurnInput(
        role=role,
        session_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
        user_prompt="hello",
        config=config,
        preset_slug=preset_slug,
        preset_version=preset_version,
        agent_preset_version_id=agent_preset_version_id,
        entity_type=AgentSessionEntity.WORKSPACE_CHAT,
        entity_id=uuid.uuid4(),
    )


def _stored_binding() -> ResolvedAgentsConfig:
    return ResolvedAgentsConfig(
        enabled=True,
        subagents=[
            ResolvedAttachedSubagentRef(
                preset="analyst-preset",
                name="analyst",
                preset_id=uuid.uuid4(),
                preset_version_id=uuid.uuid4(),
            )
        ],
    )


class TestPreservedAgentsBinding:
    def test_session_not_found_returns_none(self) -> None:
        assert _preserved_agents_binding(LoadSessionResult(found=False)) is None

    def test_stored_binding_wins(self) -> None:
        binding = _stored_binding()
        load_result = LoadSessionResult(
            found=True, agents_binding=binding, has_resume_state=True
        )
        assert _preserved_agents_binding(load_result) == binding

    def test_resume_state_without_binding_pins_disabled_subagents(self) -> None:
        load_result = LoadSessionResult(found=True, has_resume_state=True)
        assert _preserved_agents_binding(load_result) == ResolvedAgentsConfig()

    def test_fresh_session_returns_none(self) -> None:
        load_result = LoadSessionResult(found=True, has_resume_state=False)
        assert _preserved_agents_binding(load_result) is None


class TestResolveSubagents:
    @pytest.mark.anyio
    async def test_agents_disabled_skips_resolution(self) -> None:
        role = _role()
        config = AgentConfig(model_name="gpt-4o-mini", model_provider="openai")
        with patch(
            "tracecat_ee.agent.prepare.resolve_agents_config_activity",
            AsyncMock(),
        ) as resolve_mock:
            result = await _resolve_subagents(
                _prepare_input(role, config=config),
                config,
                LoadSessionResult(found=False),
            )
        resolve_mock.assert_not_awaited()
        assert result == ResolvedAgentsRuntimeConfig()

    @pytest.mark.anyio
    async def test_resumed_session_pins_stored_binding(self) -> None:
        """A resumed session resolves the stored binding, not the live preset."""
        role = _role()
        binding = _stored_binding()
        stored_ref = binding.subagents[0]
        # The live config points at a different (newer) subagent set; the
        # stored binding must win and pin exact versions.
        config = AgentConfig(model_name="gpt-4o-mini", model_provider="openai")
        resolved = ResolvedAgentsRuntimeConfig(enabled=True)
        with patch(
            "tracecat_ee.agent.prepare.resolve_agents_config_activity",
            AsyncMock(return_value=resolved),
        ) as resolve_mock:
            result = await _resolve_subagents(
                _prepare_input(role, config=config),
                config,
                LoadSessionResult(
                    found=True, agents_binding=binding, has_resume_state=True
                ),
            )
        resolve_mock.assert_awaited_once()
        resolve_input = resolve_mock.await_args.args[0]  # type: ignore[union-attr]
        assert resolve_input.follow_latest_versions is False
        assert resolve_input.agents.enabled is True
        [ref] = resolve_input.agents.subagents
        assert isinstance(ref, ResolvedAttachedSubagentRef)
        assert ref.preset_version_id == stored_ref.preset_version_id
        assert result == resolved

    @pytest.mark.anyio
    async def test_resume_state_without_binding_disables_subagents(self) -> None:
        role = _role()
        agent_config_ctor = cast(Any, AgentConfig)
        config = agent_config_ctor(
            model_name="gpt-4o-mini",
            model_provider="openai",
            agents={"enabled": True},
        )
        with patch(
            "tracecat_ee.agent.prepare.resolve_agents_config_activity",
            AsyncMock(),
        ) as resolve_mock:
            result = await _resolve_subagents(
                _prepare_input(role, config=config),
                config,
                LoadSessionResult(found=True, has_resume_state=True),
            )
        resolve_mock.assert_not_awaited()
        assert result == ResolvedAgentsRuntimeConfig()

    @pytest.mark.anyio
    async def test_enabled_without_subagents_short_circuits(self) -> None:
        role = _role()
        agent_config_ctor = cast(Any, AgentConfig)
        config = agent_config_ctor(
            model_name="gpt-4o-mini",
            model_provider="openai",
            agents={"enabled": True},
        )
        with patch(
            "tracecat_ee.agent.prepare.resolve_agents_config_activity",
            AsyncMock(),
        ) as resolve_mock:
            result = await _resolve_subagents(
                _prepare_input(role, config=config),
                config,
                LoadSessionResult(found=False),
            )
        resolve_mock.assert_not_awaited()
        assert result == ResolvedAgentsRuntimeConfig(enabled=True)


class TestResolveTurnConfig:
    @pytest.mark.anyio
    async def test_missing_config_without_preset_raises(self) -> None:
        with pytest.raises(ApplicationError, match="Config must be provided"):
            await _resolve_turn_config(_prepare_input(_role()))

    @pytest.mark.anyio
    async def test_preset_overrides_layer_actions_and_instructions(self) -> None:
        role = _role()
        preset_config = AgentConfig(
            model_name="claude-3-5-sonnet-20241022",
            model_provider="anthropic",
            instructions="base instructions",
            actions=["core.http_request"],
        )
        override = AgentConfig(
            model_name="gpt-4o-mini",
            model_provider="openai",
            instructions="append this",
            actions=["core.cases.list_cases"],
        )
        pinned_version_id = uuid.uuid4()
        with patch(
            "tracecat_ee.agent.prepare.resolve_agent_preset_config_activity",
            AsyncMock(return_value=agent_config_to_payload(preset_config)),
        ) as resolve_mock:
            config = await _resolve_turn_config(
                _prepare_input(
                    role,
                    config=override,
                    preset_slug="triage-agent",
                    agent_preset_version_id=pinned_version_id,
                )
            )
        # A recorded preset version id wins over slug + version resolution.
        resolve_input = resolve_mock.await_args.args[0]  # type: ignore[union-attr]
        assert resolve_input.preset_version_id == pinned_version_id
        assert resolve_input.preset_slug is None
        # Preset is the base; overrides replace actions and append instructions.
        assert config.model_name == "claude-3-5-sonnet-20241022"
        assert config.actions == ["core.cases.list_cases"]
        assert config.instructions == "base instructions\nappend this"


class TestInternalToolContext:
    def test_builder_sessions_get_context(self) -> None:
        entity_id = uuid.uuid4()
        context = internal_tool_context_for(
            AgentSessionEntity.AGENT_PRESET_BUILDER, entity_id
        )
        assert context is not None
        assert context.preset_id == entity_id
        assert context.entity_type == "agent_preset_builder"

    def test_other_sessions_get_none(self) -> None:
        assert (
            internal_tool_context_for(AgentSessionEntity.WORKSPACE_CHAT, uuid.uuid4())
            is None
        )
