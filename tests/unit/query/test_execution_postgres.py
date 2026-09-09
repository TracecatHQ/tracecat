"""PostgreSQL integration tests for shared query execution controls."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.query.aggregations import AggregationSpec
from tracecat.query.compiler import compile_aggregation
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


@pytest.mark.anyio
@pytest.mark.parametrize("min_count", [2, 2**31, 2**63 - 1])
async def test_multi_valued_min_count_uses_bigint(
    session: AsyncSession, min_count: int
) -> None:
    entities = sa.values(sa.column("id", sa.Integer), name="entities").data(
        [(1,), (1,), (2,)]
    )
    statement = compile_aggregation(
        sa.select(entities.c.id),
        AggregationSpec(group_by=[], min_count=min_count),
        {},
        limit=100,
        base_has_multi_valued_join=True,
        entity_id=entities.c.id,
    )
    result = await session.execute(statement)
    assert result.scalars().all() == ([2] if min_count == 2 else [])
