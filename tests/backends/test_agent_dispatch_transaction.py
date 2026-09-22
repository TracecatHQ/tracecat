"""Real PostgreSQL rollback coverage with the Temporal transport isolated.

Run against local PostgreSQL with:
uv run pytest --noconftest tests/backends/test_agent_dispatch_transaction.py
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy import Table, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable
from temporalio.api.workflowservice.v1 import StartWorkflowExecutionResponse
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.service import ConnectConfig, RPCError, RPCStatusCode, ServiceClient

from tests.database import TEST_DB_CONFIG
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.schemas import AgentWorkflowArgs
from tracecat.agent.backends.types import SessionDispatchUncertain, SessionTurnContext
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, AgentSessionHistory
from tracecat.dsl._converter import get_data_converter
from tracecat.exceptions import TracecatConflictError

pytestmark = [pytest.mark.anyio, pytest.mark.dbtest]


@pytest.fixture
async def connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(TEST_DB_CONFIG.sys_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            # Temporary ORM-shaped tables isolate every test from app data.
            # Workspace/user foreign keys are irrelevant to dispatch atomicity.
            for table in (AgentSession.__table__, AgentSessionHistory.__table__):
                assert isinstance(table, Table)
                ddl = str(
                    CreateTable(table, include_foreign_key_constraints=[]).compile(
                        dialect=engine.dialect
                    )
                ).replace("CREATE TABLE", "CREATE TEMPORARY TABLE", 1)
                await connection.execute(text(ddl))
            await connection.commit()
            yield connection
    finally:
        await engine.dispose()


class HistoryBackend(DefaultBackend):
    fail_preparation = False

    async def build_workflow_args(
        self, context: SessionTurnContext
    ) -> AgentWorkflowArgs:
        context.db.add(
            AgentSessionHistory(
                workspace_id=context.role.workspace_id,
                session_id=context.session.id,
                curr_run_id=context.run_id,
                content={"role": "user", "text": context.prompt},
            )
        )
        context.session.sdk_session_id = "prepared-native-session"
        await context.db.flush()
        if self.fail_preparation:
            raise ValueError("preparation failed")
        return await super().build_workflow_args(context)


async def make_context(db: AsyncSession) -> SessionTurnContext:
    workspace_id = uuid4()
    session = AgentSession(
        workspace_id=workspace_id,
        title="Test chat",
        entity_type="copilot",
        entity_id=workspace_id,
        backend_id="oss",
        harness_type="claude_code",
        last_error="Previous turn failed",
    )
    db.add(session)
    await db.flush()
    db.add(
        AgentSessionHistory(
            workspace_id=workspace_id,
            session_id=session.id,
            content={"role": "assistant", "text": "Earlier message"},
        )
    )
    await db.commit()
    return SessionTurnContext(
        db=db,
        session=session,
        role=Role(
            type="service",
            service_id="tracecat-api",
            organization_id=uuid4(),
            workspace_id=workspace_id,
        ),
        config=AgentConfig(model_name="test", model_provider="anthropic"),
        prompt="Investigate this alert",
        run_id=uuid4(),
        stream_id=uuid4(),
    )


def temporal_client(rpc: AsyncMock) -> Client:
    service = Mock(spec=ServiceClient)
    service.config = ConnectConfig(target_host="localhost:7233", identity="test-client")
    service._rpc_call = rpc
    return Client(service, data_converter=get_data_converter())


@pytest.mark.parametrize("failure", ["preparation", "encoding", "validation"])
async def test_rejected_start_rolls_back_history_and_retry_writes_once(
    connection: AsyncConnection, failure: str
) -> None:
    backend = HistoryBackend()
    rpc = AsyncMock(return_value=StartWorkflowExecutionResponse(run_id="run-1"))
    client = temporal_client(rpc)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with patch(
            "tracecat.agent.backends.base.get_temporal_client", return_value=client
        ):
            with pytest.MonkeyPatch.context() as patcher:
                if failure == "preparation":
                    patcher.setattr(backend, "fail_preparation", True)
                elif failure == "encoding":
                    patcher.setattr(
                        "tracecat.dsl._converter.orjson.dumps",
                        Mock(side_effect=TypeError("invalid value")),
                    )
                else:
                    # Retry-policy validation runs after payload encoding.
                    patcher.setattr(
                        backend, "retry_policy", RetryPolicy(maximum_attempts=-1)
                    )
                with pytest.raises(RuntimeError, match="before dispatch"):
                    await backend.start_turn(context)
            rpc.assert_not_awaited()
            assert not db.in_transaction()

            # Read persisted state through a fresh ORM session after rollback.
            async with AsyncSession(connection) as reader:
                saved = await reader.scalar(
                    select(AgentSession).where(AgentSession.id == session_id)
                )
                assert saved is not None
                assert saved.curr_run_id is None
                assert saved.active_stream_id is None
                assert saved.sdk_session_id is None
                assert saved.last_error == "Previous turn failed"
                assert list(
                    await reader.scalars(select(AgentSessionHistory.content))
                ) == [{"role": "assistant", "text": "Earlier message"}]

            session = await db.scalar(
                select(AgentSession).where(AgentSession.id == session_id)
            )
            assert session is not None
            retry = replace(context, session=session, run_id=uuid4(), stream_id=uuid4())
            await backend.start_turn(retry)
            rpc.assert_awaited_once()

        async with AsyncSession(connection) as reader:
            saved = await reader.scalar(
                select(AgentSession).where(AgentSession.id == session_id)
            )
            assert saved is not None
            assert saved.curr_run_id == retry.run_id
            assert saved.active_stream_id == retry.stream_id
            assert saved.sdk_session_id == "prepared-native-session"
            rows = list(await reader.scalars(select(AgentSessionHistory)))
            assert len(rows) == 2
            assert sum(row.curr_run_id == retry.run_id for row in rows) == 1


async def test_uncertain_dispatch_keeps_history_and_blocks_another_turn(
    connection: AsyncConnection,
) -> None:
    backend = HistoryBackend()
    rpc = AsyncMock(side_effect=TimeoutError("lost acknowledgement"))
    client = temporal_client(rpc)
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        with patch(
            "tracecat.agent.backends.base.get_temporal_client", return_value=client
        ):
            with pytest.raises(SessionDispatchUncertain):
                await backend.start_turn(context)
            assert not db.in_transaction()
            async with AsyncSession(connection) as reader:
                saved = await reader.scalar(
                    select(AgentSession).where(AgentSession.id == session_id)
                )
                assert saved is not None
                assert saved.curr_run_id == context.run_id
                assert saved.active_stream_id == context.stream_id
                assert len(list(await reader.scalars(select(AgentSessionHistory)))) == 2

            with pytest.raises(TracecatConflictError):
                await backend.start_turn(
                    replace(context, run_id=uuid4(), stream_id=uuid4())
                )
            rpc.assert_awaited_once()


@pytest.mark.parametrize(
    "mismatch", [None, "workspace_id", "curr_run_id", "active_stream_id"]
)
async def test_rpc_rejection_releases_only_matching_reservation(
    connection: AsyncConnection, mismatch: str | None
) -> None:
    backend = DefaultBackend()
    async with AsyncSession(connection, expire_on_commit=False) as db:
        context = await make_context(db)
        session_id = context.session.id
        # Another session with the same ownership values must remain untouched.
        other = AgentSession(
            workspace_id=context.role.workspace_id,
            title="Another chat",
            entity_type="copilot",
            entity_id=uuid4(),
            backend_id="oss",
            harness_type="claude_code",
            curr_run_id=context.run_id,
            active_stream_id=context.stream_id,
        )
        db.add(other)
        await db.commit()
        other_id = other.id

        async def reject(*_args, **_kwargs):
            if mismatch is not None:
                await db.execute(
                    update(AgentSession)
                    .where(AgentSession.id == session_id)
                    .values(**{mismatch: uuid4()})
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
            raise RPCError("request rejected", RPCStatusCode.INVALID_ARGUMENT, b"")

        rpc = AsyncMock(side_effect=reject)
        client = temporal_client(rpc)
        with patch(
            "tracecat.agent.backends.base.get_temporal_client", return_value=client
        ):
            error = RuntimeError if mismatch is None else SessionDispatchUncertain
            message = "was rejected" if mismatch is None else "requires reconciliation"
            with pytest.raises(error, match=message):
                await backend.start_turn(context)
            assert not db.in_transaction()
            async with AsyncSession(connection) as reader:
                saved = await reader.scalar(
                    select(AgentSession).where(AgentSession.id == session_id)
                )
                assert saved is not None
                if mismatch is None:
                    assert saved.curr_run_id is None
                    assert saved.active_stream_id is None
                else:
                    assert saved.curr_run_id is not None
                    assert saved.active_stream_id is not None
                untouched = await reader.scalar(
                    select(AgentSession).where(AgentSession.id == other_id)
                )
                assert untouched is not None
                assert untouched.curr_run_id == context.run_id
                assert untouched.active_stream_id == context.stream_id
            rpc.assert_awaited_once()

            if mismatch is None:
                rpc.side_effect = None
                rpc.return_value = StartWorkflowExecutionResponse(run_id="run-2")
                retry = replace(context, run_id=uuid4(), stream_id=uuid4())
                await backend.start_turn(retry)
                async with AsyncSession(connection) as reader:
                    saved = await reader.scalar(
                        select(AgentSession).where(AgentSession.id == session_id)
                    )
                    assert saved is not None
                    assert saved.curr_run_id == retry.run_id
                    assert saved.active_stream_id == retry.stream_id
