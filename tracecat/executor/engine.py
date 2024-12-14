import os
from contextlib import contextmanager

import ray

from tracecat.logger import logger

EXECUTION_TIMEOUT = 300
DEFAULT_NUM_WORKERS = min(
    os.cpu_count() or 8, 8
)  # Similar to multiprocessing.Pool's default


@contextmanager
def setup_ray():
    ray.init(
        namespace="tracecat",
        num_cpus=DEFAULT_NUM_WORKERS,
        resources={"cpu": DEFAULT_NUM_WORKERS},
    )
    try:
        logger.info("Ray initialized", resources=ray.cluster_resources())
        yield
    finally:
        ray.shutdown()
