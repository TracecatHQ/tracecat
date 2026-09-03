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
    with pytest.raises(TracecatQueryTimeoutError) as exc_info:
        async with query_execution_context(session, statement_timeout_ms=1):
            await session.execute(text("SELECT pg_sleep(0.1)"))

    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail["code"] == "query_timeout"
