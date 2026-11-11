"""Types for agent approval decisions and their submission."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from tracecat_ee.agent.approvals.service import ApprovalResult


class ToolApprovedDecision(TypedDict):
    """Persisted decision for a tool approved with argument overrides."""

    kind: Literal["tool-approved"]
    override_args: NotRequired[dict[str, Any]]
    metadata: NotRequired[dict[str, Any]]


class ToolDeniedDecision(TypedDict):
    """Persisted decision for a denied tool call."""

    kind: Literal["tool-denied"]
    message: NotRequired[str]
    metadata: NotRequired[dict[str, Any]]


class BooleanApprovalDecision(TypedDict):
    """Persisted boolean decision enriched with submission metadata."""

    value: bool
    metadata: dict[str, Any]


type PersistedApprovalDecision = (
    bool | ToolApprovedDecision | ToolDeniedDecision | BooleanApprovalDecision
)
"""Decision payload stored for a completed approval."""


@dataclass(frozen=True, slots=True)
class SubmittedDecision:
    """One decision as a caller submitted it, before it is recorded."""

    result: ApprovalResult
    # Persisted with the decision; always has ``source``. Not part of its outcome.
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ApprovalAlreadyApplied:
    """The run already had these decisions; there is no new stream to watch."""

    run_id: uuid.UUID
    pending_tool_call_ids: list[str]


@dataclass(frozen=True, slots=True)
class ApprovalDelivered:
    """The run took the decisions; ``stream_id`` carries its output."""

    run_id: uuid.UUID
    stream_id: uuid.UUID
    pending_tool_call_ids: list[str]
    resumed: bool


type ApprovalSubmissionResult = ApprovalAlreadyApplied | ApprovalDelivered
