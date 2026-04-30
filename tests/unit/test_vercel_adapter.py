from datetime import UTC, datetime
from uuid import uuid4

from tracecat.agent.adapter.vercel import convert_chat_messages_to_ui
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import ApprovalRead, ChatMessage


def _approval_message(status: ApprovalStatus) -> ChatMessage:
    return ChatMessage(
        id=str(uuid4()),
        kind=MessageKind.APPROVAL_REQUEST,
        approval=ApprovalRead(
            id=uuid4(),
            tool_call_id="toolu_123",
            tool_name="core.cases.list_cases",
            tool_call_args={"limit": 100},
            status=status,
            created_at=datetime.now(UTC),
        ),
    )


def test_convert_chat_messages_to_ui_emits_pending_approval_request() -> None:
    messages = convert_chat_messages_to_ui([_approval_message(ApprovalStatus.PENDING)])

    assert len(messages) == 1
    assert messages[0].role == "assistant"
    assert messages[0].parts[0]["type"] == "data-approval-request"


def test_convert_chat_messages_to_ui_skips_resolved_approval_request() -> None:
    messages = convert_chat_messages_to_ui([_approval_message(ApprovalStatus.APPROVED)])

    assert messages == []
