from tracecat.integrations._registry import registry
from tracecat.logger import standard_logger

logger = standard_logger(__name__)


@registry.register(
    description="Test material security integration. Do not use in production.",
)
def test() -> str:
    """Test material security integration."""
    logger.info("Testing material security integration")
    return "test_material_security"
