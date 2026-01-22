"""ExpectedField schema for template action expects definitions.

This is separated from expectations.py to avoid pulling in lark when only
the ExpectedField type is needed.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict

# Sentinel value to distinguish "no default specified" from "default=None"
# This value survives JSON serialization and can be checked after deserialization
_UNSET_SENTINEL = "__TRACECAT_UNSET__"


class ExpectedField(BaseModel):
    """Schema for a field in a template action's expects definition.

    Note: The default field uses a sentinel to distinguish between
    "no default specified" (required field) and "default is explicitly None"
    (optional field).
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    description: str | None = None
    default: Any = _UNSET_SENTINEL
    enum: list[str] | None = None
    """Optional list of allowed values for this field."""
    optional: bool | None = None
    """Whether this field is optional (alternative to using default)."""

    def has_default(self) -> bool:
        """Check if a default value was explicitly specified."""
        return self.default != _UNSET_SENTINEL
