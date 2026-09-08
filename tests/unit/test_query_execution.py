"""Unit tests for shared query execution controls."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest
from asyncpg import NumericValueOutOfRangeError, QueryCanceledError
from sqlalchemy.exc import DataError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.query.errors import (
    QueryErrorCode,
    TracecatQueryOverflowError,
    TracecatQueryTimeoutError,
)
from tracecat.query.execution import query_execution_context


def _wrapped_driver_error(driver_error: BaseException) -> RuntimeError:
    adapter_error = RuntimeError("asyncpg adapter error")
    adapter_error.__cause__ = driver_error
    return adapter_error


@pytest.mark.anyio
async def test_query_execution_sets_transaction_local_timeout() -> None:
    session = AsyncMock(spec=AsyncSession)

    async with query_execution_context(session, statement_timeout_ms=1234):
        pass

    statement = session.execute.await_args.args[0]
    assert str(statement) == "SET LOCAL statement_timeout = 1234"


@pytest.mark.anyio
async def test_query_execution_reads_configured_default_at_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.query.execution.config.TRACECAT__AGG_STATEMENT_TIMEOUT_MS",
        4321,
    )
    session = AsyncMock(spec=AsyncSession)

    async with query_execution_context(session):
        pass

    statement = session.execute.await_args.args[0]
    assert str(statement) == "SET LOCAL statement_timeout = 4321"


@pytest.mark.parametrize(
    "statement_timeout_ms",
    [0, -1, True, 1.5, "1", 2_147_483_648],
)
@pytest.mark.anyio
async def test_query_execution_rejects_unsafe_timeout_values(
    statement_timeout_ms: object,
) -> None:
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises((TypeError, ValueError)):
        async with query_execution_context(
            session,
            statement_timeout_ms=cast(int, statement_timeout_ms),
        ):
            pass

    session.execute.assert_not_awaited()


@pytest.mark.parametrize("wrapped", [False, True], ids=["direct", "adapter-wrapped"])
@pytest.mark.anyio
async def test_query_execution_maps_query_cancellation_by_type(
    wrapped: bool,
) -> None:
    driver_error = QueryCanceledError("cancelled")
    orig = _wrapped_driver_error(driver_error) if wrapped else driver_error
    sqlalchemy_error = OperationalError("SELECT pg_sleep(1)", {}, orig)
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(TracecatQueryTimeoutError) as exc_info:
        async with query_execution_context(session):
            raise sqlalchemy_error

    assert exc_info.value.code is QueryErrorCode.TIMEOUT
    assert exc_info.value.detail == {
        "code": "query_timeout",
        "message": (
            "Aggregation timed out. Narrow filters or reduce group cardinality."
        ),
    }
    assert exc_info.value.__cause__ is sqlalchemy_error


@pytest.mark.parametrize("wrapped", [False, True], ids=["direct", "adapter-wrapped"])
@pytest.mark.anyio
async def test_query_execution_maps_numeric_overflow_by_type(
    wrapped: bool,
) -> None:
    driver_error = NumericValueOutOfRangeError("overflow")
    orig = _wrapped_driver_error(driver_error) if wrapped else driver_error
    sqlalchemy_error = DataError("SELECT value::bigint", {}, orig)
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(TracecatQueryOverflowError) as exc_info:
        async with query_execution_context(session):
            raise sqlalchemy_error

    assert exc_info.value.code is QueryErrorCode.NUMERIC_OVERFLOW
    assert exc_info.value.detail == {
        "code": "query_numeric_overflow",
        "message": "Aggregation result exceeds the supported numeric range.",
    }
    assert exc_info.value.__cause__ is sqlalchemy_error


@pytest.mark.parametrize(
    "sqlalchemy_error",
    [
        OperationalError(
            "SELECT 1",
            {},
            RuntimeError("canceling statement due to statement timeout"),
        ),
        DataError("SELECT 1", {}, OverflowError("numeric value out of range")),
    ],
    ids=["timeout-like-message", "overflow-like-message"],
)
@pytest.mark.anyio
async def test_query_execution_does_not_match_error_messages(
    sqlalchemy_error: OperationalError | DataError,
) -> None:
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(type(sqlalchemy_error)) as exc_info:
        async with query_execution_context(session):
            raise sqlalchemy_error

    assert exc_info.value is sqlalchemy_error
