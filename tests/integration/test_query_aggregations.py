"""Shared aggregation compiler regressions against PostgreSQL."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.query.aggregations import AggregationSpec
from tracecat.query.compiler import compile_aggregation


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
