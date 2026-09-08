"""Transaction-local execution controls for shared queries."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asyncpg import NumericValueOutOfRangeError, QueryCanceledError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.query.errors import (
    TracecatQueryOverflowError,
    TracecatQueryTimeoutError,
)


def _validate_statement_timeout_ms(statement_timeout_ms: int) -> None:
    if isinstance(statement_timeout_ms, bool) or not isinstance(
        statement_timeout_ms, int
    ):
        raise TypeError("statement_timeout_ms must be an integer")
    if not 1 <= statement_timeout_ms <= config.POSTGRES_STATEMENT_TIMEOUT_MAX_MS:
        raise ValueError(
            "statement_timeout_ms must be between 1 and 2147483647 milliseconds"
        )


def _has_driver_error(
    error: BaseException | None,
    driver_error_type: type[BaseException],
) -> bool:
    """Return whether a SQLAlchemy DBAPI error wraps a typed driver error."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, driver_error_type):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


@asynccontextmanager
async def query_execution_context(
    session: AsyncSession,
    *,
    statement_timeout_ms: int | None = None,
) -> AsyncIterator[None]:
    """Apply a transaction-local timeout and map known PostgreSQL query errors.

    The timeout is interpolated because PostgreSQL does not support bind parameters
    in ``SET LOCAL``. Validation guarantees that only a positive integer is placed
    in the statement. The caller owns the surrounding transaction.

    Args:
        session: Session whose current transaction will execute the query.
        statement_timeout_ms: PostgreSQL statement timeout in milliseconds.

    Raises:
        TracecatQueryTimeoutError: If PostgreSQL cancels the query at its timeout.
        TracecatQueryOverflowError: If PostgreSQL reports numeric overflow.
    """
    resolved_timeout_ms = (
        config.TRACECAT__AGG_STATEMENT_TIMEOUT_MS
        if statement_timeout_ms is None
        else statement_timeout_ms
    )
    _validate_statement_timeout_ms(resolved_timeout_ms)

    try:
        await session.execute(
            text(f"SET LOCAL statement_timeout = {resolved_timeout_ms}")
        )
        yield
    except DBAPIError as exc:
        if _has_driver_error(exc.orig, QueryCanceledError):
            raise TracecatQueryTimeoutError from exc
        if _has_driver_error(exc.orig, NumericValueOutOfRangeError):
            raise TracecatQueryOverflowError from exc
        raise
