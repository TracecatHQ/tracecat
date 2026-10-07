"""Connection locks release healthy sessions without masking domain errors."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tracecat.db import engine as db_engine
from tracecat.db.engine import DatabasePoolAcquisitionOrderError, get_serialized_session
from tracecat.db.locks import pg_advisory_connection_lock
from tracecat.workspace_sync.operations.types import StaleSyncPreviewError


@pytest.mark.anyio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_domain_error_releases_connection_lock(cleanup_fails: bool) -> None:
    connection = AsyncMock(spec=AsyncConnection)
    connection.invalidated = False
    if cleanup_fails:
        connection.rollback.side_effect = RuntimeError("Synthetic cleanup error")
    error = StaleSyncPreviewError("Synthetic stale preview")
    with pytest.raises(StaleSyncPreviewError) as caught:
        async with pg_advisory_connection_lock(connection, 42):
            raise error
    assert caught.value is error
    connection.rollback.assert_awaited_once()
    if cleanup_fails:
        connection.invalidate.assert_awaited_once()
    else:
        connection.invalidate.assert_not_awaited()
        assert "pg_advisory_unlock" in str(
            connection.execute.await_args_list[-1].args[0]
        )


@pytest.mark.anyio
async def test_cancellation_discards_connection_lock() -> None:
    connection = AsyncMock(spec=AsyncConnection)
    connection.invalidated = False
    with pytest.raises(asyncio.CancelledError):
        async with pg_advisory_connection_lock(connection, 42):
            raise asyncio.CancelledError
    connection.invalidate.assert_awaited_once()


@pytest.mark.anyio
async def test_invalid_lock_key_does_not_check_out_connection(monkeypatch) -> None:
    engine = Mock(side_effect=AssertionError("Must not acquire a connection"))
    monkeypatch.setattr("tracecat.db.engine.get_async_engine", engine)
    with pytest.raises(ValueError, match="out of range"):
        async with get_serialized_session(2**63):
            pass
    engine.assert_not_called()


@pytest.mark.anyio
async def test_serialized_session_rejects_auth_pool_nesting_before_checkout(
    monkeypatch,
):
    engine = Mock(side_effect=AssertionError("Must not acquire a connection"))
    monkeypatch.setattr(db_engine, "get_async_engine", engine)
    token = db_engine._ctx_auth_pool_session.set(AsyncMock(spec=AsyncSession))
    try:
        with pytest.raises(DatabasePoolAcquisitionOrderError):
            async with get_serialized_session(42):
                pass
    finally:
        db_engine._ctx_auth_pool_session.reset(token)
    engine.assert_not_called()
