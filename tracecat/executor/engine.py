import os
from contextlib import contextmanager

import ray

from tracecat.logger import logger

EXECUTION_TIMEOUT = 300
DEFAULT_NUM_WORKERS = min(os.cpu_count() or 8, 8)


@contextmanager
def setup_ray():
    # Set Ray logging to reduce disk usage
    os.environ.setdefault("RAY_ROTATION_MAX_BYTES", "1048576")  # 1MB max log size
    os.environ.setdefault("RAY_ROTATION_BACKUP_COUNT", "1")     # Keep only 1 backup
    os.environ.setdefault("RAY_BACKEND_LOG_LEVEL", "40")        # ERROR level only
    
    context = ray.init(
        namespace="tracecat",
        dashboard_host="0.0.0.0",
        include_dashboard=True,
        num_cpus=DEFAULT_NUM_WORKERS,
        resources={"cpu": DEFAULT_NUM_WORKERS},
        log_to_driver=False,  # Don't send logs to driver
    )
    try:
        logger.info(
            "Connected to Ray cluster",
            resources=ray.cluster_resources(),
            dashboard_url=context.dashboard_url,
            ray_version=context.ray_version,
            ray_commit=context.ray_commit,
        )
        yield
    finally:
        ray.shutdown()
