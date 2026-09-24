"""Wire contracts shared with the built-in durable agent workflow."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import DeferredToolApprovalResult
from tracecat.auth.types import Role


class AgentWorkflowArgs(BaseModel):
    """Arguments for starting an agent workflow."""

    # Temporal stores the original workflow input in history. Keep stale keys
    # replayable after workflow args evolve, including the removed legacy
    # ``use_workspace_credentials`` flag.
    model_config = ConfigDict(extra="ignore")

    role: Role
    agent_args: RunAgentArgs
    # Session metadata
    title: str = Field(default="New Chat", description="Session title")
    entity_type: AgentSessionEntity = Field(
        ..., description="Type of entity this session is associated with"
    )
    entity_id: uuid.UUID = Field(..., description="ID of the associated entity")
    tools: list[str] | None = Field(
        default=None, description="Tools available to the agent"
    )
    agent_preset_id: uuid.UUID | None = Field(
        default=None, description="Agent preset used for this session"
    )
    agent_preset_version_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Pinned preset version used for this workflow run. "
            "If null, the run follows the preset's current version."
        ),
    )
    harness_type: HarnessType | None = Field(
        default=None,
        description="Agent harness type. Reserved for future multi-harness support.",
    )
    continue_existing_session: bool = Field(
        default=False,
        description=("If true, session_id is caller-supplied and must already exist."),
    )


class WorkflowApprovalSubmission(BaseModel):
    approvals: dict[str, bool | DeferredToolApprovalResult]
    approved_by: uuid.UUID | None = None
    decision_metadata: dict[str, dict[str, Any]] | None = None
    new_stream_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Rotated per-turn Redis stream ID. When set, the workflow sends every "
            "event emitted after approval resumes to this new stream instead of "
            "the stream that ended at the approval pause, which may already have "
            "expired."
        ),
    )


class WorkflowCancelRequest(BaseModel):
    reason: Literal["user_cancel"] = "user_cancel"
