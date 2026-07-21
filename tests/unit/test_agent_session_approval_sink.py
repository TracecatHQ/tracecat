from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import orjson
import pytest
from pydantic_ai.tools import ToolApproved
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import (
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.exceptions import ApplicationError

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.executor.schemas import ToolExecutionResult
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat import tokens
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import ApprovalDecision, BasicChatRequest, ContinueRunRequest
from tracecat.db.models import AgentSession, AgentSessionHistory, Approval
from tracecat.exceptions import TracecatConflictError

pytestmark = pytest.mark.usefixtures("db")


class _ApprovalContinuationRedis:
    """Small stateful Redis double for continuation retry tests."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.values: dict[str, str] = {}
        self.fail_next_xadd = False

    async def delete(self, key: str) -> int:
        removed = int(key in self.streams or key in self.values)
        self.streams.pop(key, None)
        self.values.pop(key, None)
        return removed

    async def set_if_not_exists(
        self,
        key: str,
        value: str,
        *,
        expire_seconds: int,
    ) -> bool:
        _ = expire_seconds
        if key in self.values:
            return False
        self.values[key] = value
        return True

    async def xadd(
        self,
        key: str,
        fields: dict[str, str],
        *,
        maxlen: int,
        approximate: bool,
    ) -> str:
        _ = (maxlen, approximate)
        if self.fail_next_xadd:
            self.fail_next_xadd = False
            raise RuntimeError("stream write unavailable")
        entries = self.streams.setdefault(str(key), [])
        entry_id = f"{len(entries) + 1}-0"
        entries.append((entry_id, fields))
        return entry_id

    async def xrange(
        self,
        key: str,
        min_id: str = "-",
        max_id: str = "+",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        _ = (min_id, max_id)
        entries = self.streams.get(str(key), [])
        return entries[:count] if count is not None else entries.copy()

    async def xrevrange(
        self,
        key: str,
        max_id: str = "+",
        min_id: str = "-",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        _ = (max_id, min_id)
        entries = list(reversed(self.streams.get(str(key), [])))
        return entries[:count] if count is not None else entries


@contextlib.contextmanager
def _mock_approval_continuation_dependencies(
    redis_client: object,
    execute_update: AsyncMock,
) -> Iterator[None]:
    temporal_client = SimpleNamespace(
        get_workflow_handle_for=Mock(
            return_value=SimpleNamespace(execute_update=execute_update)
        )
    )
    with (
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=temporal_client),
        ),
        patch(
            "tracecat.agent.stream.connector.get_redis_client",
            AsyncMock(return_value=redis_client),
        ),
        patch(
            "tracecat.agent.session.service.get_redis_client",
            AsyncMock(return_value=redis_client),
        ),
    ):
        yield


@pytest.fixture
async def external_agent_session(session: AsyncSession, svc_role: Role) -> AgentSession:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Slack thread",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.EXTERNAL_CHANNEL.value,
        entity_id=uuid.uuid4(),
        channel_context={
            "channel_type": "slack",
            "team_id": "T123",
            "channel_id": "C123",
            "thread_ts": "1700000000.100",
            "user_id": "U123",
            "event_ts": "1700000000.100",
            "bot_user_id": "B123",
        },
    )
    session.add(agent_session)
    await session.commit()
    await session.refresh(agent_session)
    return agent_session


@pytest.fixture
async def pending_approval_session(
    session: AsyncSession,
    svc_role: Role,
) -> tuple[AgentSession, ContinueRunRequest, uuid.UUID]:
    previous_stream_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
        active_stream_id=previous_stream_id,
    )
    session.add(agent_session)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=agent_session.id,
            tool_call_id="tool_call_123",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()
    await session.refresh(agent_session)
    request = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )
    return agent_session, request, previous_stream_id


@pytest.mark.anyio
async def test_has_pending_approvals_reflects_status(
    session: AsyncSession,
    svc_role: Role,
    external_agent_session: AgentSession,
) -> None:
    service = AgentSessionService(session=session, role=svc_role)

    assert await service.has_pending_approvals(external_agent_session.id) is False

    approval = Approval(
        workspace_id=svc_role.workspace_id,
        session_id=external_agent_session.id,
        tool_call_id="call_123",
        tool_name="core.http_request",
        tool_call_args={"url": "https://example.com"},
        status=ApprovalStatus.PENDING,
    )
    session.add(approval)
    await session.commit()

    assert await service.has_pending_approvals(external_agent_session.id) is True

    approval.status = ApprovalStatus.APPROVED
    session.add(approval)
    await session.commit()

    assert await service.has_pending_approvals(external_agent_session.id) is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("continuation_is_open", "expected_live"),
    [(False, False), (True, True)],
)
async def test_stream_resume_distinguishes_pause_from_rotated_continuation(
    session: AsyncSession,
    svc_role: Role,
    external_agent_session: AgentSession,
    continuation_is_open: bool,
    expected_live: bool,
) -> None:
    """Pending approvals only suppress the closed pre-approval stream."""
    run_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    external_agent_session.curr_run_id = run_id
    external_agent_session.active_stream_id = stream_id
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=external_agent_session.id,
            tool_call_id="call_resume",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    stream = SimpleNamespace(
        is_open_approval_continuation=AsyncMock(return_value=continuation_is_open)
    )
    with (
        patch.object(
            service,
            "get_turn_lifecycle",
            AsyncMock(return_value=(TurnLifecycle.RUNNING, run_id)),
        ),
        patch(
            "tracecat.agent.session.service.AgentStream.new",
            AsyncMock(return_value=stream),
        ) as stream_new,
    ):
        state = await service.get_stream_resume_state(external_agent_session)

    assert state.has_live_stream is expected_live
    stream_new.assert_awaited_once_with(
        session_id=external_agent_session.id,
        workspace_id=svc_role.workspace_id,
        stream_id=stream_id,
    )


@pytest.mark.anyio
async def test_list_messages_preserves_compaction_metadata(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Compaction chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.flush()

    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.COMPACTION.value,
            content={
                "type": "system",
                "subtype": "compact_boundary",
                "compactMetadata": {
                    "preTokens": 128000,
                    "trigger": "auto",
                },
            },
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    messages = await service.list_messages(agent_session.id)

    assert len(messages) == 1
    assert messages[0].kind == MessageKind.COMPACTION
    assert messages[0].compaction == {
        "phase": "completed",
        "pre_tokens": 128000,
    }


@pytest.mark.anyio
async def test_list_messages_sql_owns_active_run_visibility(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    current_run_id = uuid.uuid4()
    prior_run_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Approval history",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=current_run_id,
    )

    def text_entry(*, text: str, curr_run_id: uuid.UUID | None) -> AgentSessionHistory:
        return AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            curr_run_id=curr_run_id,
            content={
                "type": "assistant",
                "uuid": str(uuid.uuid4()),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            },
        )

    legacy_entry = text_entry(text="Legacy row", curr_run_id=None)
    prior_entry = text_entry(text="Prior turn", curr_run_id=prior_run_id)
    approval_entry = AgentSessionHistory(
        session_id=agent_session.id,
        workspace_id=svc_role.workspace_id,
        kind=MessageKind.CHAT_MESSAGE.value,
        curr_run_id=current_run_id,
        content={
            "type": "assistant",
            "uuid": str(uuid.uuid4()),
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_call_123",
                        "name": "core.http_request",
                        "input": {"url": "https://example.com"},
                    }
                ],
            },
        },
    )
    continuation_entry = text_entry(
        text="Live continuation",
        curr_run_id=current_run_id,
    )
    approval = Approval(
        workspace_id=svc_role.workspace_id,
        session_id=agent_session.id,
        tool_call_id="tool_call_123",
        tool_name="core.http_request",
        tool_call_args={"url": "https://example.com"},
        status=ApprovalStatus.PENDING,
    )
    session.add_all(
        [
            agent_session,
            legacy_entry,
            prior_entry,
            approval_entry,
            continuation_entry,
            approval,
        ]
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    visible_messages = await service.list_messages(agent_session.id)
    visible_message_ids = {
        message.id for message in visible_messages if message.message is not None
    }

    assert visible_message_ids == {
        str(legacy_entry.id),
        str(prior_entry.id),
        str(approval_entry.id),
    }
    assert str(continuation_entry.id) not in visible_message_ids

    all_messages = await service.list_messages(agent_session.id, include_active=True)
    all_message_ids = {
        message.id for message in all_messages if message.message is not None
    }
    assert all_message_ids == {
        str(legacy_entry.id),
        str(prior_entry.id),
        str(approval_entry.id),
        str(continuation_entry.id),
    }


@pytest.mark.anyio
async def test_replace_interrupt_with_tool_results_replaces_legacy_interrupted_row(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Approval chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        sdk_session_id="sdk-session",
    )
    assistant_uuid = str(uuid.uuid4())
    session.add(agent_session)
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": assistant_uuid,
                "sessionId": "sdk-session",
                "type": "assistant",
                "timestamp": "2026-03-18T00:00:00Z",
                "cwd": "/home/agent",
                "version": "2.0.72",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_123",
                            "name": "core__http_request",
                            "input": {"url": "https://example.com"},
                        }
                    ],
                },
            },
        )
    )
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": str(uuid.uuid4()),
                "parentUuid": assistant_uuid,
                "sessionId": "sdk-session",
                "type": "user",
                "timestamp": "2026-03-18T00:00:01Z",
                "cwd": "/home/agent",
                "version": "2.0.72",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_123",
                            "content": "interrupted",
                            "is_error": True,
                        }
                    ],
                },
            },
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    await service.replace_interrupt_with_tool_results(
        agent_session.id,
        [
            ToolExecutionResult(
                tool_call_id="call_123",
                tool_name="core.http_request",
                result={"status": "success"},
                is_error=False,
            )
        ],
    )

    history = await service.get_session_history(agent_session.id)
    tool_result_blocks = [
        block
        for entry in history
        for block in entry.content.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]

    assert len(tool_result_blocks) == 1
    [tool_result] = tool_result_blocks
    assert tool_result["tool_use_id"] == "call_123"
    assert tool_result["is_error"] is False
    assert orjson.loads(tool_result["content"]) == {"status": "success"}


@pytest.mark.anyio
async def test_replace_interrupt_with_tool_results_tags_active_run_id(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """The inserted tool_result must be tagged with the active run id so the
    mid-turn filter hides it alongside its tool_use row. A NULL tag would leave
    the tool_result visible while its tool_use stays hidden — a dangling result.
    """
    run_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Approval chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        sdk_session_id="sdk-session",
        curr_run_id=run_id,
    )
    assistant_uuid = str(uuid.uuid4())
    session.add(agent_session)
    # Assistant tool_use row is tagged with the active run id (durability-only).
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            curr_run_id=run_id,
            content={
                "uuid": assistant_uuid,
                "sessionId": "sdk-session",
                "type": "assistant",
                "timestamp": "2026-03-18T00:00:00Z",
                "cwd": "/home/agent",
                "version": "2.0.72",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_123",
                            "name": "core__http_request",
                            "input": {"url": "https://example.com"},
                        }
                    ],
                },
            },
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    await service.replace_interrupt_with_tool_results(
        agent_session.id,
        [
            ToolExecutionResult(
                tool_call_id="call_123",
                tool_name="core.http_request",
                result={"status": "success"},
                is_error=False,
            )
        ],
    )

    # Every persisted row for this turn — the assistant tool_use and the newly
    # inserted tool_result — must carry the active run id. The mid-turn filter
    # (list_messages, include_active=False) keys off this tag: a NULL on the
    # tool_result would leave it visible while its tool_use stays hidden.
    rows = (
        await session.execute(
            select(AgentSessionHistory.curr_run_id, AgentSessionHistory.content)
            .where(AgentSessionHistory.session_id == agent_session.id)
            .order_by(AgentSessionHistory.surrogate_id)
        )
    ).all()
    by_type = {content.get("type"): crid for crid, content in rows}
    assert by_type == {"assistant": run_id, "user": run_id}


@pytest.mark.anyio
async def test_replace_interrupt_with_tool_results_spans_split_assistant_rows(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Parallel tool calls span multiple single-block assistant rows.

    Claude Code writes one assistant JSONL row per tool_use block (sharing the
    API message id), so reconciliation must union the pending tool call IDs
    across rows and anchor the combined tool_result entry after the last one.
    """
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Approval chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        sdk_session_id="sdk-session",
    )
    session.add(agent_session)

    first_assistant_uuid = str(uuid.uuid4())
    second_assistant_uuid = str(uuid.uuid4())
    for row_uuid, tool_call_id, url in (
        (first_assistant_uuid, "call_1", "https://example.com"),
        (second_assistant_uuid, "call_2", "https://example.org"),
    ):
        session.add(
            AgentSessionHistory(
                session_id=agent_session.id,
                workspace_id=svc_role.workspace_id,
                kind=MessageKind.CHAT_MESSAGE.value,
                content={
                    "uuid": row_uuid,
                    "sessionId": "sdk-session",
                    "type": "assistant",
                    "timestamp": "2026-03-18T00:00:00Z",
                    "message": {
                        "id": "msg_batch",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tool_call_id,
                                "name": "core__http_request",
                                "input": {"url": url},
                            }
                        ],
                    },
                },
            )
        )
    # SDK interrupt placeholders for both pending calls.
    for tool_call_id in ("call_1", "call_2"):
        session.add(
            AgentSessionHistory(
                session_id=agent_session.id,
                workspace_id=svc_role.workspace_id,
                kind=MessageKind.CHAT_MESSAGE.value,
                content={
                    "uuid": str(uuid.uuid4()),
                    "parentUuid": second_assistant_uuid,
                    "sessionId": "sdk-session",
                    "type": "user",
                    "timestamp": "2026-03-18T00:00:01Z",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_call_id,
                                "content": "interrupted",
                                "is_error": True,
                            }
                        ],
                    },
                },
            )
        )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    await service.replace_interrupt_with_tool_results(
        agent_session.id,
        [
            ToolExecutionResult(
                tool_call_id="call_1",
                tool_name="core.http_request",
                result={"status": "success"},
                is_error=False,
            ),
            ToolExecutionResult(
                tool_call_id="call_2",
                tool_name="core.http_request",
                result={"status": "success"},
                is_error=False,
            ),
        ],
    )

    history = await service.get_session_history(agent_session.id)
    tool_result_entries = [
        entry
        for entry in history
        if entry.content.get("type") == "user"
        and any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in entry.content.get("message", {}).get("content", [])
        )
    ]

    # The interrupt placeholders are replaced by ONE user row answering both
    # tool calls together, anchored after the last assistant row.
    assert len(tool_result_entries) == 1
    [entry] = tool_result_entries
    assert entry.content.get("parentUuid") == second_assistant_uuid
    blocks = entry.content["message"]["content"]
    assert {block["tool_use_id"] for block in blocks} == {"call_1", "call_2"}
    assert all(block["is_error"] is False for block in blocks)


@pytest.mark.anyio
async def test_replace_interrupt_with_tool_results_spans_split_rows_without_message_id(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """When SDK rows omit message.id, contiguous assistant rows are one batch."""
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Approval chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        sdk_session_id="sdk-session",
    )
    session.add(agent_session)

    first_assistant_uuid = str(uuid.uuid4())
    second_assistant_uuid = str(uuid.uuid4())
    for row_uuid, tool_call_id, url in (
        (first_assistant_uuid, "call_1", "https://example.com"),
        (second_assistant_uuid, "call_2", "https://example.org"),
    ):
        session.add(
            AgentSessionHistory(
                session_id=agent_session.id,
                workspace_id=svc_role.workspace_id,
                kind=MessageKind.CHAT_MESSAGE.value,
                content={
                    "uuid": row_uuid,
                    "sessionId": "sdk-session",
                    "type": "assistant",
                    "timestamp": "2026-03-18T00:00:00Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tool_call_id,
                                "name": "core__http_request",
                                "input": {"url": url},
                            }
                        ],
                    },
                },
            )
        )
    for tool_call_id in ("call_1", "call_2"):
        session.add(
            AgentSessionHistory(
                session_id=agent_session.id,
                workspace_id=svc_role.workspace_id,
                kind=MessageKind.CHAT_MESSAGE.value,
                content={
                    "uuid": str(uuid.uuid4()),
                    "parentUuid": second_assistant_uuid,
                    "sessionId": "sdk-session",
                    "type": "user",
                    "timestamp": "2026-03-18T00:00:01Z",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_call_id,
                                "content": "interrupted",
                                "is_error": True,
                            }
                        ],
                    },
                },
            )
        )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    await service.replace_interrupt_with_tool_results(
        agent_session.id,
        [
            ToolExecutionResult(
                tool_call_id="call_1",
                tool_name="core.http_request",
                result={"status": "success"},
                is_error=False,
            ),
            ToolExecutionResult(
                tool_call_id="call_2",
                tool_name="core.http_request",
                result={"status": "success"},
                is_error=False,
            ),
        ],
    )

    history = await service.get_session_history(agent_session.id)
    tool_result_entries = [
        entry
        for entry in history
        if entry.content.get("type") == "user"
        and any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in entry.content.get("message", {}).get("content", [])
        )
    ]

    assert len(tool_result_entries) == 1
    [entry] = tool_result_entries
    assert entry.content.get("parentUuid") == second_assistant_uuid
    blocks = entry.content["message"]["content"]
    assert {block["tool_use_id"] for block in blocks} == {"call_1", "call_2"}
    assert all(block["is_error"] is False for block in blocks)


@pytest.mark.anyio
async def test_replace_interrupt_with_tool_results_does_not_duplicate_existing_result(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Do not append a duplicate tool_result when replacement is retried."""
    # Arrange: create the session that owns the approval continuation history.
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Approval chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        sdk_session_id="sdk-session",
    )
    assistant_uuid = str(uuid.uuid4())
    session.add(agent_session)
    # Arrange: persist the assistant tool_use that the replacement logic anchors
    # on before looking for later tool_result rows.
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": assistant_uuid,
                "sessionId": "sdk-session",
                "type": "assistant",
                "timestamp": "2026-03-18T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_123",
                            "name": "core__http_request",
                            "input": {"url": "https://example.com"},
                        }
                    ],
                },
            },
        )
    )
    # Arrange: simulate an activity retry after the replacement row was already
    # committed. The existing real tool_result should make the replacement path
    # a no-op.
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": str(uuid.uuid4()),
                "parentUuid": assistant_uuid,
                "sessionId": "sdk-session",
                "type": "user",
                "timestamp": "2026-03-18T00:00:01Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_123",
                            "content": '{"status":"already-recorded"}',
                            "is_error": False,
                        }
                    ],
                },
            },
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    await service.replace_interrupt_with_tool_results(
        agent_session.id,
        [
            ToolExecutionResult(
                tool_call_id="call_123",
                tool_name="core.http_request",
                result={"status": "success"},
                is_error=False,
            )
        ],
    )

    history = await service.get_session_history(agent_session.id)
    # The SQL-backed existence check should prevent appending a second
    # tool_result for the same tool call.
    tool_result_blocks = [
        block
        for entry in history
        for block in entry.content.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]

    assert len(tool_result_blocks) == 1
    assert tool_result_blocks[0]["content"] == '{"status":"already-recorded"}'


@pytest.mark.anyio
async def test_replace_interrupt_with_tool_results_preserves_interrupt_without_assistant_uuid(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Approval chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        sdk_session_id="sdk-session",
    )
    session.add(agent_session)
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "sessionId": "sdk-session",
                "type": "assistant",
                "timestamp": "2026-03-18T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_123",
                            "name": "core__http_request",
                            "input": {"url": "https://example.com"},
                        }
                    ],
                },
            },
        )
    )
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": str(uuid.uuid4()),
                "sessionId": "sdk-session",
                "type": "user",
                "timestamp": "2026-03-18T00:00:01Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_123",
                            "content": "interrupted",
                            "is_error": True,
                        }
                    ],
                },
            },
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    await service.replace_interrupt_with_tool_results(
        agent_session.id,
        [
            ToolExecutionResult(
                tool_call_id="call_123",
                tool_name="core.http_request",
                result={"status": "success"},
                is_error=False,
            )
        ],
    )

    history = await service.get_session_history(agent_session.id)
    tool_result_blocks = [
        block
        for entry in history
        for block in entry.content.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]

    assert len(tool_result_blocks) == 1
    [tool_result] = tool_result_blocks
    assert tool_result["tool_use_id"] == "call_123"
    assert tool_result["content"] == "interrupted"
    assert tool_result["is_error"] is True


@pytest.mark.anyio
async def test_replace_interrupt_with_tool_results_ignores_existing_interrupt_result(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    # Arrange: create a session with the assistant tool_use that originally
    # triggered human approval.
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Approval chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        sdk_session_id="sdk-session",
    )
    assistant_uuid = str(uuid.uuid4())
    session.add(agent_session)
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": assistant_uuid,
                "sessionId": "sdk-session",
                "type": "assistant",
                "timestamp": "2026-03-18T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_123",
                            "name": "core__http_request",
                            "input": {"url": "https://example.com"},
                        }
                    ],
                },
            },
        )
    )
    # This row has the same tool_call_id as the approved result, but it is only
    # the SDK's approval-interrupt placeholder. The idempotency check must
    # ignore it, otherwise we would skip writing the real approved result.
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": str(uuid.uuid4()),
                "parentUuid": assistant_uuid,
                "sessionId": "sdk-session",
                "type": "user",
                "timestamp": "2026-03-18T00:00:01Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_123",
                            "content": "interrupted",
                            "is_error": True,
                        }
                    ],
                },
            },
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    # Act: replace the interrupted approval artifact with the actual tool
    # execution result produced after approval.
    await service.replace_interrupt_with_tool_results(
        agent_session.id,
        [
            ToolExecutionResult(
                tool_call_id="call_123",
                tool_name="core.http_request",
                result={"status": "success"},
                is_error=False,
            )
        ],
    )

    history = await service.get_session_history(agent_session.id)
    # Assert: the placeholder was deleted and exactly one real, non-error
    # tool_result was inserted for the approved tool call.
    tool_result_blocks = [
        block
        for entry in history
        for block in entry.content.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]

    assert len(tool_result_blocks) == 1
    assert tool_result_blocks[0]["tool_use_id"] == "call_123"
    assert tool_result_blocks[0]["is_error"] is False
    assert orjson.loads(tool_result_blocks[0]["content"]) == {"status": "success"}


@pytest.mark.anyio
async def test_replace_interrupt_with_tool_results_requires_same_assistant_turn(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Approval chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        sdk_session_id="sdk-session",
    )
    older_assistant_uuid = str(uuid.uuid4())
    newer_assistant_uuid = str(uuid.uuid4())
    session.add(agent_session)
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": older_assistant_uuid,
                "sessionId": "sdk-session",
                "type": "assistant",
                "timestamp": "2026-03-18T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_old",
                            "name": "core__http_request",
                            "input": {"url": "https://old.example.com"},
                        }
                    ],
                },
            },
        )
    )
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": str(uuid.uuid4()),
                "parentUuid": older_assistant_uuid,
                "sessionId": "sdk-session",
                "type": "user",
                "timestamp": "2026-03-18T00:00:01Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_old",
                            "content": "interrupted",
                            "is_error": True,
                        }
                    ],
                },
            },
        )
    )
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": newer_assistant_uuid,
                "sessionId": "sdk-session",
                "type": "assistant",
                "timestamp": "2026-03-18T00:00:02Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_new",
                            "name": "core__http_request",
                            "input": {"url": "https://new.example.com"},
                        }
                    ],
                },
            },
        )
    )
    session.add(
        AgentSessionHistory(
            session_id=agent_session.id,
            workspace_id=svc_role.workspace_id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={
                "uuid": str(uuid.uuid4()),
                "parentUuid": newer_assistant_uuid,
                "sessionId": "sdk-session",
                "type": "user",
                "timestamp": "2026-03-18T00:00:03Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_new",
                            "content": "interrupted",
                            "is_error": True,
                        }
                    ],
                },
            },
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    await service.replace_interrupt_with_tool_results(
        agent_session.id,
        [
            ToolExecutionResult(
                tool_call_id="call_old",
                tool_name="core.http_request",
                result={"status": "old"},
                is_error=False,
            ),
            ToolExecutionResult(
                tool_call_id="call_new",
                tool_name="core.http_request",
                result={"status": "new"},
                is_error=False,
            ),
        ],
    )

    history = await service.get_session_history(agent_session.id)
    tool_result_blocks = [
        block
        for entry in history
        for block in entry.content.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]

    assert len(tool_result_blocks) == 2
    assert {block["tool_use_id"] for block in tool_result_blocks} == {
        "call_old",
        "call_new",
    }
    assert all(block["content"] == "interrupted" for block in tool_result_blocks)
    assert all(block["is_error"] is True for block in tool_result_blocks)


@pytest.mark.anyio
async def test_run_turn_continue_with_inbox_source_for_non_external_session(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    curr_run_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=curr_run_id,
        active_stream_id=uuid.uuid4(),
    )
    session.add(agent_session)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=agent_session.id,
            tool_call_id="tool_call_123",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    execute_update = AsyncMock(return_value=None)
    fake_redis = _ApprovalContinuationRedis()
    with _mock_approval_continuation_dependencies(fake_redis, execute_update):
        result = await service.run_turn(agent_session.id, continuation)

    # A real approval submission rotates the stream and returns the fresh id.
    assert result is not None
    assert result.active_stream_id is not None
    execute_update.assert_awaited_once()
    assert len(fake_redis.streams) == 1


@pytest.mark.anyio
async def test_run_turn_continue_partial_approval_reports_not_resumed(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    curr_run_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=curr_run_id,
    )
    session.add(agent_session)
    for tool_call_id in ("tool_call_123", "tool_call_456"):
        session.add(
            Approval(
                workspace_id=svc_role.workspace_id,
                session_id=agent_session.id,
                tool_call_id=tool_call_id,
                tool_name="core.http_request",
                tool_call_args={"url": "https://example.com"},
                status=ApprovalStatus.PENDING,
            )
        )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    execute_update = AsyncMock(return_value=False)
    fake_redis = _ApprovalContinuationRedis()
    with _mock_approval_continuation_dependencies(fake_redis, execute_update):
        result = await service.run_turn(agent_session.id, continuation)

    assert result is not None
    assert result.curr_run_id == curr_run_id
    assert result.active_stream_id is not None
    execute_update.assert_awaited_once()

    approved = await session.scalar(
        select(Approval).where(
            Approval.session_id == agent_session.id,
            Approval.tool_call_id == "tool_call_123",
        )
    )
    assert approved is not None
    assert approved.status is ApprovalStatus.APPROVED
    assert approved.decision == {"value": True, "metadata": {"source": "inbox"}}

    pending = await session.scalar(
        select(Approval).where(
            Approval.session_id == agent_session.id,
            Approval.tool_call_id == "tool_call_456",
        )
    )
    assert pending is not None
    assert pending.status is ApprovalStatus.PENDING

    stream_key = (
        f"agent-stream:{svc_role.workspace_id}:{agent_session.id}:"
        f"{result.active_stream_id}"
    )
    stream_payloads = [
        orjson.loads(fields[tokens.DATA_KEY])
        for _, fields in fake_redis.streams[stream_key]
    ]
    assert stream_payloads[-1] == {
        tokens.END_TOKEN: tokens.END_TOKEN_VALUE,
        "terminal": False,
        "reason": "approval_pending",
    }


async def _partial_batch_session(
    session: AsyncSession,
    svc_role: Role,
) -> tuple[AgentSession, AgentSessionService]:
    """Seed a session whose parallel batch has one decided and one pending call."""
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
    )
    session.add(agent_session)
    for tool_call_id in ("tool_call_123", "tool_call_456"):
        session.add(
            Approval(
                workspace_id=svc_role.workspace_id,
                session_id=agent_session.id,
                tool_call_id=tool_call_id,
                tool_name="core.http_request",
                tool_call_args={"url": "https://example.com"},
                status=ApprovalStatus.PENDING,
            )
        )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    first = ContinueRunRequest(
        decisions=[ApprovalDecision(tool_call_id="tool_call_123", action="approve")],
        source="inbox",
    )
    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), AsyncMock(return_value=False)
    ):
        await service.run_turn(agent_session.id, first)

    return agent_session, service


@pytest.mark.anyio
async def test_run_turn_continue_matching_resubmission_is_noop(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """A replayed decision for an already-decided call must not 400."""
    agent_session, service = await _partial_batch_session(session, svc_role)

    execute_update = AsyncMock(return_value=False)
    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), execute_update
    ):
        result = await service.run_turn(
            agent_session.id,
            ContinueRunRequest(
                decisions=[
                    ApprovalDecision(tool_call_id="tool_call_123", action="approve")
                ],
                source="inbox",
            ),
        )

    assert result is None
    execute_update.assert_not_awaited()

    pending = await session.scalar(
        select(Approval).where(
            Approval.session_id == agent_session.id,
            Approval.tool_call_id == "tool_call_456",
        )
    )
    assert pending is not None
    assert pending.status is ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_run_turn_continue_deny_replay_across_surfaces_is_noop(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Each surface supplies its own deny reason; the outcome still matches."""
    agent_session = await _seed_approval_session(
        session,
        svc_role,
        approvals=[
            Approval(
                tool_call_id="tool_call_123",
                tool_name="core.http_request",
                tool_call_args={"url": "https://example.com"},
                status=ApprovalStatus.REJECTED,
                decision={
                    "kind": "tool-denied",
                    "message": "Tool denied by user",
                    "metadata": {"source": "inbox"},
                },
            ),
            Approval(
                tool_call_id="tool_call_456",
                tool_name="core.http_request",
                tool_call_args={"url": "https://example.com"},
                status=ApprovalStatus.PENDING,
            ),
        ],
    )

    service = AgentSessionService(session=session, role=svc_role)
    execute_update = AsyncMock(return_value=True)
    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), execute_update
    ):
        result = await service.run_turn(
            agent_session.id,
            ContinueRunRequest(
                decisions=[
                    # Slack's default reason differs from the recorded inbox one.
                    ApprovalDecision(
                        tool_call_id="tool_call_123",
                        action="deny",
                        reason="The tool call was denied.",
                    ),
                    ApprovalDecision(tool_call_id="tool_call_456", action="approve"),
                ],
                source="slack",
            ),
        )

    assert result is not None
    execute_update.assert_awaited_once()
    submitted = execute_update.await_args
    assert submitted is not None
    assert set(submitted.args[1].approvals) == {"tool_call_456"}


@pytest.mark.anyio
async def test_run_turn_continue_conflicting_resubmission_raises(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """A stale card contradicting a recorded decision must not read as accepted."""
    agent_session, service = await _partial_batch_session(session, svc_role)

    execute_update = AsyncMock(return_value=False)
    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), execute_update
    ):
        with pytest.raises(TracecatConflictError):
            await service.run_turn(
                agent_session.id,
                ContinueRunRequest(
                    decisions=[
                        ApprovalDecision(tool_call_id="tool_call_123", action="deny")
                    ],
                    source="slack",
                ),
            )

    execute_update.assert_not_awaited()
    approved = await session.scalar(
        select(Approval).where(
            Approval.session_id == agent_session.id,
            Approval.tool_call_id == "tool_call_123",
        )
    )
    assert approved is not None
    assert approved.status is ApprovalStatus.APPROVED


@pytest.mark.anyio
async def test_run_turn_continue_mixed_resubmission_forwards_pending_only(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Resending a decided call alongside a pending one forwards only the latter."""
    agent_session, service = await _partial_batch_session(session, svc_role)

    execute_update = AsyncMock(return_value=True)
    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), execute_update
    ):
        result = await service.run_turn(
            agent_session.id,
            ContinueRunRequest(
                decisions=[
                    ApprovalDecision(tool_call_id="tool_call_123", action="approve"),
                    ApprovalDecision(tool_call_id="tool_call_456", action="approve"),
                ],
                source="inbox",
            ),
        )

    assert result is not None
    execute_update.assert_awaited_once()
    assert execute_update.await_args is not None
    submitted = execute_update.await_args.args[1]
    assert set(submitted.approvals) == {"tool_call_456"}


@pytest.mark.anyio
async def test_run_turn_continue_conflicting_after_batch_settled_raises(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Once nothing is pending, a contradicting replay still must not report success."""
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
    )
    session.add(agent_session)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=agent_session.id,
            tool_call_id="tool_call_123",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.APPROVED,
            decision={"value": True, "metadata": {"source": "inbox"}},
        )
    )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    with _mock_approval_continuation_dependencies(
        _ApprovalContinuationRedis(), AsyncMock(return_value=False)
    ):
        with pytest.raises(TracecatConflictError):
            await service.run_turn(
                agent_session.id,
                ContinueRunRequest(
                    decisions=[
                        ApprovalDecision(tool_call_id="tool_call_123", action="deny")
                    ],
                    source="slack",
                ),
            )


@pytest.mark.anyio
async def test_run_turn_continue_old_worker_none_defaults_to_resumed(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    curr_run_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=curr_run_id,
        active_stream_id=uuid.uuid4(),
    )
    session.add(agent_session)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=agent_session.id,
            tool_call_id="tool_call_123",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    execute_update = AsyncMock(return_value=None)
    fake_redis = _ApprovalContinuationRedis()
    with _mock_approval_continuation_dependencies(fake_redis, execute_update):
        result = await service.run_turn(agent_session.id, continuation)

    assert result is not None
    assert result.curr_run_id == curr_run_id
    assert result.active_stream_id is not None
    execute_update.assert_awaited_once()


def test_approval_submission_key_is_stable_for_tool_call_set() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    first_key = AgentSessionService._approval_submission_key(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        tool_call_ids=("tool_call_123", "tool_call_456"),
    )
    reordered_key = AgentSessionService._approval_submission_key(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        tool_call_ids=("tool_call_456", "tool_call_123"),
    )
    different_key = AgentSessionService._approval_submission_key(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        tool_call_ids=("tool_call_123",),
    )

    assert first_key == reordered_key
    assert first_key != different_key


@pytest.mark.anyio
async def test_run_turn_continue_override_maps_to_tool_approved(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    curr_run_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=curr_run_id,
    )
    session.add(agent_session)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=agent_session.id,
            tool_call_id="tool_call_123",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="override",
                override_args={"url": "https://modified.example.com"},
            )
        ],
        source="inbox",
    )

    execute_update = AsyncMock(return_value=None)
    fake_redis = _ApprovalContinuationRedis()
    with _mock_approval_continuation_dependencies(fake_redis, execute_update):
        result = await service.run_turn(agent_session.id, continuation)

    execute_update.assert_awaited_once()
    await_args = execute_update.await_args
    assert await_args is not None
    submission = await_args.args[1]
    decision = submission.approvals["tool_call_123"]
    assert isinstance(decision, ToolApproved)
    assert decision.override_args == {"url": "https://modified.example.com"}

    # Stream rotation: the continuation mints a fresh stream id, pins it on the
    # session row, threads it into the submission payload, and returns it so the
    # HTTP layer attaches to the fresh (suffix-only) stream.
    assert result is not None
    assert result.active_stream_id is not None
    assert submission.new_stream_id == result.active_stream_id
    assert len(fake_redis.streams) == 1
    await session.refresh(agent_session)
    assert agent_session.active_stream_id == result.active_stream_id


@pytest.mark.anyio
async def test_approval_continuation_retry_reuses_stream_after_ambiguous_failure(
    session: AsyncSession,
    svc_role: Role,
    pending_approval_session: tuple[AgentSession, ContinueRunRequest, uuid.UUID],
) -> None:
    agent_session, continuation, previous_stream_id = pending_approval_session
    service = AgentSessionService(session=session, role=svc_role)
    fake_redis = _ApprovalContinuationRedis()
    execute_update = AsyncMock(
        side_effect=[WorkflowUpdateRPCTimeoutOrCancelledError(), None]
    )

    with _mock_approval_continuation_dependencies(fake_redis, execute_update):
        with pytest.raises(WorkflowUpdateRPCTimeoutOrCancelledError):
            await service.run_turn(agent_session.id, continuation)

        await session.refresh(agent_session)
        continuation_stream_id = agent_session.active_stream_id
        assert continuation_stream_id is not None
        assert continuation_stream_id != previous_stream_id

        # The update may have succeeded and completed its stream even though
        # the client observed an ambiguous timeout. The retry must still attach
        # to this attempt and reuse its Temporal update ID.
        stream_key = (
            f"agent-stream:{svc_role.workspace_id}:{agent_session.id}:"
            f"{continuation_stream_id}"
        )
        await fake_redis.xadd(
            stream_key,
            {
                tokens.DATA_KEY: orjson.dumps(
                    {tokens.END_TOKEN: tokens.END_TOKEN_VALUE}
                ).decode()
            },
            maxlen=10000,
            approximate=True,
        )

        result = await service.run_turn(agent_session.id, continuation)

    assert result is not None
    assert result.active_stream_id == continuation_stream_id
    assert execute_update.await_count == 2
    first_call, retry_call = execute_update.await_args_list
    assert first_call.args[1].new_stream_id == continuation_stream_id
    assert retry_call.args[1].new_stream_id == continuation_stream_id
    expected_update_id = f"set-approvals:{continuation_stream_id}"
    assert first_call.kwargs["id"] == expected_update_id
    assert retry_call.kwargs["id"] == expected_update_id

    assert len(fake_redis.streams[stream_key]) == 2
    marker = orjson.loads(fake_redis.streams[stream_key][0][1][tokens.DATA_KEY])
    assert agent_session.curr_run_id is not None
    submission_key = service._approval_submission_key(
        workspace_id=service.workspace_id,
        session_id=agent_session.id,
        run_id=agent_session.curr_run_id,
        tool_call_ids=("tool_call_123",),
    )
    assert marker == {
        "kind": "approval-continuation-start",
        "submission_key": submission_key,
        "previous_stream_id": str(previous_stream_id),
    }


@pytest.mark.anyio
async def test_approval_continuation_rejection_restores_and_retries_fresh_stream(
    session: AsyncSession,
    svc_role: Role,
    pending_approval_session: tuple[AgentSession, ContinueRunRequest, uuid.UUID],
) -> None:
    agent_session, continuation, previous_stream_id = pending_approval_session
    service = AgentSessionService(session=session, role=svc_role)
    fake_redis = _ApprovalContinuationRedis()
    execute_update = AsyncMock(
        side_effect=[
            WorkflowUpdateFailedError(
                ApplicationError("approval update rejected", non_retryable=True)
            ),
            None,
        ]
    )

    with _mock_approval_continuation_dependencies(fake_redis, execute_update):
        with pytest.raises(WorkflowUpdateFailedError):
            await service.run_turn(agent_session.id, continuation)

        rejected_stream_id = execute_update.await_args_list[0].args[1].new_stream_id
        await session.refresh(agent_session)
        assert agent_session.active_stream_id == previous_stream_id

        result = await service.run_turn(agent_session.id, continuation)

    assert result is not None
    assert result.active_stream_id not in {previous_stream_id, rejected_stream_id}
    retry_stream_id = result.active_stream_id
    assert execute_update.await_count == 2
    assert execute_update.await_args_list[0].kwargs["id"] == (
        f"set-approvals:{rejected_stream_id}"
    )
    assert execute_update.await_args_list[1].kwargs["id"] == (
        f"set-approvals:{retry_stream_id}"
    )

    rejected_stream_key = (
        f"agent-stream:{svc_role.workspace_id}:{agent_session.id}:{rejected_stream_id}"
    )
    rejected_entries = fake_redis.streams[rejected_stream_key]
    assert len(rejected_entries) == 2
    assert orjson.loads(rejected_entries[-1][1][tokens.DATA_KEY]) == {
        tokens.END_TOKEN: tokens.END_TOKEN_VALUE
    }


@pytest.mark.anyio
async def test_approval_continuation_setup_failure_precedes_update(
    session: AsyncSession,
    svc_role: Role,
    pending_approval_session: tuple[AgentSession, ContinueRunRequest, uuid.UUID],
) -> None:
    agent_session, continuation, previous_stream_id = pending_approval_session
    service = AgentSessionService(session=session, role=svc_role)
    fake_redis = _ApprovalContinuationRedis()
    fake_redis.fail_next_xadd = True
    execute_update = AsyncMock(return_value=None)

    with (
        _mock_approval_continuation_dependencies(fake_redis, execute_update),
        pytest.raises(RuntimeError, match="stream write unavailable"),
    ):
        await service.run_turn(agent_session.id, continuation)

    execute_update.assert_not_awaited()
    await session.refresh(agent_session)
    assert agent_session.active_stream_id == previous_stream_id


@pytest.mark.anyio
async def test_approval_continuation_cas_loser_reuses_winning_stream(
    session: AsyncSession,
    svc_role: Role,
    pending_approval_session: tuple[AgentSession, ContinueRunRequest, uuid.UUID],
) -> None:
    agent_session, _, previous_stream_id = pending_approval_session
    assert agent_session.curr_run_id is not None
    service = AgentSessionService(session=session, role=svc_role)
    submission_key = service._approval_submission_key(
        workspace_id=service.workspace_id,
        session_id=agent_session.id,
        run_id=agent_session.curr_run_id,
        tool_call_ids=("tool_call_123",),
    )
    winning_stream_id = uuid.uuid4()
    winning_stream_key = (
        f"agent-stream:{svc_role.workspace_id}:{agent_session.id}:{winning_stream_id}"
    )
    fake_redis = _ApprovalContinuationRedis()
    fake_redis.streams[winning_stream_key] = [
        (
            "1-0",
            {
                tokens.DATA_KEY: orjson.dumps(
                    {
                        "kind": "approval-continuation-start",
                        "submission_key": submission_key,
                        "previous_stream_id": str(previous_stream_id),
                    }
                ).decode()
            },
        )
    ]

    # Simulate a concurrent request installing the winning stream without
    # synchronizing this request's already-loaded ORM object.
    await session.execute(
        update(AgentSession)
        .where(AgentSession.id == agent_session.id)
        .values(active_stream_id=winning_stream_id)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    assert agent_session.active_stream_id == previous_stream_id

    with patch(
        "tracecat.agent.stream.connector.get_redis_client",
        AsyncMock(return_value=fake_redis),
    ):
        attempt = await service._prepare_approval_continuation_attempt(
            agent_session=agent_session,
            curr_run_id=agent_session.curr_run_id,
            submission_key=submission_key,
        )

    assert attempt.stream_id == winning_stream_id
    assert agent_session.active_stream_id == winning_stream_id
    assert list(fake_redis.streams) == [winning_stream_key]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("submitted_ids", "error_match"),
    [
        ([], "do not match"),
        (["tool_call_123", "unexpected_call"], "do not match"),
        (["tool_call_123", "tool_call_123"], "duplicate"),
    ],
)
async def test_run_turn_rejects_invalid_approval_ids_before_stream_rotation(
    session: AsyncSession,
    svc_role: Role,
    submitted_ids: list[str],
    error_match: str,
) -> None:
    previous_stream_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
        active_stream_id=previous_stream_id,
    )
    session.add(agent_session)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=agent_session.id,
            tool_call_id="tool_call_123",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(tool_call_id=tool_call_id, action="approve")
            for tool_call_id in submitted_ids
        ],
        source="inbox",
    )
    get_temporal_client = AsyncMock()
    new_stream = AsyncMock()

    with (
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            get_temporal_client,
        ),
        patch(
            "tracecat.agent.session.service.AgentStream.new",
            new_stream,
        ),
        pytest.raises(ValueError, match=error_match),
    ):
        await service.run_turn(agent_session.id, continuation)

    get_temporal_client.assert_not_awaited()
    new_stream.assert_not_awaited()
    await session.refresh(agent_session)
    assert agent_session.active_stream_id == previous_stream_id


@pytest.mark.anyio
@pytest.mark.parametrize(
    "seed_decided,expectation",
    [
        pytest.param(False, pytest.raises(ValueError), id="unknown-tool-call-rejected"),
        pytest.param(True, contextlib.nullcontext(), id="settled-replay-is-noop"),
    ],
)
async def test_run_turn_continue_without_pending_approvals(
    session: AsyncSession,
    svc_role: Role,
    seed_decided: bool,
    expectation: contextlib.AbstractContextManager[object],
) -> None:
    """With nothing pending, only a matching settled replay is a no-op."""
    rotated_stream_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
        active_stream_id=rotated_stream_id,
    )
    session.add(agent_session)
    if seed_decided:
        session.add(
            Approval(
                workspace_id=svc_role.workspace_id,
                session_id=agent_session.id,
                tool_call_id="tool_call_123",
                tool_name="core.http_request",
                tool_call_args={"url": "https://example.com"},
                status=ApprovalStatus.APPROVED,
                decision={"value": True, "metadata": {"source": "inbox"}},
            )
        )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    fake_handle = SimpleNamespace(execute_update=AsyncMock(return_value=None))
    get_workflow_handle_for = Mock(return_value=fake_handle)
    fake_client = SimpleNamespace(get_workflow_handle_for=get_workflow_handle_for)
    with patch(
        "tracecat.agent.session.service.get_temporal_client",
        AsyncMock(return_value=fake_client),
    ):
        with expectation:
            assert await service.run_turn(agent_session.id, continuation) is None

    get_workflow_handle_for.assert_not_called()
    fake_handle.execute_update.assert_not_awaited()


@pytest.mark.anyio
async def test_run_turn_continue_duplicate_submission_reuses_active_attempt(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    previous_stream_id = uuid.uuid4()
    rotated_stream_id = uuid.uuid4()
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=svc_role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
        active_stream_id=rotated_stream_id,
    )
    session.add(agent_session)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=agent_session.id,
            tool_call_id="tool_call_123",
            tool_name="core.http_request",
            tool_call_args={"url": "https://example.com"},
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()
    await session.refresh(agent_session)

    service = AgentSessionService(session=session, role=svc_role)
    continuation = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    assert agent_session.curr_run_id is not None
    submission_key = service._approval_submission_key(
        workspace_id=service.workspace_id,
        session_id=agent_session.id,
        run_id=agent_session.curr_run_id,
        tool_call_ids=("tool_call_123",),
    )
    continuation_marker = {
        "kind": "approval-continuation-start",
        "submission_key": submission_key,
        "previous_stream_id": str(previous_stream_id),
    }
    # A stream already carries this submission's continuation marker, so the
    # second submitter reuses the installed attempt rather than minting a new one.
    fake_redis = SimpleNamespace(
        xrange=AsyncMock(
            return_value=[
                (
                    "1-0",
                    {tokens.DATA_KEY: orjson.dumps(continuation_marker).decode()},
                )
            ]
        ),
        xadd=AsyncMock(return_value="1-0"),
    )
    execute_update = AsyncMock(return_value=None)
    with _mock_approval_continuation_dependencies(fake_redis, execute_update):
        result = await service.run_turn(agent_session.id, continuation)

    assert result is not None
    assert result.active_stream_id == rotated_stream_id
    await session.refresh(agent_session)
    assert result.active_stream_id == agent_session.active_stream_id
    # No fresh stream is written; the update fires on the reused stream id.
    fake_redis.xadd.assert_not_awaited()
    execute_update.assert_awaited_once()
    assert execute_update.await_args is not None
    assert execute_update.await_args.kwargs["id"] == (
        f"set-approvals:{rotated_stream_id}"
    )


@pytest.mark.anyio
async def test_run_turn_merges_basic_chat_request_instructions(
    session: AsyncSession,
    svc_role: Role,
    external_agent_session: AgentSession,
) -> None:
    service = AgentSessionService(session=session, role=svc_role)
    fake_client = SimpleNamespace(start_workflow=AsyncMock(return_value=None))

    @contextlib.asynccontextmanager
    async def _fake_build_agent_config(_session: AgentSession):
        yield AgentConfig(
            instructions="Base preset instructions",
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=[],
        )

    with (
        patch.object(service, "_build_agent_config", _fake_build_agent_config),
        patch.object(
            service,
            "has_pending_approvals",
            AsyncMock(return_value=False),
        ),
        patch.object(
            service,
            "auto_title_session_on_first_prompt",
            AsyncMock(return_value=None),
        ),
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=fake_client),
        ),
    ):
        response = await service.run_turn(
            external_agent_session.id,
            BasicChatRequest(
                message="summarize this thread",
                instructions="Slack actor context for this turn:\n- Slack email: jordan@example.com",
            ),
        )

    assert response is not None
    await_args = fake_client.start_workflow.await_args
    assert await_args is not None
    workflow_args = await_args.args[1]
    assert (
        workflow_args.agent_args.config.instructions
        == "Base preset instructions\n\nSlack actor context for this turn:\n- Slack email: jordan@example.com"
    )
    assert workflow_args.agent_args.user_prompt == "summarize this thread"


async def _seed_approval_session(
    session: AsyncSession,
    svc_role: Role,
    *,
    approvals: list[Approval],
) -> AgentSession:
    """Seed a session plus caller-supplied approval rows."""
    workspace_id = svc_role.workspace_id
    assert workspace_id is not None
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Preset chat",
        workspace_id=workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.flush()
    for approval in approvals:
        approval.workspace_id = workspace_id
        approval.session_id = agent_session.id
        session.add(approval)
    await session.commit()
    return agent_session


@pytest.mark.anyio
async def test_apply_decisions_skips_already_terminal_row(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """A deny submitted against an APPROVED row must not overwrite it."""
    agent_session = await _seed_approval_session(
        session,
        svc_role,
        approvals=[
            Approval(
                tool_call_id="tool_call_123",
                tool_name="core.http_request",
                tool_call_args={"url": "https://example.com"},
                status=ApprovalStatus.APPROVED,
                decision={"value": True},
            )
        ],
    )

    service = AgentSessionService(session=session, role=svc_role)
    await service._apply_submitted_approval_decisions(
        session_id=agent_session.id,
        approval_map={"tool_call_123": False},
        decision_metadata={},
    )

    row = (
        await session.execute(
            select(Approval.status, Approval.decision).where(
                Approval.session_id == agent_session.id,
                Approval.tool_call_id == "tool_call_123",
            )
        )
    ).one()
    assert row.status is ApprovalStatus.APPROVED
    assert row.decision == {"value": True}


@pytest.mark.anyio
async def test_apply_decisions_warns_on_missing_row(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Tool call IDs with no persisted row warn once, batched, instead of raising."""
    agent_session = await _seed_approval_session(session, svc_role, approvals=[])

    service = AgentSessionService(session=session, role=svc_role)
    with patch("tracecat.agent.session.service.logger") as mock_logger:
        await service._apply_submitted_approval_decisions(
            session_id=agent_session.id,
            approval_map={"tool_call_missing": True, "tool_call_absent": False},
            decision_metadata={},
        )

    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["tool_call_ids"] == [
        "tool_call_absent",
        "tool_call_missing",
    ]


@pytest.mark.anyio
async def test_apply_decisions_nulls_approved_by_for_unknown_user(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """A role user_id with no user row must not raise an FK violation."""
    agent_session = await _seed_approval_session(
        session,
        svc_role,
        approvals=[
            Approval(
                tool_call_id="tool_call_123",
                tool_name="core.http_request",
                tool_call_args={"url": "https://example.com"},
                status=ApprovalStatus.PENDING,
            )
        ],
    )

    ghost_role = svc_role.model_copy(update={"user_id": uuid.uuid4()})
    service = AgentSessionService(session=session, role=ghost_role)
    await service._apply_submitted_approval_decisions(
        session_id=agent_session.id,
        approval_map={"tool_call_123": True},
        decision_metadata={},
    )

    row = (
        await session.execute(
            select(Approval.status, Approval.approved_by).where(
                Approval.session_id == agent_session.id,
                Approval.tool_call_id == "tool_call_123",
            )
        )
    ).one()
    assert row.status is ApprovalStatus.APPROVED
    assert row.approved_by is None


@pytest.mark.anyio
async def test_apply_decisions_visible_to_later_read_in_same_session(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Rows already in the identity map must not read back stale after apply.

    ``_emit_approval_idle_segment`` re-selects these rows in the same session
    right after the decisions land, so a Core UPDATE must not leave the ORM
    holding the pre-update PENDING state.
    """
    agent_session = await _seed_approval_session(
        session,
        svc_role,
        approvals=[
            Approval(
                tool_call_id="tool_call_123",
                tool_name="core.http_request",
                tool_call_args={"url": "https://example.com"},
                status=ApprovalStatus.PENDING,
            )
        ],
    )

    service = AgentSessionService(session=session, role=svc_role)
    preloaded = await session.scalar(
        select(Approval).where(
            Approval.session_id == agent_session.id,
            Approval.tool_call_id == "tool_call_123",
        )
    )
    assert preloaded is not None
    assert preloaded.status is ApprovalStatus.PENDING

    await service._apply_submitted_approval_decisions(
        session_id=agent_session.id,
        approval_map={"tool_call_123": True},
        decision_metadata={"tool_call_123": {"source": "inbox"}},
    )

    items = await service._approval_stream_items(
        session_id=agent_session.id,
        tool_call_ids=["tool_call_123"],
    )
    assert [item.status for item in items] == ["approved"]

    reread = await session.scalar(
        select(Approval).where(
            Approval.session_id == agent_session.id,
            Approval.tool_call_id == "tool_call_123",
        )
    )
    assert reread is not None
    assert reread.status is ApprovalStatus.APPROVED
    assert reread.decision == {"value": True, "metadata": {"source": "inbox"}}
    # `updated_at` cannot be asserted to advance here: the test fixture runs one
    # outer transaction, and `func.now()` is transaction-start time.
    assert reread.approved_at is not None
