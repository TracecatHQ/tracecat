"""Registry test configuration.

This conftest.py enables feature flags needed for EE UDF tests.
The flags must be added to config.TRACECAT__FEATURE_FLAGS BEFORE
tracecat.api.app is imported, because internal_router.py conditionally
includes task_router/duration_router at import time based on is_feature_enabled().
"""

from tracecat import config
from tracecat.feature_flags.enums import FeatureFlag

# Add EE feature flags needed for tests.
# This must happen before tracecat.api.app is imported (which happens in test files).
# The root conftest.py imports from tracecat.config but NOT from tracecat.api.app,
# so this modification happens before internal_router.py is loaded.
config.TRACECAT__FEATURE_FLAGS.add(FeatureFlag.CASE_TASKS)
config.TRACECAT__FEATURE_FLAGS.add(FeatureFlag.CASE_DURATIONS)
