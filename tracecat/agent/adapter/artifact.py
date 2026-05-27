"""Mission Control artifact data-part contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from tracecat.agent.common.stream_types import UnifiedStreamEvent

ARTIFACT_DATA_PART_TYPE = "data-artifact"

type ArtifactOp = Literal["upsert", "remove"]


class ArtifactSchema(BaseModel):
    """Base class for Mission Control artifact models."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ArtifactScope(ArtifactSchema):
    """Subagent attribution for Mission Control data parts."""

    agent_id: str | None = Field(default=None, alias="agentId")
    agent_type: str | None = Field(default=None, alias="agentType")
    parent_tool_call_id: str | None = Field(default=None, alias="parentToolCallId")


class BaseArtifact(ArtifactSchema):
    """Common fields shared by all artifact types."""

    id: str
    title: str
    scope: ArtifactScope | None = None


class CaseArtifact(BaseArtifact):
    """Case artifact shown in the Mission Control side panel."""

    type: Literal["case"] = "case"
    severity: Literal[
        "unknown",
        "informational",
        "low",
        "medium",
        "high",
        "critical",
        "fatal",
        "other",
    ]
    status: Literal[
        "unknown",
        "new",
        "in_progress",
        "on_hold",
        "resolved",
        "closed",
        "other",
    ]


class WorkflowArtifact(BaseArtifact):
    """Workflow artifact shown in the Mission Control side panel."""

    type: Literal["workflow"] = "workflow"
    color: str
    is_published: bool | None = Field(default=None, alias="isPublished")


class RunArtifact(BaseArtifact):
    """Workflow run artifact shown in the Mission Control side panel."""

    type: Literal["run"] = "run"
    workflow_id: str = Field(alias="workflowId")
    status: Literal["running", "success", "failed", "cancelled"]
    started_at: datetime = Field(alias="startedAt")


class TableArtifact(BaseArtifact):
    """Table artifact shown in the Mission Control side panel."""

    type: Literal["table"] = "table"
    row_count: int | None = Field(default=None, alias="rowCount")


class AlertArtifact(BaseArtifact):
    """Alert artifact stub. Extend when alert emit sites are wired."""

    type: Literal["alert"] = "alert"


class IntegrationArtifact(BaseArtifact):
    """Integration artifact stub. Extend when integration emit sites are wired."""

    type: Literal["integration"] = "integration"


class SecretArtifact(BaseArtifact):
    """Secret artifact stub. Extend when secret emit sites are wired."""

    type: Literal["secret"] = "secret"


class GenericArtifact(BaseArtifact):
    """Escape hatch for agent-surfaced objects without a dedicated panel view."""

    type: Literal["generic"] = "generic"
    data: dict[str, Any] | None = None


type Artifact = Annotated[
    CaseArtifact
    | WorkflowArtifact
    | RunArtifact
    | TableArtifact
    | AlertArtifact
    | IntegrationArtifact
    | SecretArtifact
    | GenericArtifact,
    Field(discriminator="type"),
]

ArtifactAdapter: TypeAdapter[Artifact] = TypeAdapter(Artifact)


class ArtifactDataPayload(ArtifactSchema):
    """Vercel custom data-part payload for Mission Control artifacts."""

    op: ArtifactOp
    artifact: Artifact


def artifact_data_payload(op: ArtifactOp, artifact: Artifact) -> dict[str, Any]:
    """Serialize an artifact operation for a `data-artifact` UI message part."""
    payload = ArtifactDataPayload(op=op, artifact=artifact)
    return payload.model_dump(mode="json", by_alias=True, exclude_none=True)


def artifact_stream_event(op: ArtifactOp, artifact: Artifact) -> UnifiedStreamEvent:
    """Serialize an artifact operation as a durable unified stream event."""
    payload = artifact_data_payload(op, artifact)
    return UnifiedStreamEvent.artifact_event(
        op=op,
        artifact=cast(dict[str, Any], payload["artifact"]),
    )
