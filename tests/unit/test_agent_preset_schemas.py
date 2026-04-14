"""Validation tests for agent preset request schemas."""

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
            "enable_internet_access": False,
            "current_version_id": None,
            "created_at": "2026-03-09T00:00:00Z",
            "updated_at": "2026-03-09T00:00:00Z",
        }
    )

    assert payload.model_name == "   "
    assert payload.model_provider == "   "


def test_agent_preset_version_read_schema_accepts_legacy_whitespace_model_fields() -> (
    None
):
    payload = AgentPresetVersionReadMinimal.model_validate(
        {
            "id": "522b4d28-ae2b-4705-bb53-c3aa9071fe16",
            "preset_id": "f3af894f-3d0e-484d-8a2c-36931ca68cc0",
            "workspace_id": "6b2bb4d8-8461-486d-b4ca-e10a5a19d2f2",
            "version": 1,
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
            "enable_internet_access": False,
            "created_at": "2026-03-09T00:00:00Z",
            "updated_at": "2026-03-09T00:00:00Z",
        }
    )

    assert payload.model_name == "   "
    assert payload.model_provider == "   "


def test_agent_preset_read_schema_normalizes_legacy_litellm_provider() -> None:
    payload = AgentPresetRead.model_validate(
        {
            "id": "522b4d28-ae2b-4705-bb53-c3aa9071fe16",
            "workspace_id": "6b2bb4d8-8461-486d-b4ca-e10a5a19d2f2",
            "name": "Legacy preset",
            "slug": "legacy-preset",
            "description": None,
            "instructions": None,
            "model_name": "customer-model",
            "model_provider": "litellm",
            "base_url": None,
            "output_type": None,
            "actions": None,
            "namespaces": None,
            "tool_approvals": None,
            "mcp_integrations": None,
            "retries": 3,
            "enable_internet_access": False,
            "current_version_id": None,
            "created_at": "2026-03-09T00:00:00Z",
            "updated_at": "2026-03-09T00:00:00Z",
        }
    )

    assert payload.model_provider == "custom-model-provider"


def test_agent_preset_version_read_schema_normalizes_legacy_litellm_provider() -> None:
    payload = AgentPresetVersionReadMinimal.model_validate(
        {
            "id": "522b4d28-ae2b-4705-bb53-c3aa9071fe16",
            "preset_id": "f3af894f-3d0e-484d-8a2c-36931ca68cc0",
            "workspace_id": "6b2bb4d8-8461-486d-b4ca-e10a5a19d2f2",
            "version": 1,
            "instructions": None,
            "model_name": "customer-model",
            "model_provider": "litellm",
            "base_url": None,
            "output_type": None,
            "actions": None,
            "namespaces": None,
            "tool_approvals": None,
            "mcp_integrations": None,
            "retries": 3,
            "enable_internet_access": False,
            "created_at": "2026-03-09T00:00:00Z",
            "updated_at": "2026-03-09T00:00:00Z",
        }
    )

    assert payload.model_provider == "custom-model-provider"
