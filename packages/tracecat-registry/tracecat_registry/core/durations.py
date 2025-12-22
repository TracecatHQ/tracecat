"""Core registry UDFs for case durations (feature-flagged)."""

from tracecat_registry import config
from tracecat_registry._internal.logger import logger

if config.flags.case_durations:
    logger.info(
        "Case durations feature flag is enabled. Enabling case durations integration."
    )
    from tracecat_ee.cases.durations import get_case_metrics
else:
    get_case_metrics = None
    logger.info(
        "Case durations feature flag is not enabled. "
        "Skipping case durations integration."
    )
