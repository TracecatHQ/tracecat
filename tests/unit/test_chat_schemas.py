"""Tests for chat API schemas."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.chat.schemas import ApprovalRead


def approval_read_data(decision: object) -> dict[str, object]:
    """Build validation input for an approval timeline record."""
    return {
        "id": uuid.uuid4(),
        "tool_call_id": "tool-call-1",
        "tool_name": "core.example.action",
        "status": ApprovalStatus.APPROVED,
        "decision": decision,
        "created_at": datetime.now(UTC),
    }


@pytest.mark.parametrize(
    "decision",
    [
        True,
        False,
        {"kind": "tool-approved"},
        {
            "kind": "tool-approved",
            "override_args": {"limit": 10},
            "metadata": {"source": "inbox"},
        },
        {
            "kind": "tool-denied",
            "message": "Not approved",
            "metadata": {"source": "inbox"},
        },
        {"value": True, "metadata": {"source": "inbox"}},
    ],
)
def test_approval_read_accepts_persisted_decision_shapes(decision: object) -> None:
    approval = ApprovalRead.model_validate(approval_read_data(decision))

    assert approval.decision == decision


def test_approval_read_rejects_unstructured_decision() -> None:
    with pytest.raises(ValidationError):
        ApprovalRead.model_validate(
            approval_read_data({"unexpected": "decision shape"})
        )
