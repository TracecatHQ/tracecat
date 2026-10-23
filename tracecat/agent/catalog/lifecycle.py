"""Provider lifecycle for platform catalog models."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tracecat.logger import logger

LIFECYCLE_METADATA_KEY = "lifecycle"

type ModelLifecycleStatus = Literal["deprecated", "retired"]


class ModelLifecycle(BaseModel):
    """Provider-announced deprecation of one model."""

    # Ignore unknown keys so older workers still read newer lifecycle data.
    model_config = ConfigDict(extra="ignore", frozen=True)

    deprecated_on: date | None = None
    retires_on: date
    replacement: str | None = Field(
        default=None,
        description="Same-provider catalog model that retired runs move to.",
    )

    def status_on(self, today: date | None = None) -> ModelLifecycleStatus:
        """Return ``retired`` from ``retires_on`` onward, else ``deprecated``."""
        today = today or datetime.now(UTC).date()
        return "retired" if today >= self.retires_on else "deprecated"


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
