import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.session_events import (
    add_after_commit_callback,
    rollback_after_commit_callbacks,
)


@pytest.mark.anyio
async def test_rollback_after_commit_callbacks_discards_on_cancellation() -> None:
    """Cancellation removes callbacks registered after the guard checkpoint."""
    session = AsyncSession()

    async def existing_callback() -> None:
        pass

    async def cancelled_callback() -> None:
        pass

    try:
        add_after_commit_callback(session, existing_callback)

        with pytest.raises(asyncio.CancelledError):
            with rollback_after_commit_callbacks(session):
                add_after_commit_callback(session, cancelled_callback)
                raise asyncio.CancelledError

        assert session.sync_session.info["after_commit_callbacks"] == [
            existing_callback
        ]
    finally:
        await session.close()
