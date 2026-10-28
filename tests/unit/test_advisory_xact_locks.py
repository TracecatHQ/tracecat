"""Transaction-scoped advisory locks keep serializing contenders.

Each test contends two live sessions on one lock key. A lock must never outlive
the transaction that took it, so the protected work has to run inside that
transaction or under a held lock transaction on its own connection.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool
from sqlalchemy.util import await_only
from tracecat_ee.tunnels import gateways

from tracecat.auth.types import Role
from tracecat.cases.service import CaseFieldsService
from tracecat.db.engine import (
    get_async_engine,
    get_async_session_bypass_rls_context_manager,
    get_serialized_session,
)
from tracecat.db.exceptions import AdvisoryLockLostError
from tracecat.db.locks import hold_pg_advisory_xact_lock

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

# Long enough for a contender that should block to reach the lock call.
CONTENTION_WINDOW = 0.3


def _lock_key() -> int:
    return uuid.uuid4().int % (2**63)


async def _lock_is_free(key: int) -> bool:
    async with hold_pg_advisory_xact_lock(
        get_async_engine(), key, wait=False
    ) as holder:
        return holder is not None


async def _terminate_lock_holder(key: int) -> None:
    async with get_async_engine().connect() as admin:
        await admin.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_locks"
                " WHERE locktype = 'advisory' AND granted"
                " AND classid = :high AND objid = :low AND objsubid = 1"
            ),
            {"high": key >> 32, "low": key & 0xFFFFFFFF},
        )


async def test_held_lock_blocks_waiters_and_fails_tries() -> None:
    key = _lock_key()
    waiter_acquired = asyncio.Event()

    async def wait_for_lock() -> None:
        async with hold_pg_advisory_xact_lock(get_async_engine(), key):
            waiter_acquired.set()

    async with hold_pg_advisory_xact_lock(get_async_engine(), key) as holder:
        assert holder is not None
        waiter = asyncio.create_task(wait_for_lock())
        await asyncio.sleep(CONTENTION_WINDOW)
        assert not waiter_acquired.is_set()
        assert not await _lock_is_free(key)
    await asyncio.wait_for(waiter, timeout=10)
    assert await _lock_is_free(key)


async def test_lock_is_released_when_the_body_raises() -> None:
    key = _lock_key()
    with pytest.raises(RuntimeError):
        async with hold_pg_advisory_xact_lock(get_async_engine(), key):
            raise RuntimeError("Synthetic failure")
    assert await _lock_is_free(key)


async def test_lock_survives_a_server_idle_in_transaction_timeout() -> None:
    key = _lock_key()
    engine = create_async_engine(
        get_async_engine().url,
        poolclass=NullPool,
        connect_args={
            "server_settings": {"idle_in_transaction_session_timeout": "100ms"}
        },
    )
    try:
        async with hold_pg_advisory_xact_lock(engine, key):
            await asyncio.sleep(CONTENTION_WINDOW)
            assert not await _lock_is_free(key)
    finally:
        await engine.dispose()
    assert await _lock_is_free(key)


async def test_serialized_session_waiter_sees_every_commit_of_the_holder() -> None:
    """The holder commits twice; the waiter's snapshot starts after both."""
    key = _lock_key()
    table = f"lock_probe_{uuid.uuid4().hex}"
    async with get_async_engine().begin() as connection:
        await connection.execute(text(f"CREATE TABLE {table} (n integer)"))
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_entered = asyncio.Event()

    async def holder() -> None:
        async with get_serialized_session(key) as session:
            await session.execute(text(f"INSERT INTO {table} VALUES (1)"))
            await session.commit()
            await session.execute(text(f"INSERT INTO {table} VALUES (2)"))
            holder_ready.set()
            await release_holder.wait()
            await session.commit()

    async def waiter() -> int:
        async with get_serialized_session(key) as session:
            waiter_entered.set()
            return (
                await session.execute(text(f"SELECT count(*) FROM {table}"))
            ).scalar_one()

    try:
        holder_task = asyncio.create_task(holder())
        await asyncio.wait_for(holder_ready.wait(), timeout=10)
        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(CONTENTION_WINDOW)
        assert not waiter_entered.is_set()
        release_holder.set()
        await asyncio.wait_for(holder_task, timeout=10)
        assert await asyncio.wait_for(waiter_task, timeout=10) == 2
    finally:
        async with get_async_engine().begin() as connection:
            await connection.execute(text(f"DROP TABLE IF EXISTS {table}"))


async def test_serialized_session_cannot_commit_after_losing_the_lock() -> None:
    """A dropped holder connection fails the work commit instead of racing."""
    key = _lock_key()
    table = f"lock_probe_{uuid.uuid4().hex}"
    async with get_async_engine().begin() as connection:
        await connection.execute(text(f"CREATE TABLE {table} (n integer)"))
    try:
        async with get_serialized_session(key) as session:
            await session.execute(text(f"INSERT INTO {table} VALUES (1)"))
            await _terminate_lock_holder(key)
            assert await _lock_is_free(key)
            with pytest.raises(AdvisoryLockLostError):
                await session.commit()
        async with get_async_engine().connect() as connection:
            count = await connection.execute(text(f"SELECT count(*) FROM {table}"))
            assert count.scalar_one() == 0
    finally:
        async with get_async_engine().begin() as connection:
            await connection.execute(text(f"DROP TABLE IF EXISTS {table}"))


async def test_serialized_session_checks_the_holder_after_flushing() -> None:
    """Losing the holder while pending ORM writes flush still fails the commit."""
    key = _lock_key()
    table = f"lock_probe_{uuid.uuid4().hex}"

    class Base(DeclarativeBase):
        pass

    class Probe(Base):
        __tablename__ = table
        id: Mapped[int] = mapped_column(primary_key=True)

    async with get_async_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with get_serialized_session(key) as session:

            def lose_holder_during_flush(*_args: object) -> None:
                await_only(_terminate_lock_holder(key))

            event.listen(session.sync_session, "after_flush", lose_holder_during_flush)
            session.add(Probe(id=1))
            with pytest.raises(AdvisoryLockLostError):
                await session.commit()
        async with get_async_engine().connect() as connection:
            count = await connection.execute(text(f"SELECT count(*) FROM {table}"))
            assert count.scalar_one() == 0
    finally:
        async with get_async_engine().begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)


async def test_custom_field_schema_init_waits_for_the_first_commit(
    svc_role: Role,
) -> None:
    async def initialize_second() -> None:
        async with get_async_session_bypass_rls_context_manager() as session:
            await CaseFieldsService(session, svc_role).initialize_workspace_schema()
            await session.commit()

    async with get_async_session_bypass_rls_context_manager() as session:
        service = CaseFieldsService(session, svc_role)
        try:
            await service.initialize_workspace_schema()
            second = asyncio.create_task(initialize_second())
            await asyncio.sleep(CONTENTION_WINDOW)
            assert not second.done()
            await session.commit()
            # The second initializer sees the committed schema instead of
            # racing the first transaction's uncommitted DDL.
            await asyncio.wait_for(second, timeout=10)
        finally:
            await session.rollback()
            await service.drop_workspace_schema()
            await session.commit()


async def test_tunnel_reconcile_skips_while_another_process_reconciles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader_ready = asyncio.Event()
    release_leader = asyncio.Event()
    calls = 0

    async def desired_specs(_session: object) -> list[object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            leader_ready.set()
            await release_leader.wait()
        return []

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(gateways, "_RECONCILE_LOCK_KEY", _lock_key())
    monkeypatch.setattr(gateways, "desired_gateway_specs", desired_specs)
    monkeypatch.setattr(gateways, "KubernetesClient", lambda: client)
    monkeypatch.setattr(
        gateways,
        "KubernetesGateways",
        lambda _client: SimpleNamespace(reconcile=AsyncMock(return_value=(0, 0))),
    )

    leader = asyncio.create_task(gateways.reconcile_gateways_once())
    await asyncio.wait_for(leader_ready.wait(), timeout=10)
    await asyncio.wait_for(gateways.reconcile_gateways_once(), timeout=10)
    assert calls == 1
    release_leader.set()
    await asyncio.wait_for(leader, timeout=10)
    await gateways.reconcile_gateways_once()
    assert calls == 2
