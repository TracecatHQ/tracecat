"""Validation tests for agent preset request schemas."""

import pytest
from pydantic import BaseModel, ValidationError

from tracecat.agent.preset.internal_router import (
    PresetCreateRequest,
    PresetUpdateRequest,
)
from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate


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
