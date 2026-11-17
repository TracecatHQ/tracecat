"""Provider lifecycle for platform catalog models."""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from tracecat.agent.catalog.types import ModelLifecycle
from tracecat.logger import logger

LIFECYCLE_METADATA_KEY = "lifecycle"


def read_model_lifecycle(
    metadata: Mapping[str, Any] | None,
) -> ModelLifecycle | None:
    """Parse the lifecycle from catalog row metadata, if any."""
    if not metadata or (raw := metadata.get(LIFECYCLE_METADATA_KEY)) is None:
        return None
    try:
        return ModelLifecycle.model_validate(raw)
    except ValidationError as e:
        logger.warning("Ignoring malformed model lifecycle", error=str(e))
        return None
