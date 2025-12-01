"""Core registry UDFs for case durations (feature-flagged)."""

from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.logger import logger

if is_feature_enabled(FeatureFlag.CASE_DURATIONS):
    logger.info(
        "Case durations feature flag is enabled. Enabling case durations integration."
    )
    from tracecat_ee.cases.durations import list_case_durations
else:
    list_case_durations = None
    logger.info(
        "Case durations feature flag is not enabled. "
        "Skipping case durations integration."
    )
