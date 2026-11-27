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
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool
from sqlalchemy.util import await_only
from tracecat_ee.tunnels import gateways

from tracecat.auth.types import Role
from tracecat.cases.schemas import CaseFieldCreate, CaseFieldUpdate
from tracecat.cases.service import CaseFieldsService
from tracecat.db.engine import (
    get_async_engine,
    get_async_session_bypass_rls_context_manager,
    get_serialized_session,
)
from tracecat.db.exceptions import AdvisoryLockLostError
from tracecat.db.locks import (
    hold_pg_advisory_xact_lock,
    require_lock_holder_on_commit,
)
from tracecat.db.models import Case
from tracecat.tables.enums import SqlType

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


async def _wait_until_lock_is_free(key: int, timeout: float = 5.0) -> bool:
    """Poll until the lock is free; False if it is still held at the deadline.

    ``pg_terminate_backend`` only signals the backend. Its locks are released
    when the backend exits, which can lag behind under load.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await _lock_is_free(key):
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.05)
    return True


async def _terminate_lock_holder(key: int) -> None:
    async with get_async_engine().connect() as admin:
        await admin.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_locks"
                " WHERE locktype = 'advisory' AND granted"
                " AND database = (SELECT oid FROM pg_database"
                " WHERE datname = current_database())"
                " AND classid = :high AND objid = :low AND objsubid = 1"
            ),
            {"high": key >> 32, "low": key & 0xFFFFFFFF},
        )


async def _advisory_waiters(key: int) -> int:
    async with get_async_engine().connect() as connection:
        result = await connection.execute(
            text(
                "SELECT count(*) FROM pg_locks"
                " WHERE locktype = 'advisory' AND NOT granted"
                " AND database = (SELECT oid FROM pg_database"
                " WHERE datname = current_database())"
                " AND classid = :high AND objid = :low AND objsubid = 1"
            ),
            {"high": key >> 32, "low": key & 0xFFFFFFFF},
        )
        return result.scalar_one()


async def _wait_for_relation_lock_waiter(table_name: str) -> None:
    """Return once a session is queued for a lock on the table."""
    async with get_async_engine().connect() as connection:
        for _ in range(100):
            result = await connection.execute(
                text(
                    "SELECT count(*) FROM pg_locks"
                    " WHERE locktype = 'relation' AND NOT granted"
                    " AND relation = to_regclass(:table_name)"
                ),
                {"table_name": table_name},
            )
            if result.scalar_one():
                return
            await asyncio.sleep(0.05)
    raise AssertionError(f"Nothing waited for a lock on {table_name}")


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
            assert await _wait_until_lock_is_free(key)
            with pytest.raises(AdvisoryLockLostError):
                await session.commit()
        async with get_async_engine().connect() as connection:
            count = await connection.execute(text(f"SELECT count(*) FROM {table}"))
            assert count.scalar_one() == 0
    finally:
        async with get_async_engine().begin() as connection:
            await connection.execute(text(f"DROP TABLE IF EXISTS {table}"))


async def test_commit_fails_when_the_holder_transaction_ended_on_a_live_socket() -> (
    None
):
    """The guard checks ownership, not just that the holder connection answers."""
    key = _lock_key()
    engine = get_async_engine()
    async with hold_pg_advisory_xact_lock(engine, key) as holder:
        assert holder is not None
        async with AsyncSession(engine) as session:
            require_lock_holder_on_commit(session, holder, key)
            await session.execute(text("SELECT 1"))
            # Ends the lock transaction; the holder connection stays open.
            await holder.rollback()
            with pytest.raises(AdvisoryLockLostError):
                await session.commit()


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
            # Waiting on the advisory lock, not on the first one's DDL.
            assert await _advisory_waiters(service._schema_lock_key()) >= 1
            await session.commit()
            # The second initializer sees the committed schema instead of
            # racing the first transaction's uncommitted DDL.
            await asyncio.wait_for(second, timeout=10)
        finally:
            await session.rollback()
            await service.drop_workspace_schema()
            await session.commit()


@pytest.mark.parametrize("schema_exists", [False, True])
async def test_custom_field_reads_never_take_the_schema_lock(
    svc_role: Role, schema_exists: bool
) -> None:
    """Concurrent reads take no advisory lock and never create the schema."""
    if schema_exists:
        async with get_async_session_bypass_rls_context_manager() as session:
            await CaseFieldsService(session, svc_role).initialize_workspace_schema()
            await session.commit()

    async def read_fields() -> tuple[int, bool]:
        async with get_async_session_bypass_rls_context_manager() as session:
            service = CaseFieldsService(session, svc_role)
            case = Case(id=uuid.uuid4(), workspace_id=svc_role.workspace_id)
            await service.get_fields(case)
            await service.batch_get_fields([case.id], ["severity_score"])
            # Transaction-scoped locks stay listed until this transaction ends.
            held = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                    " AND pid = pg_backend_pid()"
                )
            )
            table = await session.scalar(
                text("SELECT to_regclass(:name) IS NOT NULL"),
                {"name": service._full_table_name()},
            )
            await session.rollback()
            return held, table

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(read_fields() for _ in range(8))), timeout=30
        )
        assert results == [(0, schema_exists)] * 8
    finally:
        async with get_async_session_bypass_rls_context_manager() as session:
            await CaseFieldsService(session, svc_role).drop_workspace_schema()
            await session.commit()


async def test_case_field_columns_stay_valid_until_the_read_ends(
    svc_role: Role,
) -> None:
    """Field DDL waits for a read that took columns, so the names stay valid.

    Without this, a rename committed between the column read and the row query
    fails the query, and a cache filled during the rename stores wrong columns.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        await CaseFieldsService(session, svc_role).create_field(
            CaseFieldCreate(name="first", type=SqlType.TEXT)
        )

    async def rename() -> None:
        async with get_async_session_bypass_rls_context_manager() as session:
            await CaseFieldsService(session, svc_role).update_field(
                "first", CaseFieldUpdate(name="second")
            )

    async def names() -> set[str]:
        async with get_async_session_bypass_rls_context_manager() as session:
            columns = await CaseFieldsService(session, svc_role).list_fields()
            return {column["name"] for column in columns}

    try:
        async with get_async_session_bypass_rls_context_manager() as session:
            service = CaseFieldsService(session, svc_role)
            assert "first" in {c["name"] for c in await service.list_fields()}
            renaming = asyncio.create_task(rename())
            await _wait_for_relation_lock_waiter(service._full_table_name())
            assert not renaming.done()
            # The select list still names "first".
            case = Case(id=uuid.uuid4(), workspace_id=svc_role.workspace_id)
            assert await service.get_fields(case) is None
            await session.commit()
        await asyncio.wait_for(renaming, timeout=10)
        assert {"second"} <= await names()
        assert "first" not in await names()
    finally:
        async with get_async_session_bypass_rls_context_manager() as session:
            await CaseFieldsService(session, svc_role).drop_workspace_schema()
            await session.commit()


@pytest.mark.parametrize("warm", [False, True], ids=["cold", "warm"])
async def test_case_field_column_read_waits_for_open_field_ddl(
    svc_role: Role, warm: bool
) -> None:
    """The column read waits for open field DDL, then sees its result.

    The read locks the table in the same statement that reads the catalog. A
    read that looked at the catalog before taking the lock would return the
    columns from before the DDL. The warm case reuses a prepared statement on
    the same connection, which must also lock before it reads.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        await CaseFieldsService(session, svc_role).create_field(
            CaseFieldCreate(name="first", type=SqlType.TEXT)
        )

    async def names(reader: AsyncSession) -> set[str]:
        columns = await CaseFieldsService(reader, svc_role).list_fields()
        return {column["name"] for column in columns}

    try:
        async with get_async_engine().connect() as connection:
            reader = AsyncSession(bind=connection)
            if warm:
                assert await names(reader) == {
                    "id",
                    "case_id",
                    "created_at",
                    "updated_at",
                    "first",
                }
                await reader.commit()
            async with get_async_session_bypass_rls_context_manager() as session:
                writer = CaseFieldsService(session, svc_role)
                await writer.editor.create_column(
                    CaseFieldCreate(name="second", type=SqlType.TEXT)
                )
                read = asyncio.create_task(names(reader))
                await _wait_for_relation_lock_waiter(writer._full_table_name())
                assert not read.done()
                await session.commit()
            assert "second" in await asyncio.wait_for(read, timeout=10)
            await reader.close()
    finally:
        async with get_async_session_bypass_rls_context_manager() as session:
            await CaseFieldsService(session, svc_role).drop_workspace_schema()
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
