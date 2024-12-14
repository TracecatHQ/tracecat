import asyncio
import os
from contextlib import contextmanager
from typing import Any

import ray
from ray.exceptions import RayTaskError

from tracecat.dsl.models import RunActionInput
from tracecat.executor.service import sync_executor_entrypoint
from tracecat.logger import logger
from tracecat.types.auth import Role

EXECUTION_TIMEOUT = 300
DEFAULT_NUM_WORKERS = os.cpu_count() or 8  # Similar to multiprocessing.Pool's default


@contextmanager
def setup_ray():
    ray.init()
    try:
        logger.info("Ray initialized")
        yield
    finally:
        ray.shutdown()


@ray.remote
def run_action_task(input: RunActionInput, role: Role) -> Any:
    """Ray task that runs an action."""
    return sync_executor_entrypoint(input, role)


async def run_action_on_ray_cluster(input: RunActionInput, role: Role) -> Any:
    """Run an action on the ray cluster."""
    obj_ref = run_action_task.remote(input, role)
    try:
        coro = asyncio.to_thread(ray.get, obj_ref)
        return await asyncio.wait_for(coro, timeout=EXECUTION_TIMEOUT)
    except TimeoutError as e:
        logger.error("Action timed out, cancelling task", error=e)
        ray.cancel(obj_ref, force=True)
        raise e
    except RayTaskError as e:
        logger.error("Error running action on ray cluster", error=e)
        if isinstance(e.cause, BaseException):
            raise e.cause from None
        raise e
