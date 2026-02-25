"""Types for registry version locks."""

from typing import Any, Self, cast

from pydantic import BaseModel, ValidationError, model_validator


class RegistryLock(BaseModel):
    """Registry version lock with action-level bindings for O(1) resolution.

    Attributes:
        origins: Maps repository origin to pinned version string.
            Example: {"tracecat_registry": "2024.12.10.123456"}
        actions: Maps action name to its source origin.
            Example: {"core.transform.reshape": "tracecat_registry"}
    """

    origins: dict[str, str]
    actions: dict[str, str]

    @model_validator(mode="after")
    def validate_action_origins(self) -> Self:
        """Ensure all action origins reference valid origin keys."""
        # Get valid origin keys and referenced origins from actions
        valid_origins = set(self.origins.keys())
        referenced_origins = set(self.actions.values())

        # Check for orphaned references (actions pointing to non-existent origins)
        orphaned = referenced_origins - valid_origins
        if orphaned:
            raise ValueError(
                f"Actions reference unknown origins: {sorted(orphaned)}. "
                f"Valid origins are: {sorted(valid_origins)}"
            )

        return self


def coerce_registry_lock(value: Any) -> RegistryLock | None:
    """Best-effort coercion for registry lock payloads.

    Supports:
    1. Canonical shape: {"origins": {...}, "actions": {...}}
    2. Legacy flat shape: {"origin": "version"}
    """
    if value is None:
        return None

    if isinstance(value, RegistryLock):
        return value

    if not isinstance(value, dict):
        return None

    try:
        return RegistryLock.model_validate(value)
    except ValidationError:
        if all(
            isinstance(origin, str) and isinstance(version, str)
            for origin, version in value.items()
        ):
            legacy_origins = cast(dict[str, str], value)
            return RegistryLock(origins=legacy_origins, actions={})
        return None
