"""Native Pi history stays native while the shared chat renderer sees messages."""

from copy import deepcopy
from uuid import uuid4

from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

from tracecat.agent.session.pi import (
    active_pi_branch,
    merge_pi_display_rows,
    pi_display_message,
)
from tracecat.agent.types import ClaudeSDKMessageTA
from tracecat.db.models import AgentSessionHistory


def test_pi_display_projection_preserves_native_history() -> None:
    native = {
        "id": "entry-1",
        "type": "message",
        "parentId": "root",
        "message": {
            "role": "assistant",
            "model": "test-model",
            "content": [
                {"type": "text", "text": "Checking"},
                {
                    "type": "toolCall",
                    "id": "call-1",
                    "name": "core.test",
                    "arguments": {"x": 1},
                },
            ],
        },
    }
    before = deepcopy(native)
    display = pi_display_message(native)
    assert display is not None
    message = ClaudeSDKMessageTA.validate_python(display["message"])
    assert isinstance(message, AssistantMessage)
    assert isinstance(message.content[0], TextBlock)
    assert isinstance(message.content[1], ToolUseBlock)
    assert message.content[0].text == "Checking"
    assert message.content[1].id == "call-1"
    assert native == before


def test_pi_user_and_tool_result_render_as_existing_chat_parts() -> None:
    for native_message in (
        {"role": "user", "content": "Hello"},
        {
            "role": "toolResult",
            "toolCallId": "call-1",
            "isError": False,
            "content": [{"type": "text", "text": "done"}],
        },
    ):
        display = pi_display_message({"type": "message", "message": native_message})
        assert display is not None
        ClaudeSDKMessageTA.validate_python(display["message"])
    assert (
        pi_display_message({"type": "compaction", "summary": "native context"}) is None
    )


def test_active_branch_excludes_superseded_tool_results() -> None:
    rows = [
        AgentSessionHistory(kind="pi-native", content={"id": "user", "parentId": None}),
        AgentSessionHistory(
            kind="pi-native", content={"id": "assistant", "parentId": "user"}
        ),
        AgentSessionHistory(
            kind="pi-native", content={"id": "blocked", "parentId": "assistant"}
        ),
        AgentSessionHistory(kind="pi-state", content={"leaf_id": "blocked"}),
        AgentSessionHistory(
            kind="pi-native", content={"id": "approved", "parentId": "assistant"}
        ),
        AgentSessionHistory(
            kind="pi-native", content={"id": "reply", "parentId": "approved"}
        ),
        AgentSessionHistory(kind="pi-state", content={"leaf_id": "reply"}),
    ]
    assert [row.content["id"] for row in active_pi_branch(rows)] == [
        "user",
        "assistant",
        "approved",
        "reply",
    ]
    assert rows[2].content["id"] == "blocked"


def test_pending_user_bubble_is_replaced_by_native_history_without_duplicates() -> None:
    run_id = uuid4()
    pending = AgentSessionHistory(
        kind="pi-input", curr_run_id=run_id, surrogate_id=1, content={"type": "user"}
    )
    native = AgentSessionHistory(
        kind="pi-native",
        curr_run_id=run_id,
        surrogate_id=2,
        content={"type": "message", "message": {"role": "user", "content": "Hi"}},
    )
    cancelled = AgentSessionHistory(
        kind="cancelled",
        curr_run_id=run_id,
        surrogate_id=3,
        content={"reason": "user_cancel"},
    )
    assert merge_pi_display_rows([pending], []) == [pending]
    assert merge_pi_display_rows([pending, native, cancelled], [native]) == [
        native,
        cancelled,
    ]
