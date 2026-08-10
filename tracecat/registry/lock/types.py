"""Types for registry version locks."""

from typing import Self

from pydantic import BaseModel, Field, model_validator


class RegistryLock(BaseModel):
    """Registry version lock with action-level bindings for O(1) resolution.

    Attributes:
        origins: Maps repository origin to pinned version string.
            Example: {"tracecat_registry": "2024.12.10.123456"}
        actions: Maps action name to its source origin.
            Example: {"core.transform.reshape": "tracecat_registry"}
        origin_fingerprints: Optional immutable origin fingerprints. New locks
            prefer execution artifact SHA-256 hashes and fall back to manifest
            fingerprints when older versions do not have artifact hashes.
    """

    origins: dict[str, str]
    actions: dict[str, str]
    origin_fingerprints: dict[str, str] = Field(default_factory=dict)

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

        unknown_fingerprint_origins = set(self.origin_fingerprints) - valid_origins
        if unknown_fingerprint_origins:
            raise ValueError(
                "Origin fingerprints reference unknown origins: "
                f"{sorted(unknown_fingerprint_origins)}. "
                f"Valid origins are: {sorted(valid_origins)}"
            )

        return self
