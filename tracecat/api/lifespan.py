"""Context managers for background tasks owned by the API lifespan."""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from tracecat import config
from tracecat.agent.catalog.loader import load_platform_catalog_on_startup
from tracecat.api.common import add_temporal_search_attributes
from tracecat.cases.triggers.consumer import start_case_trigger_consumer
from tracecat.logger import logger
from tracecat.registry.sync.jobs import sync_platform_registry_on_startup

_LIFESPAN_TASK_SHUTDOWN_TIMEOUT_SECONDS = 10.0

type TaskFactory = Callable[[], Coroutine[Any, Any, None]]


async def _stop_lifespan_task(
    task: asyncio.Task[None],
    *,
    name: str,
    shutdown_timeout: float | None,
) -> None:
    owner_task = asyncio.current_task()
    owner_cancellation_count = owner_task.cancelling() if owner_task is not None else 0
    completed_before_shutdown = task.done()
    if shutdown_timeout is not None and not completed_before_shutdown:
        logger.info("Waiting for lifespan task to complete", task=name)
        try:
            await asyncio.wait_for(task, timeout=shutdown_timeout)
        except TimeoutError:
            logger.warning(
                "Lifespan task did not complete in time; cancelling",
                task=name,
                timeout=shutdown_timeout,
            )
        except Exception as e:
            logger.warning(
                "Lifespan task failed during shutdown",
                task=name,
                error=e,
            )
            return
        else:
            logger.info("Lifespan task completed", task=name)
            return

    if not task.done():
        task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        if (
            owner_task is not None
            and owner_task.cancelling() > owner_cancellation_count
        ):
            raise
        logger.debug("Lifespan task cancelled", task=name)
    except Exception as e:
        logger.warning(
            "Lifespan task failed before shutdown"
            if completed_before_shutdown
            else "Lifespan task stopped with error",
            task=name,
            error=e,
        )
    else:
        if completed_before_shutdown:
            logger.debug("Lifespan task had already completed", task=name)


@asynccontextmanager
async def _lifespan_task(
    task_factory: TaskFactory,
    *,
    name: str,
    shutdown_timeout: float | None = None,
) -> AsyncIterator[None]:
    task = asyncio.create_task(task_factory(), name=name)
    logger.debug("Spawned lifespan task", task=name)
    try:
        yield
    finally:
        await _stop_lifespan_task(
            task,
            name=name,
            shutdown_timeout=shutdown_timeout,
        )


@asynccontextmanager
async def temporal_search_attributes_lifespan() -> AsyncIterator[None]:
    """Manage Temporal search attribute registration."""
    async with _lifespan_task(
        add_temporal_search_attributes,
        name="temporal_search_attributes",
    ):
        yield


@asynccontextmanager
async def platform_registry_sync_lifespan() -> AsyncIterator[None]:
    """Manage the non-blocking platform registry startup sync."""
    async with _lifespan_task(
        sync_platform_registry_on_startup,
        name="platform_registry_sync",
        shutdown_timeout=_LIFESPAN_TASK_SHUTDOWN_TIMEOUT_SECONDS,
    ):
        yield


@asynccontextmanager
async def platform_catalog_load_lifespan() -> AsyncIterator[None]:
    """Manage the non-blocking platform catalog startup load."""
    async with _lifespan_task(
        load_platform_catalog_on_startup,
        name="platform_catalog_load",
        shutdown_timeout=_LIFESPAN_TASK_SHUTDOWN_TIMEOUT_SECONDS,
    ):
        yield


@asynccontextmanager
async def case_trigger_consumer_lifespan() -> AsyncIterator[None]:
    """Manage the case trigger consumer when it is enabled."""
    if not config.TRACECAT__CASE_TRIGGERS_ENABLED:
        yield
        return

    async with _lifespan_task(
        start_case_trigger_consumer,
        name="case_trigger_consumer",
    ):
        yield
