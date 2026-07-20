import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.session_events import AfterCommitQueue


@pytest.mark.anyio
async def test_checkpointed_discards_callbacks_on_cancellation() -> None:
    """Cancellation removes callbacks registered after the guard checkpoint."""
    session = AsyncSession()

    async def existing_callback() -> None:
        pass

    async def cancelled_callback() -> None:
        pass

    try:
        queue = AfterCommitQueue.of(session)
        queue.add(existing_callback)

        with pytest.raises(asyncio.CancelledError):
            with queue.checkpointed():
                queue.add(cancelled_callback)
                raise asyncio.CancelledError

        assert queue.callbacks == [existing_callback]
    finally:
        await session.close()
