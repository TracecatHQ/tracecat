"""PostgreSQL integration tests for shared query execution controls."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.query.errors import TracecatQueryTimeoutError
from tracecat.query.execution import query_execution_context


@pytest.mark.anyio
async def test_postgres_statement_timeout_maps_to_query_timeout(
    session: AsyncSession,
) -> None:
    await session.execute(text("SET LOCAL statement_timeout = '5min'"))
    with pytest.raises(TracecatQueryTimeoutError) as exc_info:
        async with session.begin_nested():
            async with query_execution_context(session, statement_timeout_ms=1):
                await session.execute(text("SELECT pg_sleep(0.1)"))

    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "query_timeout"
    assert await session.scalar(text("SHOW statement_timeout")) == "5min"
    assert await session.scalar(text("SELECT 1")) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("previous_timeout", ["0", "5min"])
async def test_nested_query_timeouts_restore_previous_settings(
    session: AsyncSession, previous_timeout: str
) -> None:
    await session.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": previous_timeout},
    )
    async with query_execution_context(session, statement_timeout_ms=1000):
        assert await session.scalar(text("SHOW statement_timeout")) == "1s"
        async with query_execution_context(session, statement_timeout_ms=2000):
            assert await session.scalar(text("SHOW statement_timeout")) == "2s"
        assert await session.scalar(text("SHOW statement_timeout")) == "1s"
    assert await session.scalar(text("SHOW statement_timeout")) == previous_timeout
