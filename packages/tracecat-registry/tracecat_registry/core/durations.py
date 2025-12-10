"""Core registry UDFs for case durations (feature-flagged)."""

from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.logger import logger

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
