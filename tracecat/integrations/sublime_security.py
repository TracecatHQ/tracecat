from tracecat.integrations._registry import registry
from tracecat.logger import standard_logger

logger = standard_logger(__name__)


@registry.register(
    description="Test sublime security integration. Do not use in production.",
)
def test() -> str:
    """Test sublime security integration."""
    logger.info("Testing sublime security integration")
    return "test_material_security"
