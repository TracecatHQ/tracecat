"""Runtime dispatch ownership against PostgreSQL, using an isolated schema."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tracecat.agent.session import runtime
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentSession,
    AgentSessionHistory,
    Base,
    Organization,
    Workspace,
)
from tracecat.exceptions import TracecatConflictError
from tracecat.feature_flags.enums import FeatureFlag


def test_runtime_reservation_serializes_sends_and_survives_ambiguous_start(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    monkeypatch.setattr(
        runtime.config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.AGENT_RUNTIME}
    )

    async def scenario():
        schema = f"pi_dispatch_test_{uuid4().hex}"
        engine = create_async_engine(url, poolclass=NullPool)
        scoped = engine.execution_options(schema_translate_map={None: schema})
        factory = async_sessionmaker(scoped, expire_on_commit=False)
        role = Role(
            type="service",
            service_id="tracecat-api",
            organization_id=uuid4(),
            workspace_id=uuid4(),
        )
        config = AgentConfig(model_name="test", model_provider="anthropic")
        session_ids = [uuid4(), uuid4()]
        started: list[runtime.RuntimeTurnPayload] = []

        class Client:
            async def start_workflow(self, _name, turn, **_kwargs):
                started.append(turn)
                if turn.session_id == session_ids[1]:
                    raise TimeoutError("Start response was lost")

        async def client():
            return Client()

        monkeypatch.setattr(runtime, "get_temporal_client", client)
        try:
            async with engine.begin() as db:
                await db.execute(text(f'CREATE SCHEMA "{schema}"'))
            async with scoped.begin() as db:
                await db.run_sync(Base.metadata.create_all)
            async with factory.begin() as db:
                db.add(
                    Organization(
                        id=role.organization_id, name="Test", slug=str(uuid4())
                    )
                )
                await db.flush()
                db.add(
                    Workspace(
                        id=role.workspace_id,
                        organization_id=role.organization_id,
                        name="Test",
                    )
                )
                await db.flush()
                for session_id in session_ids:
                    db.add(
                        AgentSession(
                            id=session_id,
                            workspace_id=role.workspace_id,
                            entity_type="copilot",
                            entity_id=role.workspace_id,
                            harness_type="pi_rpc",
                        )
                    )

            async def send(session_id):
                async with factory() as db:
                    session = await db.scalar(
                        select(AgentSession).where(AgentSession.id == session_id)
                    )
                    assert session is not None
                    await runtime.start_runtime_turn(
                        db,
                        session,
                        role=role,
                        agent_config=config,
                        prompt="Hello",
                        run_id=uuid4(),
                        stream_id=uuid4(),
                    )

            results = await asyncio.gather(
                send(session_ids[0]), send(session_ids[0]), return_exceptions=True
            )
            assert (
                sum(isinstance(result, TracecatConflictError) for result in results)
                == 1
            )
            assert results.count(None) == 1
            assert len(started) == 1
            with pytest.raises(TimeoutError):
                await send(session_ids[1])
            with pytest.raises(TracecatConflictError):
                await send(session_ids[1])
            assert len(started) == 2
            async with factory() as db:
                for turn in started:
                    row = await db.scalar(
                        select(AgentSession).where(AgentSession.id == turn.session_id)
                    )
                    assert row is not None
                    assert row.curr_run_id == turn.turn_id
                    assert row.active_stream_id == turn.active_stream_id
                inputs = (
                    await db.scalars(
                        select(AgentSessionHistory).where(
                            AgentSessionHistory.kind == "pi-input"
                        )
                    )
                ).all()
                assert len(inputs) == 2
        finally:
            async with engine.begin() as db:
                await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()

    asyncio.run(scenario())
