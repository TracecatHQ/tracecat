"""Transaction-local execution controls for shared queries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
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
    in the statement. On success, restore the previous timeout so later queries
    in the same transaction are unaffected. On failure, the caller must roll back
    the surrounding transaction or savepoint, which also restores the timeout;
    issuing cleanup SQL in an aborted transaction would hide the original error.

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
        previous_timeout = await session.scalar(text("SHOW statement_timeout"))
        await session.execute(
            text(f"SET LOCAL statement_timeout = {resolved_timeout_ms}")
        )
        yield
        await session.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": previous_timeout},
        )
    except DBAPIError as exc:
        if _has_driver_error(exc.orig, QueryCanceledError):
            raise TracecatQueryTimeoutError from exc
        if _has_driver_error(exc.orig, NumericValueOutOfRangeError):
            raise TracecatQueryOverflowError from exc
        raise


@asynccontextmanager
async def transaction_settings(
    session: AsyncSession, settings: Mapping[str, str]
) -> AsyncIterator[None]:
    """Apply PostgreSQL settings for the current transaction, then restore them.

    Each setting is set with ``set_config(name, value, true)``, so it lasts at
    most until the transaction ends. On success the previous values are
    restored, so later statements in the same transaction are unaffected. On
    failure the caller must roll back the surrounding transaction or savepoint,
    which also restores them; issuing cleanup SQL in an aborted transaction
    would hide the original error. An empty mapping does nothing.

    Args:
        session: Session whose current transaction runs the statements.
        settings: Setting names and the values to use inside the block.
    """
    if not settings:
        yield
        return
    names = list(settings)
    previous = list(
        (
            await session.execute(
                text(
                    "SELECT current_setting(name) FROM unnest(CAST(:names AS text[]))"
                    " WITH ORDINALITY AS s(name, position) ORDER BY position"
                ),
                {"names": names},
            )
        ).scalars()
    )
    await _set_local(session, names, [settings[name] for name in names])
    yield
    await _set_local(session, names, previous)


async def _set_local(
    session: AsyncSession, names: list[str], values: list[str]
) -> None:
    await session.execute(
        text(
            "SELECT set_config(name, value, true) FROM unnest("
            "CAST(:names AS text[]), CAST(:values AS text[])) AS s(name, value)"
        ),
        {"names": names, "values": values},
    )
