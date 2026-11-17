"""Serialized sessions validate before checking out any connection."""

from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db import engine as db_engine
from tracecat.db.engine import DatabasePoolAcquisitionOrderError, get_serialized_session


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
