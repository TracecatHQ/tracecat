"""Schemas for case workflow triggers."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from tracecat.cases.enums import CaseEventType
from tracecat.core.schemas import Schema


class CaseWorkflowTriggerConfig(Schema):
    """Configuration for a case workflow trigger.

    Stored in workflow.object.nodes[].data.caseTriggers (camelCase in JSON).
    """

    id: str = Field(..., description="Unique identifier for this trigger config.")
    enabled: bool = Field(default=True, description="Whether the trigger is enabled.")
    event_type: CaseEventType = Field(
        ...,
        description="The case event type that triggers this workflow.",
    )
    field_filters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dot-delimited path filters (e.g., data.field, data.new). "
            "For comment/description triggers, set event_type=case_updated "
            "and field_filters['data.field'] to the desired subtype."
        ),
    )
    allow_self_trigger: bool = Field(
        default=False,
        description=(
            "Whether this workflow can be triggered by case events "
            "that were caused by the same workflow."
        ),
    )


class CaseTriggerPayload(Schema):
    """Payload passed to workflows triggered by case events."""

    case_id: str = Field(..., description="The UUID of the case.")
    case_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="The custom field values for the case.",
    )
    case_event: dict[str, Any] = Field(
        ...,
        description=(
            "The case event that triggered the workflow. "
            "Contains id, type, created_at, data, and user_id."
        ),
    )
