from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Final, cast

import sqlalchemy.orm
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.logger import logger

_AFTER_COMMIT_KEY: Final[str] = "after_commit_callbacks"
_EVENT_LOOP_KEY: Final[str] = "event_loop"
AsyncCallback = Callable[[], Awaitable[Any]]


def add_after_commit_callback(session: AsyncSession, callback: AsyncCallback) -> None:
    """Register a callable to run after this session commits.

    The callable may be sync or async. If async, it will be scheduled onto
    the event loop captured during registration.

    Args:
        session: The async SQLAlchemy session to register the callback with.
        callback: The callable function to execute after commit. Should return an awaitable.

    Returns:
        None

    Note:
        The callback will be executed on the event loop that was active when
        this function was called. If no event loop is running, the callback
        will still be registered but may not execute properly.
    """
    logger.debug("Adding after_commit callback", callback=callback)
    sync = session.sync_session
    sync.info.setdefault(_AFTER_COMMIT_KEY, []).append(callback)
    # Capture the current loop if available; used to schedule async callbacks.
    try:
        loop = asyncio.get_running_loop()
        sync.info.setdefault(_EVENT_LOOP_KEY, loop)
    except RuntimeError:
        loop = None


@event.listens_for(sqlalchemy.orm.Session, "after_commit")
def _run_after_commit(session: sqlalchemy.orm.Session) -> None:  # pyright: ignore[reportUnusedFunction] - registered as SQLAlchemy event listener
    """Execute all registered after-commit callbacks for the given session.

    This function is automatically called by SQLAlchemy after a session commits.
    It retrieves all registered callbacks and executes them, handling both
    synchronous and asynchronous callbacks appropriately.

    Args:
        session: The SQLAlchemy session that just committed.

    Returns:
        None

    Note:
        Async callbacks are scheduled as tasks on the event loop that was
        captured during callback registration. If no loop was captured,
        a new event loop is created.
    """
    callbacks = cast(list[AsyncCallback], session.info.pop(_AFTER_COMMIT_KEY, []))
    if not callbacks:
        return
    logger.debug("Running after_commit callbacks", session=session, callbacks=callbacks)
    if _EVENT_LOOP_KEY not in session.info:
        logger.error("Expected event loop not found in session info", session=session)
        return
    loop = session.info[_EVENT_LOOP_KEY]
    for cb in callbacks:
        try:
            if (coro := cb()) and asyncio.iscoroutine(coro):
                logger.debug("Running after_commit callback", callback=coro)
                asyncio.run_coroutine_threadsafe(coro, loop)
            else:
                logger.warning("Callback did not return a coroutine", callback=cb)
        except Exception as e:
            logger.error("after_commit callback failed", error=str(e))
