from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from claude_agent_sdk.types import (
    AssistantMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from tracecat.agent.adapter.vercel import (
    DataEventPayload,
    DataUIPart,
    ToolUIPartInputAvailable,
    ToolUIPartOutputAvailable,
    ToolUIPartOutputError,
    VercelStreamContext,
    convert_chat_messages_to_ui,
)
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.channel_bots.messages import (
    ChannelMessage,
    ChannelMessageSender,
    QuotedMessage,
    QuotedMessageKind,
)
from tracecat.agent.channel_bots.schemas import ChannelBotProvider
from tracecat.agent.common.stream_types import UnifiedStreamEvent
from tracecat.agent.session.service import _extract_vercel_user_prompt
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import ApprovalRead, ChatMessage, MessageAuthor


def _approval_message(
    status: ApprovalStatus,
    *,
    tool_call_id: str = "toolu_123",
    reason: str | None = None,
) -> ChatMessage:
    return ChatMessage(
        id=str(uuid4()),
        kind=MessageKind.APPROVAL_REQUEST,
        approval=ApprovalRead(
            id=uuid4(),
            tool_call_id=tool_call_id,
            tool_name="core.cases.list_cases",
            tool_call_args={"limit": 100},
            status=status,
            reason=reason,
            created_at=datetime.now(UTC),
        ),
    )


def test_convert_chat_messages_to_ui_carries_message_time() -> None:
    recorded = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    message = ChatMessage(
        id=str(uuid4()),
        message=AssistantMessage(content=[TextBlock(text="Done.")], model="claude"),
        created_at=recorded,
    )

    [ui_message] = convert_chat_messages_to_ui([message])

    assert ui_message.metadata == {"created_at": recorded.isoformat()}


def test_convert_chat_messages_to_ui_carries_the_author() -> None:
    author = MessageAuthor(id=uuid4(), email="sam@example.com", first_name="Sam")
    message = ChatMessage(
        id=str(uuid4()),
        message=UserMessage(content="triage this"),
        author=author,
    )

    [ui_message] = convert_chat_messages_to_ui([message])

    assert ui_message.metadata == {"author": author.model_dump(mode="json")}


def _decided_call(
    status: ApprovalStatus,
    *,
    result: str,
    is_error: bool,
    reason: str | None = None,
) -> list[ChatMessage]:
    """A tool call that needed approval, its decision, and its recorded result."""
    tool_call_id = "toolu_gated"
    return [
        ChatMessage(
            id=str(uuid4()),
            message=AssistantMessage(
                content=[
                    ToolUseBlock(
                        id=tool_call_id,
                        name="core__cases__list_cases",
                        input={"limit": 100},
                    )
                ],
                model="model",
            ),
        ),
        _approval_message(status, tool_call_id=tool_call_id, reason=reason),
        ChatMessage(
            id=str(uuid4()),
            message=UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id=tool_call_id,
                        content=result,
                        is_error=is_error,
                    )
                ]
            ),
        ),
    ]


def test_convert_chat_messages_to_ui_emits_pending_approval_request() -> None:
    messages = convert_chat_messages_to_ui([_approval_message(ApprovalStatus.PENDING)])

    assert len(messages) == 1
    assert messages[0].role == "assistant"
    assert messages[0].parts[0]["type"] == "data-approval-request"


def test_convert_chat_messages_to_ui_skips_resolved_approval_request() -> None:
    messages = convert_chat_messages_to_ui([_approval_message(ApprovalStatus.APPROVED)])

    assert messages == []


def test_convert_chat_messages_to_ui_keeps_resolved_card_while_batch_pending() -> None:
    approved = _approval_message(ApprovalStatus.APPROVED, tool_call_id="toolu_a")
    pending = _approval_message(ApprovalStatus.PENDING, tool_call_id="toolu_b")

    messages = convert_chat_messages_to_ui([approved, pending])
    approval_parts = [cast(DataUIPart, message.parts[0]) for message in messages]

    assert [part["data"][0]["status"] for part in approval_parts] == [
        "approved",
        "pending",
    ]


def test_convert_chat_messages_to_ui_tags_denied_call_result() -> None:
    """A denied call's result carries the denial, so reloads don't show an error."""
    messages = convert_chat_messages_to_ui(
        _decided_call(
            ApprovalStatus.REJECTED,
            result="Tool denied by user: not today\n\nDo not retry it.",
            is_error=True,
            reason="not today",
        )
    )

    assert len(messages) == 1
    part = cast(ToolUIPartOutputError, messages[0].parts[0])
    assert part["state"] == "output-error"
    assert part.get("callProviderMetadata") == {
        "tracecat": {"approval": "rejected", "reason": "not today"}
    }


def test_convert_chat_messages_to_ui_leaves_approved_call_result_untagged() -> None:
    """Only denials are tagged: an approved call's result renders as usual."""
    messages = convert_chat_messages_to_ui(
        _decided_call(ApprovalStatus.APPROVED, result="[]", is_error=False)
    )

    assert len(messages) == 1
    part = cast(ToolUIPartOutputAvailable, messages[0].parts[0])
    assert part["state"] == "output-available"
    assert "callProviderMetadata" not in part


def test_convert_chat_messages_to_ui_shows_approved_override_args() -> None:
    """A call approved with edited arguments shows the arguments it ran with."""
    call, approval, result = _decided_call(
        ApprovalStatus.APPROVED, result="[]", is_error=False
    )
    assert approval.approval is not None
    approval.approval.decision = {
        "kind": "tool-approved",
        "override_args": {"limit": 5},
    }

    messages = convert_chat_messages_to_ui([call, approval, result])

    part = cast(ToolUIPartOutputAvailable, messages[0].parts[0])
    assert part["input"] == {"limit": 5}


def test_convert_chat_messages_to_ui_records_when_a_call_completed() -> None:
    """The merged result row's time survives, so later steps time from it."""
    called_at = datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC)
    completed_at = datetime(2026, 1, 1, 0, 0, 32, tzinfo=UTC)
    call, approval, result = _decided_call(
        ApprovalStatus.REJECTED, result="denied", is_error=True, reason="no"
    )
    call.created_at = called_at
    result.created_at = completed_at

    messages = convert_chat_messages_to_ui([call, approval, result])

    assert len(messages) == 1
    assert messages[0].metadata == {"created_at": called_at.isoformat()}
    part = cast(ToolUIPartOutputError, messages[0].parts[0])
    assert part.get("callProviderMetadata") == {
        "tracecat": {
            "approval": "rejected",
            "reason": "no",
            "completed_at": completed_at.isoformat(),
        }
    }


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


@pytest.mark.anyio
@pytest.mark.parametrize("message_id", ["steer-1", None])
async def test_stream_user_message_event_becomes_a_data_part(
    message_id: str | None,
) -> None:
    """A message that joins the running turn keeps the ID it was sent under."""
    context = VercelStreamContext(message_id="msg_test")
    event = UnifiedStreamEvent.user_message_event(
        "Focus on the newest alert", message_id=message_id
    )

    payloads = [payload async for payload in context.handle_event(event)]

    assert len(payloads) == 1
    payload = payloads[0]
    assert isinstance(payload, DataEventPayload)
    assert payload.type == "data-user-message"
    assert payload.data == {"id": message_id, "text": "Focus on the newest alert"}


FORWARD = QuotedMessage(
    kind=QuotedMessageKind.FORWARDED, author="alice", text="Disk at 95% on db-1"
)


def _channel_message(text: str) -> ChannelMessage:
    return ChannelMessage(
        provider=ChannelBotProvider.SLACK,
        text=text,
        sender=ChannelMessageSender(provider_user_id="U1", name="Dana"),
        quoted=[FORWARD],
    )


@pytest.mark.parametrize("text", ["triage this", ""])
def test_convert_chat_messages_to_ui_shows_a_channel_message_and_its_context(
    text: str,
) -> None:
    channel_message = _channel_message(text)
    messages = convert_chat_messages_to_ui(
        [
            ChatMessage(
                id=str(uuid4()),
                message=UserMessage(content=channel_message.render()),
                channel_message=channel_message,
            )
        ]
    )

    assert len(messages) == 1
    [part] = messages[0].parts
    data_part = cast(DataUIPart, part)
    assert data_part["type"] == "data-channel-message"
    data = cast(dict[str, Any], data_part["data"])
    assert data["text"] == text
    assert data["quoted"][0]["text"] == "Disk at 95% on db-1"
    assert data["sender"]["name"] == "Dana"


@pytest.mark.anyio
@pytest.mark.parametrize("channel_session", [True, False])
async def test_a_steered_channel_message_decodes_only_in_a_channel_session(
    channel_session: bool,
) -> None:
    rendered = _channel_message("and db-2").render()
    context = VercelStreamContext(
        message_id="msg_test", channel_session=channel_session
    )
    event = UnifiedStreamEvent.user_message_event(rendered, message_id="steer-1")

    payloads = [payload async for payload in context.handle_event(event)]

    payload = payloads[0]
    assert isinstance(payload, DataEventPayload)
    if channel_session:
        assert payload.data["text"] == "and db-2"
        assert payload.data["channel_message"]["quoted"][0]["author"] == "alice"
    else:
        assert payload.data == {"id": "steer-1", "text": rendered}


def test_quoted_interrupt_markers_do_not_hide_a_channel_message() -> None:
    channel_message = ChannelMessage(
        provider=ChannelBotProvider.SLACK,
        text="what happened?",
        sender=ChannelMessageSender(provider_user_id="U1"),
        quoted=[
            QuotedMessage(
                kind=QuotedMessageKind.FORWARDED,
                author="alice",
                text="[Request interrupted by user for tool use]",
            )
        ],
    )
    messages = convert_chat_messages_to_ui(
        [
            ChatMessage(
                id=str(uuid4()),
                message=UserMessage(content=[TextBlock(text=channel_message.render())]),
                channel_message=channel_message,
            )
        ]
    )

    assert len(messages) == 1


@pytest.mark.parametrize("text", ["triage this", ""])
def test_retrying_a_channel_message_resends_its_envelope(text: str) -> None:
    channel_message = _channel_message(text)
    [ui_message] = convert_chat_messages_to_ui(
        [
            ChatMessage(
                id=str(uuid4()),
                message=UserMessage(content=channel_message.render()),
                channel_message=channel_message,
            )
        ]
    )

    assert _extract_vercel_user_prompt(ui_message) == channel_message.render()
