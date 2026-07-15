"""Validation tests for agent preset request schemas."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from tracecat.agent.preset.internal_router import PresetCreateRequest
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetSkillBindingRead,
    AgentPresetUpdate,
    AgentPresetVersionReadMinimal,
    build_agent_preset_read_minimal,
    build_subagent_eligibility,
)
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
    preset_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    version = AgentPresetVersion(
        id=uuid.uuid4(),
        preset_id=preset_id,
        workspace_id=workspace_id,
        version=1,
        model_provider="openai",
        model_name="gpt-4o-mini",
        tool_approvals=tool_approvals,
        agents=agents or {},
        enable_internet_access=enable_internet_access,
        created_at=timestamp,
        updated_at=timestamp,
    )
    preset = AgentPreset(
        id=preset_id,
        workspace_id=workspace_id,
        name=name,
        slug=slug,
        description=None,
        current_version_id=version.id,
        created_at=timestamp,
        updated_at=timestamp,
    )
    preset.current_version = version
    return preset


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


def test_agent_preset_skill_binding_read_contains_only_head_metadata() -> None:
    skill_id = uuid.uuid4()

    binding = AgentPresetSkillBindingRead.model_validate(
        {
            "skill_id": str(skill_id),
            "skill_name": "triage-skill",
        }
    )

    assert binding.model_dump(mode="json") == {
        "skill_id": str(skill_id),
        "skill_name": "triage-skill",
    }


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
    ],
)
def test_agent_preset_request_schemas_reject_blank_trimmed_values(
    schema_cls: type[BaseModel],
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        schema_cls.model_validate(kwargs)


@pytest.mark.parametrize(
    "field",
    (
        "name",
        "model_name",
        "model_provider",
        "retries",
        "enable_thinking",
        "enable_internet_access",
    ),
)
def test_agent_preset_update_rejects_explicit_null_for_required_fields(
    field: str,
) -> None:
    with pytest.raises(ValidationError, match=f"{field} cannot be null"):
        AgentPresetUpdate.model_validate({field: None})


@pytest.mark.parametrize("schema_cls", [PresetCreateRequest, AgentPresetUpdate])
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
            "current_version_id": "3f8d0919-5584-4fe2-a888-123a3423372c",
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
            agents={"enabled": True, "subagents": []},
        )
    )

    dumped = payload.model_dump(mode="json")
    assert dumped["current_version_subagent_eligibility"] == {
        "eligible": False,
        "reasons": ["agents_enabled", "tool_approvals"],
        "message": (
            "This version defines its own subagents and requires manual approvals, "
            "which are not supported for preset subagents yet."
        ),
    }
    assert dumped["capabilities"] == ["approvals", "subagents"]
    assert "agents" not in dumped


def test_agent_preset_read_minimal_supports_unpublished_heads() -> None:
    timestamp = datetime(2026, 3, 9, tzinfo=UTC)
    preset = AgentPreset(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="Unpublished preset",
        slug="unpublished-preset",
        current_version_id=None,
        created_at=timestamp,
        updated_at=timestamp,
    )

    payload = build_agent_preset_read_minimal(preset)

    assert payload.current_version_id is None
    assert payload.model_provider is None
    assert payload.model_name is None
    assert payload.capabilities == []
    assert payload.current_version_subagent_eligibility.eligible is True


def test_build_subagent_eligibility_allows_plain_versions() -> None:
    eligibility = build_subagent_eligibility(
        agents_config={"enabled": False},
        tool_approvals={"core.http_request": False},
    )

    assert eligibility.eligible is True
    assert eligibility.reasons == []
    assert eligibility.message is None


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
