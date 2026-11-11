"""Encode approval decisions onto approval rows and decode them for delivery."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.approvals.types import (
    BooleanApprovalDecision,
    PersistedApprovalDecision,
    ToolApprovedDecision,
    ToolDeniedDecision,
)
from tracecat.agent.types import ToolApproved, ToolDenied

if TYPE_CHECKING:
    from tracecat_ee.agent.approvals.service import ApprovalResult

    from tracecat.chat.schemas import ApprovalDecision
    from tracecat.db.models import Approval

DEFAULT_DENY_MESSAGE = "Tool denied by user"

type ApprovalDecisionMetadata = dict[str, Any]
"""Submission metadata persisted alongside a decision.

Always carries ``source``; client-supplied keys are merged over it, so the
value type stays open. ``decision_outcome`` ignores this entirely so the
same decision replayed from another surface is not a conflict.
"""


@dataclass(frozen=True, slots=True)
class SubmittedDecision:
    """One decision as a caller submitted it, before it is recorded."""

    result: ApprovalResult
    metadata: ApprovalDecisionMetadata

    @classmethod
    def of(cls, decision: ApprovalDecision, *, source: str) -> SubmittedDecision:
        """Map a clicked decision onto what the run receives, stamped with its source."""
        return cls(
            result=approval_result(
                decision.action,
                reason=decision.reason,
                override_args=decision.override_args,
            ),
            metadata={**(decision.metadata or {}), "source": source},
        )


@dataclass(frozen=True, slots=True)
class _ApprovalDecisionFields:
    """Approval row fields derived from a deferred approval result."""

    status: ApprovalStatus
    reason: str | None
    decision: PersistedApprovalDecision


def _approval_decision_fields(
    result: ApprovalResult,
    *,
    decision_metadata: ApprovalDecisionMetadata | None,
) -> _ApprovalDecisionFields:
    """Map a deferred approval result into Approval row update fields."""
    status: ApprovalStatus
    reason: str | None = None
    decision: PersistedApprovalDecision

    match result:
        case bool(value):
            status = ApprovalStatus.APPROVED if value else ApprovalStatus.REJECTED
            if decision_metadata:
                boolean_decision: BooleanApprovalDecision = {
                    "value": value,
                    "metadata": decision_metadata,
                }
                decision = boolean_decision
            else:
                decision = value
        case ToolApproved(override_args=override_args):
            status = ApprovalStatus.APPROVED
            approved_decision: ToolApprovedDecision = {"kind": "tool-approved"}
            if override_args is not None:
                approved_decision["override_args"] = override_args
            if decision_metadata:
                approved_decision["metadata"] = decision_metadata
            decision = approved_decision
        case ToolDenied(message=message):
            status = ApprovalStatus.REJECTED
            reason = message
            denied_decision: ToolDeniedDecision = {"kind": "tool-denied"}
            if message:
                denied_decision["message"] = message
            if decision_metadata:
                denied_decision["metadata"] = decision_metadata
            decision = denied_decision
        case _:
            raise ValueError(f"Unsupported approval result: {type(result)}")

    return _ApprovalDecisionFields(status=status, reason=reason, decision=decision)


def approval_result(
    action: Literal["approve", "override", "deny"],
    *,
    reason: str | None = None,
    override_args: dict[str, Any] | None = None,
) -> ApprovalResult:
    """Map a clicked action onto the decision the run receives."""
    match action:
        case "approve":
            return True
        case "override":
            return ToolApproved(override_args=override_args or {})
        case _:
            return ToolDenied(message=reason or DEFAULT_DENY_MESSAGE)


def record_decision(
    approval: Approval,
    submitted: SubmittedDecision,
    *,
    approved_by: uuid.UUID | None,
) -> None:
    """Write a submitted decision and its approver onto a pending row."""
    fields = _approval_decision_fields(
        submitted.result, decision_metadata=submitted.metadata
    )
    approval.status = fields.status
    approval.reason = fields.reason
    approval.decision = fields.decision
    approval.approved_by = approved_by
    approval.approved_at = datetime.now(tz=UTC)


def decision_outcome(result: ApprovalResult) -> tuple[Any, ...]:
    """Reduce a decision to its outcome, as JSON-serializable values.

    Two decisions with the same outcome are the same decision, whichever
    surface sent them and whatever deny message it supplied.
    """
    match result:
        case bool(value):
            return ("value", value)
        case ToolApproved(override_args=override_args):
            return ("tool-approved", override_args)
        case ToolDenied():
            return ("tool-denied",)
        case _:
            raise ValueError(f"Unsupported approval result: {type(result)}")


def decision_matches_row(result: ApprovalResult, approval: Approval) -> bool:
    """Return whether a resubmitted decision matches a decided row."""
    if approval.decision is None:
        # Rows without a decision payload predate it: the status is the outcome.
        submitted = _approval_decision_fields(result, decision_metadata=None)
        return submitted.status is approval.status
    return decision_outcome(result) == decision_outcome(recorded_result(approval))


def recorded_result(approval: Approval) -> ApprovalResult:
    """Rebuild the decision to deliver from its recorded row."""
    decision = approval.decision
    match decision:
        case bool(value) | {"value": bool(value)}:
            return value
        case {"kind": "tool-approved"}:
            approved = cast(ToolApprovedDecision, decision)
            return ToolApproved(override_args=approved.get("override_args"))
        case {"kind": "tool-denied"}:
            denied = cast(ToolDeniedDecision, decision)
            return ToolDenied(
                message=denied.get("message") or approval.reason or DEFAULT_DENY_MESSAGE
            )
    # Rows without a decision payload predate it: the status is the outcome.
    if approval.status is ApprovalStatus.APPROVED:
        return True
    return ToolDenied(message=approval.reason or DEFAULT_DENY_MESSAGE)
