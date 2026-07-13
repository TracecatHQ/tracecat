from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

from tracecat.agent.adapter.vercel import (
    DataEventPayload,
    DataUIPart,
    ToolUIPartInputAvailable,
    VercelStreamContext,
    convert_chat_messages_to_ui,
)
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.common.stream_types import UnifiedStreamEvent
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


def test_convert_chat_messages_to_ui_emits_cancelled_marker() -> None:
    messages = convert_chat_messages_to_ui(
        [
            ChatMessage(
                id=str(uuid4()),
                kind=MessageKind.CANCELLED,
                cancelled={"reason": "user_cancel"},
            )
        ]
    )

    assert len(messages) == 1
    assert messages[0].role == "system"
    part = cast(DataUIPart, messages[0].parts[0])
    assert part["type"] == "data-cancelled"
    assert part["data"] == {"reason": "user_cancel"}


def test_convert_chat_messages_to_ui_cancelled_marker_carries_tool_call_ids() -> None:
    """Structured interrupt metadata survives the DB reload path."""
    messages = convert_chat_messages_to_ui(
        [
            ChatMessage(
                id=str(uuid4()),
                kind=MessageKind.CANCELLED,
                cancelled={
                    "reason": "user_cancel",
                    "tool_call_ids": ["toolu_aborted"],
                },
            )
        ]
    )

    assert len(messages) == 1
    part = cast(DataUIPart, messages[0].parts[0])
    assert part["type"] == "data-cancelled"
    assert part["data"] == {
        "reason": "user_cancel",
        "tool_call_ids": ["toolu_aborted"],
    }


def test_convert_claude_agent_tool_history_uses_effective_input() -> None:
    """Reloaded UI history matches the input Tracecat actually executed."""
    requested_input = {
        "subagent_type": "case-agent",
        "prompt": "List cases",
        "model": "sonnet",
        "isolation": "worktree",
    }
    tool_use = ToolUseBlock(
        id="toolu_agent",
        name="Agent",
        input=requested_input,
    )
    messages = convert_chat_messages_to_ui(
        [
            ChatMessage(
                id=str(uuid4()),
                message=AssistantMessage(
                    content=[tool_use],
                    model="custom-model",
                ),
            )
        ]
    )

    assert len(messages) == 1
    part = cast(ToolUIPartInputAvailable, messages[0].parts[0])
    assert part["input"] == {
        "subagent_type": "case-agent",
        "prompt": "List cases",
    }
    assert tool_use.input == {
        "subagent_type": "case-agent",
        "prompt": "List cases",
        "model": "sonnet",
        "isolation": "worktree",
    }


@pytest.mark.anyio
async def test_stream_cancelled_event_carries_tool_call_ids() -> None:
    """Structured interrupt metadata survives the live-stream path."""
    context = VercelStreamContext(message_id="msg_test")
    event = UnifiedStreamEvent.cancelled_event(
        reason="user_cancel",
        tool_call_ids=["toolu_aborted", "toolu_unresolved"],
    )

    payloads = [payload async for payload in context.handle_event(event)]

    data_payloads = [p for p in payloads if isinstance(p, DataEventPayload)]
    assert len(data_payloads) == 1
    assert data_payloads[0].type == "data-cancelled"
    assert data_payloads[0].data == {
        "reason": "user_cancel",
        "tool_call_ids": ["toolu_aborted", "toolu_unresolved"],
    }
