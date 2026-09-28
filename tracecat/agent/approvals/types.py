"""Types for persisted agent approval decisions."""

from typing import Any, Literal, NotRequired, TypedDict


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
