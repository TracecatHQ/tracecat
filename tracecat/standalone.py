"""Development-only combined API and Temporal worker entrypoint.

This module is intended for lightweight development instances. It runs the API
and all Temporal workers in one process and one asyncio event loop; it is not a
production deployment topology.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import uvicorn
import uvloop
from alembic.config import Config as AlembicConfig
from temporalio.client import Client

from alembic import command as alembic_command
from tracecat import config
from tracecat.agent.executor_worker import main as agent_executor_worker_main
from tracecat.agent.worker import main as agent_worker_main
from tracecat.dsl.client import connect_to_temporal, get_temporal_client
from tracecat.dsl.plugins import TracecatPydanticAIPlugin
from tracecat.dsl.worker import main as dsl_worker_main
from tracecat.executor.worker import main as executor_worker_main
from tracecat.logger import logger
from tracecat.storage.blob import close_storage_client_cache
from tracecat.temporal.worker_lifecycle import (
    install_worker_shutdown_signal_handlers,
)
from tracecat.uvicorn_server import NoSignalUvicornServer

ComponentMain = Callable[[asyncio.Event], Coroutine[Any, Any, None]]


@dataclass(frozen=True)
class Component:
    """A named standalone component coroutine."""

    name: str
    main: ComponentMain


def _run_migrations() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    alembic_config = AlembicConfig(str(repo_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(repo_root / "alembic"))
    alembic_config.set_main_option("prepend_sys_path", str(repo_root))
    logger.info("Running standalone database migrations")
    alembic_command.upgrade(alembic_config, "head")
    logger.info("Standalone database migrations complete")


async def _serve_api(shutdown_event: asyncio.Event) -> None:
    port = int(os.environ.get("PORT") or 8000)
    server = NoSignalUvicornServer(
        uvicorn.Config(
            "tracecat.api.app:app",
            host="0.0.0.0",
            port=port,
            log_level=(os.environ.get("LOG_LEVEL") or "info").lower(),
        )
    )
    server_task = asyncio.create_task(server.serve(), name="uvicorn-server")
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="uvicorn-shutdown")
    try:
        done, _ = await asyncio.wait(
            (server_task, shutdown_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if server_task in done:
            await server_task
            return

        server.should_exit = True
        await server_task
    finally:
        if not shutdown_task.done():
            shutdown_task.cancel()
            with suppress(asyncio.CancelledError):
                await shutdown_task


async def _run_dsl_worker(
    shutdown_event: asyncio.Event, *, temporal_client: Client
) -> None:
    await dsl_worker_main(
        shutdown_event,
        close_storage_cache=False,
        temporal_client=temporal_client,
    )


async def _run_executor_worker(
    shutdown_event: asyncio.Event, *, temporal_client: Client
) -> None:
    await executor_worker_main(
        shutdown_event,
        close_storage_cache=False,
        capture_internal_server_signals=False,
        temporal_client=temporal_client,
    )


async def _run_agent_worker(
    shutdown_event: asyncio.Event, *, temporal_client: Client
) -> None:
    await agent_worker_main(
        shutdown_event,
        close_storage_cache=False,
        temporal_client=temporal_client,
    )


async def _run_agent_executor_worker(
    shutdown_event: asyncio.Event, *, temporal_client: Client
) -> None:
    await agent_executor_worker_main(
        shutdown_event,
        close_storage_cache=False,
        capture_internal_server_signals=False,
        temporal_client=temporal_client,
    )


def _log_component_failure(name: str, error: BaseException | None) -> None:
    if error is None:
        logger.error("Standalone component exited unexpectedly", component=name)
        return
    logger.opt(exception=(type(error), error, error.__traceback__)).error(
        "Standalone component crashed",
        component=name,
        error_type=type(error).__name__,
    )


async def _run_component(component: Component, shutdown_event: asyncio.Event) -> None:
    try:
        await component.main(shutdown_event)
    except asyncio.CancelledError:
        raise
    except BaseException as e:
        raise RuntimeError(f"Standalone component {component.name} crashed") from e


async def _supervise(
    shutdown_event: asyncio.Event,
    *,
    temporal_client: Client,
    plugin_temporal_client: Client,
) -> int:
    components = (
        Component("api", _serve_api),
        Component(
            "dsl-worker",
            partial(_run_dsl_worker, temporal_client=plugin_temporal_client),
        ),
        Component(
            "executor-worker",
            partial(_run_executor_worker, temporal_client=temporal_client),
        ),
        Component(
            "agent-worker",
            partial(_run_agent_worker, temporal_client=plugin_temporal_client),
        ),
        Component(
            "agent-executor-worker",
            partial(
                _run_agent_executor_worker,
                temporal_client=temporal_client,
            ),
        ),
    )
    tasks = {
        asyncio.create_task(
            _run_component(component, shutdown_event), name=component.name
        ): component.name
        for component in components
    }
    shutdown_task = asyncio.create_task(
        shutdown_event.wait(), name="standalone-shutdown"
    )
    reported_failures: set[asyncio.Task[None]] = set()
    failed = False

    try:
        done, _ = await asyncio.wait(
            (*tasks, shutdown_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_task not in done:
            failed = True
            for task, name in tasks.items():
                if task in done:
                    _log_component_failure(name, task.exception())
                    reported_failures.add(task)
            shutdown_event.set()

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task, result in zip(tasks, results, strict=True):
            if isinstance(result, BaseException):
                failed = True
                if task not in reported_failures:
                    _log_component_failure(tasks[task], result)
    finally:
        if not shutdown_task.done():
            shutdown_task.cancel()
            with suppress(asyncio.CancelledError):
                await shutdown_task
        await close_storage_client_cache()

    return 1 if failed else 0


async def main() -> int:
    """Run migrations and supervise all development components."""
    if config.TRACECAT__STANDALONE_RUN_MIGRATIONS:
        _run_migrations()

    temporal_client = await get_temporal_client()
    plugin_temporal_client = await connect_to_temporal(
        plugins=[TracecatPydanticAIPlugin()]
    )
    shutdown_event = asyncio.Event()
    with install_worker_shutdown_signal_handlers(shutdown_event):
        exit_code = await _supervise(
            shutdown_event,
            temporal_client=temporal_client,
            plugin_temporal_client=plugin_temporal_client,
        )
    logger.info("Standalone shutdown complete", exit_code=exit_code)
    return exit_code


def run() -> int:
    """Run the standalone supervisor in one uvloop event loop."""
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        return loop.run_until_complete(main())
    except Exception:
        logger.exception("Standalone process failed")
        return 1
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


if __name__ == "__main__":
    raise SystemExit(run())
