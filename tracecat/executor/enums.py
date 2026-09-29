"""Lightweight execution types shared by workers and agent runtimes."""

from __future__ import annotations

from enum import StrEnum


class ExecutorBackendType(StrEnum):
    """Execution isolation mode."""

    DIRECT = "direct"
    NSJAIL = "nsjail"

    @classmethod
    def _missing_(cls, value: object) -> ExecutorBackendType | None:
        # Keep the deployed backend name as an input alias only.
        if value == "ephemeral":
            return cls.NSJAIL
        return None

    @classmethod
    def from_config(cls, value: str | None) -> ExecutorBackendType:
        """Parse the backend setting, treating an empty value as unset."""
        try:
            return cls((value or "").strip() or cls.DIRECT)
        except ValueError:
            raise ValueError(
                f"Invalid TRACECAT__EXECUTOR_BACKEND: {value!r}. "
                "Expected 'direct' or 'nsjail' ('ephemeral' is an alias for "
                "'nsjail')."
            ) from None

    @property
    def uses_nsjail(self) -> bool:
        """Whether this backend runs workloads inside nsjail."""
        return self is ExecutorBackendType.NSJAIL
