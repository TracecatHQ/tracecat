"""Stop a cancelled request's running statement in PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import anyio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.engine import get_async_cancel_engine
from tracecat.logger import logger

# How long one cancel request may take, including opening its connection.
_CANCEL_TIMEOUT_SECONDS = 5
# How long to wait for the work to end before cancelling again.
_RECANCEL_SECONDS = 1


async def cancel_statement_on_cancellation[T](
    session: AsyncSession, work: Callable[[], Awaitable[T]]
) -> T:
    """Run ``work()``; if the caller is cancelled, stop its statements first.

    Cancelling the task that awaits a statement does not reliably stop it.
    asyncpg sends PostgreSQL's cancel request from a separate task, but
    SQLAlchemy first tries to roll back on the still-busy connection, fails,
    and terminates it, which discards that pending request. The server then
    runs the statement to completion with no one waiting.

    So ``work()`` runs in its own task. When the caller is cancelled, this
    calls ``pg_cancel_backend`` for the session's backend over a separate,
    unpooled connection, so it works even when the main pool is exhausted. The
    statement fails with an ordinary query-cancelled error, ``work()`` rolls
    back as for any other error, and the connection stays usable. A cancel
    that lands between two statements has no effect, so it is sent again every
    second until ``work()`` ends; each statement is still bounded by its own
    statement timeout. Only then is the cancellation passed on, so the session
    is never closed while ``work()`` still uses it.

    The backend stays this session's for the whole call: the session holds an
    open transaction, and search work only uses savepoints. ``work()`` runs
    with a copy of the current context, so request-scoped context variables
    such as the role stay visible to it. This costs one ``pg_backend_pid()``
    round trip, plus one short connection per cancel when a request is
    actually cancelled.
    """
    pid = await session.scalar(sa.text("SELECT pg_backend_pid()"))
    task = asyncio.ensure_future(work())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Inside anyio task groups (BaseHTTPMiddleware) cancellation is
        # level-triggered: every later await in the cancelled scope is
        # cancelled again, so the cleanup is shielded.
        with anyio.CancelScope(shield=True):
            while not task.done():
                with anyio.move_on_after(_CANCEL_TIMEOUT_SECONDS):
                    await _cancel_backend(pid)
                await asyncio.wait({task}, timeout=_RECANCEL_SECONDS)
        raise


async def _cancel_backend(pid: int | None) -> None:
    if pid is None:
        return
    try:
        async with get_async_cancel_engine().connect() as connection:
            await connection.execute(
                sa.text("SELECT pg_cancel_backend(:pid)"), {"pid": pid}
            )
    except Exception as exc:
        # Best effort: the next attempt, or the statement timeout, follows.
        logger.warning("Could not cancel abandoned statement", error=str(exc))
