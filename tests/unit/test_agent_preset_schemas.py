"""Validation tests for agent preset request schemas."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from tracecat.agent.preset.internal_router import (
    PresetCreateRequest,
    PresetUpdateRequest,
)
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetUpdate,
    AgentPresetVersionReadMinimal,
    build_agent_preset_read_minimal,
    build_subagent_eligibility,
)
from tracecat.db.models import AgentPreset


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
        current_version_id=None,
        tool_approvals=tool_approvals,
        agents=agents or {"enabled": False},
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
