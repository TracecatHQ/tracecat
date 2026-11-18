"""A cancelled request keeps stopping its statement until its work ends."""

import asyncio
import gc
from unittest.mock import AsyncMock

import pytest

from tracecat.db import cancellation


@pytest.mark.anyio
async def test_cancel_is_sent_again_until_the_work_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first cancel lands between two statements and has no effect.
    work_may_end = asyncio.Event()
    pids: list[int | None] = []

    async def cancel_backend(pid: int | None) -> None:
        pids.append(pid)
        if len(pids) == 2:
            work_may_end.set()

    monkeypatch.setattr(cancellation, "_cancel_backend", cancel_backend)
    monkeypatch.setattr(cancellation, "_RECANCEL_SECONDS", 0.01)
    session = AsyncMock()
    session.scalar.return_value = 4242
    work_ended = False

    async def work() -> str:
        nonlocal work_ended
        await work_may_end.wait()
        work_ended = True
        return "done"

    request = asyncio.ensure_future(
        cancellation.cancel_statement_on_cancellation(session, work)
    )
    await asyncio.sleep(0.01)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    # The cancellation is passed on only after the work has finished.
    assert work_ended
    assert pids == [4242, 4242]


@pytest.mark.anyio
async def test_work_is_not_started_when_the_backend_lookup_fails() -> None:
    session = AsyncMock()
    session.scalar.side_effect = RuntimeError("synthetic lookup failure")
    started = False

    async def work() -> None:
        nonlocal started
        started = True

    with pytest.raises(RuntimeError):
        await cancellation.cancel_statement_on_cancellation(session, work)
    assert not started


@pytest.mark.anyio
async def test_the_cancelled_statement_error_is_not_left_unretrieved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancel_backend(_pid: int | None) -> None:
        return None

    monkeypatch.setattr(cancellation, "_cancel_backend", cancel_backend)
    monkeypatch.setattr(cancellation, "_RECANCEL_SECONDS", 0.01)
    session = AsyncMock()
    session.scalar.return_value = 4242
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.sleep(0.02)
        raise RuntimeError("synthetic cancelled statement")

    loop = asyncio.get_running_loop()
    reported: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    try:
        request = asyncio.ensure_future(
            cancellation.cancel_statement_on_cancellation(session, work)
        )
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        del request
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)
    assert not [
        context
        for context in reported
        if "never retrieved" in str(context.get("message", ""))
    ]
