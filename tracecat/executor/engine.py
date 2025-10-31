import logging
import os
from contextlib import contextmanager

import ray

from tracecat import config
from tracecat.logger import logger

DEFAULT_NUM_WORKERS = min(os.cpu_count() or 8, 8)


@contextmanager
def setup_ray():
    context = ray.init(
        namespace="tracecat",
        dashboard_host="0.0.0.0",
        include_dashboard=True,
        log_to_driver=config.TRACECAT__APP_ENV == "development",
        logging_level=logging.WARNING,
        num_cpus=DEFAULT_NUM_WORKERS,
        resources={"cpu": DEFAULT_NUM_WORKERS},
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
