"""Validation tests for agent preset request schemas."""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.schema import CallableColumnDefault

from tracecat.agent.preset.internal_router import (
    PresetCreateRequest,
    PresetUpdateRequest,
)
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetSkillBindingRead,
    AgentPresetUpdate,
    AgentPresetVersionReadMinimal,
    build_agent_preset_read_minimal,
    build_subagent_eligibility,
)
from tracecat.agent.subagents import AgentSubagentsConfig, ResolvedAgentsConfig
from tracecat.db.models import AgentPreset, AgentPresetVersion


def make_agent_preset(
    *,
    name: str = "Preset",
    slug: str = "preset",
    tool_approvals: dict[str, bool] | None = None,
    agents: dict[str, object] | None = None,
    enable_internet_access: bool = False,
) -> AgentPreset:
    timestamp = datetime(2026, 3, 9, tzinfo=UTC)
    return AgentPreset(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name=name,
        slug=slug,
        description=None,
        model_provider="openai",
        model_name="gpt-4o-mini",
        current_version_id=None,
        tool_approvals=tool_approvals,
        agents=agents or {},
        enable_internet_access=enable_internet_access,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_agent_preset_create_trims_required_fields() -> None:
    payload = AgentPresetCreate(
        name="  Triage preset  ",
        slug="  triage-preset  ",
        description="trim check",
        instructions=None,
        model_name="  gpt-4o-mini  ",
        model_provider="  openai  ",
        base_url=None,
        output_type=None,
        actions=None,
        namespaces=None,
        tool_approvals=None,
        mcp_integrations=None,
        retries=3,
        enable_thinking=True,
    )

    assert payload.name == "Triage preset"
    assert payload.slug == "triage-preset"
    assert payload.model_name == "gpt-4o-mini"
    assert payload.model_provider == "openai"


def test_agent_preset_create_rejects_catalog_without_legacy_model_fields() -> None:
    with pytest.raises(ValidationError):
        AgentPresetCreate.model_validate(
            {
                "name": "Catalog preset",
                "catalog_id": str(uuid.uuid4()),
            }
        )


def test_agent_preset_create_requires_model_fields_without_catalog_id() -> None:
    with pytest.raises(ValidationError):
        AgentPresetCreate.model_validate({"name": "Legacy preset"})


def test_agent_preset_create_accepts_skill_binding_without_version() -> None:
    skill_id = uuid.uuid4()

    payload = AgentPresetCreate.model_validate(
        {
            "name": "Skill-only preset",
            "model_name": "gpt-4o-mini",
            "model_provider": "openai",
            "skills": [{"skill_id": str(skill_id)}],
        }
    )

    assert payload.skills is not None
    assert payload.skills[0].skill_id == skill_id
    assert payload.skills[0].model_dump(mode="json") == {"skill_id": str(skill_id)}


def test_agent_preset_create_ignores_legacy_skill_version_id() -> None:
    skill_id = uuid.uuid4()

    payload = AgentPresetCreate.model_validate(
        {
            "name": "Legacy skill binding",
            "model_name": "gpt-4o-mini",
            "model_provider": "openai",
            "skills": [
                {
                    "skill_id": str(skill_id),
                    "skill_version_id": str(uuid.uuid4()),
                }
            ],
        }
    )

    assert payload.skills is not None
    assert payload.skills[0].model_dump(mode="json") == {"skill_id": str(skill_id)}


def test_agent_preset_skill_binding_read_requires_stored_version() -> None:
    with pytest.raises(ValidationError):
        AgentPresetSkillBindingRead.model_validate(
            {
                "skill_id": str(uuid.uuid4()),
                "skill_name": "triage-skill",
                "skill_version": 1,
            }
        )


@pytest.mark.parametrize(
    ("schema_cls", "kwargs"),
    [
        (
            AgentPresetUpdate,
            {
                "name": "   ",
                "model_name": "gpt-4o-mini",
                "model_provider": "openai",
            },
        ),
        (
            PresetCreateRequest,
            {
                "name": "   ",
                "slug": "triage-preset",
                "model_name": "gpt-4o-mini",
                "model_provider": "openai",
            },
        ),
        (
            PresetUpdateRequest,
            {
                "name": "   ",
            },
        ),
    ],
)
def test_agent_preset_request_schemas_reject_blank_trimmed_values(
    schema_cls: type[BaseModel],
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        schema_cls.model_validate(kwargs)


@pytest.mark.parametrize("schema_cls", [PresetCreateRequest, PresetUpdateRequest])
def test_internal_agent_preset_request_schemas_reject_invalid_catalog_id(
    schema_cls: type[BaseModel],
) -> None:
    with pytest.raises(ValidationError):
        schema_cls.model_validate(
            {
                "name": "Triage preset",
                "catalog_id": "not-a-uuid",
            }
        )


def test_agent_preset_read_schema_accepts_legacy_whitespace_model_fields() -> None:
    payload = AgentPresetRead.model_validate(
        {
            "id": "522b4d28-ae2b-4705-bb53-c3aa9071fe16",
            "workspace_id": "6b2bb4d8-8461-486d-b4ca-e10a5a19d2f2",
            "name": "Legacy preset",
            "slug": "legacy-preset",
            "description": None,
            "instructions": None,
            "model_name": "   ",
            "model_provider": "   ",
            "base_url": None,
            "output_type": None,
            "actions": None,
            "namespaces": None,
            "tool_approvals": None,
            "mcp_integrations": None,
            "retries": 3,
            "enable_thinking": True,
            "enable_internet_access": False,
            "current_version_id": None,
            "created_at": "2026-03-09T00:00:00Z",
            "updated_at": "2026-03-09T00:00:00Z",
        }
    )

    assert payload.model_name == "   "
    assert payload.model_provider == "   "
    assert payload.enable_thinking is True


def test_agent_preset_read_minimal_exposes_capabilities() -> None:
    payload = build_agent_preset_read_minimal(
        make_agent_preset(
            name="Approval preset",
            slug="approval-preset",
            tool_approvals={
                "core.http_request": False,
                "core.cases.create_case": True,
            },
            enable_internet_access=True,
        )
    )

    dumped = payload.model_dump(mode="json")
    assert dumped["capabilities"] == ["approvals", "internet_access"]
    assert dumped["current_version_subagent_eligibility"] == {
        "eligible": False,
        "reasons": ["tool_approvals"],
        "message": (
            "This version requires manual approvals, which are not supported for "
            "preset subagents yet."
        ),
    }
    assert "tool_approvals" not in dumped


def test_agent_preset_read_minimal_exposes_current_version_subagent_eligibility() -> (
    None
):
    payload = build_agent_preset_read_minimal(
        make_agent_preset(
            name="Parent preset",
            slug="parent-preset",
            tool_approvals={"core.http_request": True},
            agents={"subagents": []},
        )
    )

    dumped = payload.model_dump(mode="json")
    assert dumped["current_version_subagent_eligibility"] == {
        "eligible": False,
        "reasons": ["tool_approvals"],
        "message": (
            "This version requires manual approvals, which are not supported for "
            "preset subagents yet."
        ),
    }
    assert dumped["capabilities"] == ["approvals"]
    assert "agents" not in dumped


def test_build_subagent_eligibility_allows_no_attached_children() -> None:
    eligibility = build_subagent_eligibility(
        agents_config={"subagents": []},
        tool_approvals={"core.http_request": False},
    )

    assert eligibility.eligible is True
    assert eligibility.reasons == []
    assert eligibility.message is None


@pytest.mark.parametrize("enabled", [True, False])
def test_agents_config_drops_legacy_enabled_field(enabled: bool) -> None:
    """Rows written before the toggle was removed still carry `enabled`."""
    config = AgentSubagentsConfig.model_validate(
        {"enabled": enabled, "subagents": [{"preset": "analyst"}]}
    )

    assert [ref.preset for ref in config.subagents] == ["analyst"]
    assert "enabled" not in config.model_dump()


@pytest.mark.parametrize("enabled", [True, False])
def test_resolved_agents_config_drops_legacy_enabled_field(enabled: bool) -> None:
    """The persisted binding schema tolerates the same legacy key."""
    config = ResolvedAgentsConfig.model_validate(
        {
            "enabled": enabled,
            "subagents": [
                {
                    "preset": "analyst",
                    "preset_id": str(uuid.uuid4()),
                    "preset_version_id": str(uuid.uuid4()),
                }
            ],
        }
    )

    assert [ref.preset for ref in config.subagents] == ["analyst"]
    assert "enabled" not in config.model_dump()


@pytest.mark.parametrize("model", [AgentPreset, AgentPresetVersion])
def test_orm_agents_default_validates_against_schema(
    model: type[DeclarativeBase],
) -> None:
    """A row written with the ORM default must validate without legacy keys."""
    column = model.__table__.c.agents
    default = column.default
    assert isinstance(default, CallableColumnDefault)

    # SQLAlchemy wraps the zero-arg lambda, so the execution context is unused.
    value = default.arg(cast(Any, None))

    assert value == AgentSubagentsConfig().model_dump(mode="json")
    assert ResolvedAgentsConfig.model_validate(value).subagents == []
    assert column.server_default is not None
    assert str(column.server_default.arg) == "'{\"subagents\": []}'::jsonb"


def test_agents_config_rejects_misspelled_subagents_field() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentSubagentsConfig.model_validate({"subagent": [{"preset": "analyst"}]})


def test_resolved_agents_config_rejects_other_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResolvedAgentsConfig.model_validate({"enabled": False, "disabled": True})


def test_build_subagent_eligibility_rejects_nested_subagents() -> None:
    eligibility = build_subagent_eligibility(
        agents_config={
            "subagents": [{"preset": "nested-child"}],
        },
        tool_approvals={},
    )

    assert eligibility.eligible is False
    assert eligibility.reasons == ["subagents_attached"]
    assert eligibility.message == (
        "This version defines its own subagents. Remove those subagents before "
        "attaching this version as a subagent."
    )


def test_agent_preset_version_read_schema_accepts_legacy_whitespace_model_fields() -> (
    None
):
    payload = AgentPresetVersionReadMinimal.model_validate(
        {
            "id": "522b4d28-ae2b-4705-bb53-c3aa9071fe16",
            "preset_id": "f3af894f-3d0e-484d-8a2c-36931ca68cc0",
            "workspace_id": "6b2bb4d8-8461-486d-b4ca-e10a5a19d2f2",
            "version": 1,
            "created_at": "2026-03-09T00:00:00Z",
            "updated_at": "2026-03-09T00:00:00Z",
        }
    )

    assert payload.version == 1
    assert str(payload.workspace_id) == "6b2bb4d8-8461-486d-b4ca-e10a5a19d2f2"
