import asyncio
import multiprocessing.pool as mp
import os

import uvloop

from tracecat.logger import logger

EXECUTION_TIMEOUT = 300

_pool: mp.Pool | None = None


# We want to be able to serve a looped action
# Before we send out tasks to the executor we should inspect the size of the loop
# and set the right chunk size for each worker


def _init_worker_process():
    """Initialize each worker process with its own event loop"""
    # Configure uvloop for the process and create a new event loop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = uvloop.new_event_loop()
    asyncio.set_event_loop(loop)
    logger.info("Initialized worker process with new event loop", pid=os.getpid())


def get_pool():
    """Get the executor, creating it if it doesn't exist"""
    global _pool
    if _pool is None:
        _pool = mp.Pool(initializer=_init_worker_process, maxtasksperchild=100)
        logger.info("Initialized executor process pool")
    return _pool
