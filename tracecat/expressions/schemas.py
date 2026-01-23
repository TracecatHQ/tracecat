"""ExpectedField schema for template action expects definitions.

This is separated from expectations.py to avoid pulling in lark when only
the ExpectedField type is needed.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, model_serializer

from tracecat.common import UNSET


class ExpectedField(BaseModel):
    """Schema for a field in a template action's expects definition.

    Note: The default field uses a sentinel to distinguish between
    "no default specified" (required field) and "default is explicitly None"
    (optional field).
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    description: str | None = None
    default: Any = UNSET
    enum: list[str] | None = None
    """Optional list of allowed values for this field."""
    optional: bool | None = None
    """Whether this field is optional (alternative to using default)."""

    def has_default(self) -> bool:
        """Check if a default value was explicitly specified."""
        return self.default is not UNSET

    @model_serializer(mode="plain")
    def _serialize(self) -> dict[str, Any]:
        """Serialize model, omitting 'default' field when it's UNSET."""
        data = {k: getattr(self, k) for k in self.model_fields if k != "default"}
        if self.has_default():
            data["default"] = self.default
        return data
