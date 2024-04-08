"""Datadog integrations."""

from tracecat.integrations._registry import registry
from tracecat.logger import standard_logger

logger = standard_logger(__name__)


@registry.register(description="Test datadog integration. Do not use in production.")
def test() -> str:
    """Test datadog integration."""
    logger.info("Testing datadog integration. Woof")
    return "test_datadog"
