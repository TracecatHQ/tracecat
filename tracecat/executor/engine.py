import os
from contextlib import contextmanager

import ray

from tracecat.logger import logger

EXECUTION_TIMEOUT = 300
DEFAULT_NUM_WORKERS = min(os.cpu_count() or 8, 8)


def _get_ray_logging_config():
    """Configure Ray logging to reduce disk usage."""
    # Set Ray logging environment variables to reduce disk pressure
    ray_env_vars = {
        # Reduce log file size and rotation
        "RAY_ROTATION_MAX_BYTES": os.environ.get("RAY_ROTATION_MAX_BYTES", "1048576"),  # 1MB
        "RAY_ROTATION_BACKUP_COUNT": os.environ.get("RAY_ROTATION_BACKUP_COUNT", "2"),  # Keep only 2 backups
        
        # Set log level to ERROR to reduce verbosity (40 = ERROR level)
        "RAY_BACKEND_LOG_LEVEL": os.environ.get("RAY_BACKEND_LOG_LEVEL", "40"),
        
        # Disable driver logging to reduce log volume
        "RAY_DISABLE_IMPORT_WARNING": "1",
    }
    
    # Apply environment variables
    for key, value in ray_env_vars.items():
        if key not in os.environ:
            os.environ[key] = value
            logger.debug(f"Set Ray logging config: {key}={value}")
    
    return ray_env_vars


@contextmanager
def setup_ray():
    # Configure Ray logging before initialization
    ray_config = _get_ray_logging_config()
    logger.info("Configuring Ray with reduced logging", config=ray_config)
    
    # Monitor disk usage before starting Ray
    try:
        from tracecat.executor.monitoring import monitor_ray_disk_usage
        monitor_ray_disk_usage()
    except Exception as e:
        logger.warning("Failed to monitor disk usage", error=e)
    
    context = ray.init(
        namespace="tracecat",
        dashboard_host="0.0.0.0",
        include_dashboard=True,
        num_cpus=DEFAULT_NUM_WORKERS,
        resources={"cpu": DEFAULT_NUM_WORKERS},
        log_to_driver=False,  # Disable logging to driver to reduce disk usage
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
