"""Stop a cancelled request's running statement in PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import anyio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tracecat.logger import logger

# Upper bound on cancelling the statement and letting the work unwind.
_CANCEL_TIMEOUT_SECONDS = 10


async def cancel_statement_on_cancellation[T](
    session: AsyncSession, work: Coroutine[Any, Any, T]
) -> T:
    """Run ``work``; if the caller is cancelled, stop its statement first.

    Cancelling the task that awaits a statement does not reliably stop it.
    asyncpg sends PostgreSQL's cancel request from a separate task, but
    SQLAlchemy first tries to roll back on the still-busy connection, fails,
    and terminates it, which discards that pending request. The server then
    runs the statement to completion with no one waiting.

    So ``work`` runs in its own task. When the caller is cancelled, this calls
    ``pg_cancel_backend`` for the session's backend from another pooled
    connection. The statement fails with an ordinary query-cancelled error,
    ``work`` rolls back as for any other error, and the connection stays
    usable. Only then is the cancellation passed on. ``pg_cancel_backend`` is
    plain SQL, so it also works through a transaction pooler, and a role may
    always cancel its own backends.

    ``work`` runs with a copy of the current context, so request-scoped context
    variables such as the role stay visible to it. This costs one
    ``pg_backend_pid()`` round trip, plus a second pooled connection only when
    a request is actually cancelled.
    """
    pid = await session.scalar(sa.text("SELECT pg_backend_pid()"))
    task = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Inside anyio task groups (BaseHTTPMiddleware) cancellation is
        # level-triggered: every later await in the cancelled scope is
        # cancelled again, so the cleanup is shielded and time-bounded.
        with (
            anyio.CancelScope(shield=True),
            anyio.move_on_after(_CANCEL_TIMEOUT_SECONDS),
        ):
            await _cancel_backend(session, pid)
            await asyncio.gather(task, return_exceptions=True)
        if not task.done():
            task.cancel()
        raise


async def _cancel_backend(session: AsyncSession, pid: int | None) -> None:
    bind = session.bind
    # A session is bound to an engine, or (in tests) to one connection.
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    if pid is None or engine is None:
        return
    try:
        async with engine.connect() as connection:
            await connection.execute(
                sa.text("SELECT pg_cancel_backend(:pid)"), {"pid": pid}
            )
    except Exception as exc:
        # Best effort: the statement still ends at its own timeout.
        logger.warning("Could not cancel abandoned statement", error=str(exc))
