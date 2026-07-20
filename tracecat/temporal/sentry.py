"""Shared Sentry setup for Temporal workers running in one process."""

from __future__ import annotations

import os

import sentry_sdk

from tracecat import __version__ as APP_VERSION
from tracecat import config
from tracecat.logger import logger

_initialized = False


def initialize_temporal_sentry() -> bool:
    """Initialize the process-wide Sentry SDK once when a DSN is configured."""
    sentry_dsn = os.environ.get("SENTRY_DSN")
    if not sentry_dsn:
        return False

    global _initialized
    if _initialized:
        return True

    app_env = config.TRACECAT__APP_ENV
    temporal_namespace = config.TEMPORAL__CLUSTER_NAMESPACE
    sentry_environment = (
        config.SENTRY_ENVIRONMENT_OVERRIDE or f"{app_env}-{temporal_namespace}"
    )
    logger.info("Initializing Sentry interceptor")
    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=sentry_environment,
        release=f"tracecat@{APP_VERSION}",
    )
    _initialized = True
    logger.info(
        "Sentry initialized",
        environment=sentry_environment,
        app_env=app_env,
        temporal_namespace=temporal_namespace,
    )
    return True
