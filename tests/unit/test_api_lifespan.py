import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

import tracecat.api.lifespan as lifespan_module


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("context_factory", "task_factory_name"),
    [
        (
            lifespan_module.temporal_search_attributes_lifespan,
            "add_temporal_search_attributes",
        ),
        (
            lifespan_module.platform_registry_sync_lifespan,
            "sync_platform_registry_on_startup",
        ),
        (
            lifespan_module.platform_catalog_load_lifespan,
            "load_platform_catalog_on_startup",
        ),
    ],
)
async def test_one_shot_lifespan_context_starts_task(
    monkeypatch: pytest.MonkeyPatch,
    context_factory: Callable[[], AbstractAsyncContextManager[None]],
    task_factory_name: str,
) -> None:
    started = asyncio.Event()

    async def run() -> None:
        started.set()

    monkeypatch.setattr(lifespan_module, task_factory_name, run)

    async with context_factory():
        await started.wait()


@pytest.mark.anyio
async def test_case_trigger_lifespan_cancels_enabled_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()
    keep_running = asyncio.Event()

    async def consume() -> None:
        started.set()
        try:
            await keep_running.wait()
        finally:
            stopped.set()

    monkeypatch.setattr(
        lifespan_module,
        "start_case_trigger_consumer",
        consume,
    )
    monkeypatch.setattr(
        lifespan_module.config,
        "TRACECAT__CASE_TRIGGERS_ENABLED",
        True,
    )

    async with lifespan_module.case_trigger_consumer_lifespan():
        await started.wait()

    assert stopped.is_set()


@pytest.mark.anyio
async def test_case_trigger_lifespan_skips_disabled_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = False

    async def consume() -> None:
        nonlocal started
        started = True

    monkeypatch.setattr(
        lifespan_module,
        "start_case_trigger_consumer",
        consume,
    )
    monkeypatch.setattr(
        lifespan_module.config,
        "TRACECAT__CASE_TRIGGERS_ENABLED",
        False,
    )

    async with lifespan_module.case_trigger_consumer_lifespan():
        await asyncio.sleep(0)

    assert not started


@pytest.mark.anyio
async def test_lifespan_task_propagates_owner_cancellation_during_cleanup() -> None:
    child_started = asyncio.Event()
    child_cleanup_started = asyncio.Event()
    keep_running = asyncio.Event()
    cleanup_blocker = asyncio.Event()
    owner_continued = False

    async def run_child() -> None:
        child_started.set()
        try:
            await keep_running.wait()
        except asyncio.CancelledError:
            child_cleanup_started.set()
            await cleanup_blocker.wait()
            raise

    async def run_owner() -> None:
        nonlocal owner_continued
        async with lifespan_module._lifespan_task(run_child, name="test_child"):
            await child_started.wait()
        owner_continued = True

    owner_task = asyncio.create_task(run_owner())
    await child_cleanup_started.wait()
    owner_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await owner_task

    assert not owner_continued


@pytest.mark.anyio
async def test_lifespan_task_cancels_after_shutdown_timeout() -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()
    keep_running = asyncio.Event()

    async def run() -> None:
        started.set()
        try:
            await keep_running.wait()
        finally:
            stopped.set()

    async with lifespan_module._lifespan_task(
        run,
        name="test_task",
        shutdown_timeout=0,
    ):
        await started.wait()

    assert stopped.is_set()
