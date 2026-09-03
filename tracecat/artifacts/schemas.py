"""Artifact schemas and data-part payload contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from tracecat.cases.enums import CaseSeverity, CaseStatus

ARTIFACT_DATA_PART_TYPE = "data-artifact"

type ArtifactOp = Literal["upsert", "remove"]
type ArtifactType = Literal[
    "case",
    "workflow",
    "run",
    "table",
    "agent",
    "alert",
    "integration",
    "secret",
    "generic",
]


class ArtifactSchema(BaseModel):
    """Base class for artifact models."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ArtifactScope(ArtifactSchema):
    """Attribution scope for artifact data parts."""

    agent_id: str | None = Field(default=None, alias="agentId")
    agent_type: str | None = Field(default=None, alias="agentType")
    parent_tool_call_id: str | None = Field(default=None, alias="parentToolCallId")


class BaseArtifact(ArtifactSchema):
    """Common fields shared by all artifact types."""

    id: str
    title: str
    scope: ArtifactScope | None = None


class CaseArtifact(BaseArtifact):
    """Case artifact shown in artifact-capable chat surfaces."""

    type: Literal["case"] = "case"
    severity: CaseSeverity
    status: CaseStatus


class WorkflowArtifact(BaseArtifact):
    """Workflow artifact shown in artifact-capable chat surfaces."""

    type: Literal["workflow"] = "workflow"
    color: str
    is_published: bool | None = Field(default=None, alias="isPublished")


class RunArtifact(BaseArtifact):
    """Workflow run artifact shown in artifact-capable chat surfaces."""

    type: Literal["run"] = "run"
    workflow_id: str = Field(alias="workflowId")
    status: Literal["running", "success", "failed", "cancelled"]
    started_at: datetime = Field(alias="startedAt")


class TableArtifact(BaseArtifact):
    """Table artifact shown in artifact-capable chat surfaces."""

    type: Literal["table"] = "table"
    row_count: int | None = Field(default=None, alias="rowCount")


class AgentArtifact(BaseArtifact):
    """Agent preset artifact shown in artifact-capable chat surfaces."""

    type: Literal["agent"] = "agent"


class AlertArtifact(BaseArtifact):
    """Alert artifact stub. Extend when alert surfaces are wired."""

    type: Literal["alert"] = "alert"


class IntegrationArtifact(BaseArtifact):
    """Integration artifact stub. Extend when integration surfaces are wired."""

    type: Literal["integration"] = "integration"


class SecretArtifact(BaseArtifact):
    """Secret artifact stub. Extend when secret surfaces are wired."""

    type: Literal["secret"] = "secret"


class GenericArtifact(BaseArtifact):
    """Escape hatch for surfaced objects without a dedicated panel view."""

    type: Literal["generic"] = "generic"
    data: dict[str, Any] | None = None


type Artifact = Annotated[
    CaseArtifact
    | WorkflowArtifact
    | RunArtifact
    | TableArtifact
    | AgentArtifact
    | AlertArtifact
    | IntegrationArtifact
    | SecretArtifact
    | GenericArtifact,
    Field(discriminator="type"),
]

ArtifactAdapter: TypeAdapter[Artifact] = TypeAdapter(Artifact)


class ArtifactDataPayload(ArtifactSchema):
    """Vercel custom data-part payload for artifacts."""

    op: ArtifactOp
    artifact: Artifact


def artifact_data_payload(op: ArtifactOp, artifact: Artifact) -> dict[str, Any]:
    """Serialize an artifact operation for a `data-artifact` UI message part."""
    payload = ArtifactDataPayload(op=op, artifact=artifact)
    return payload.model_dump(mode="json", by_alias=True, exclude_none=True)
