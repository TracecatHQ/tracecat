"""Core registry UDFs for case durations (feature-flagged)."""

import logging

from tracecat_registry.config import FeatureFlag, is_feature_enabled

logger = logging.getLogger(__name__)

if is_feature_enabled(FeatureFlag.CASE_DURATIONS):
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
