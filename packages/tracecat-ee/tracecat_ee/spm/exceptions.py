"""Domain exceptions for SPM services."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any


def _jsonable_context(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable_context(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable_context(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable_context(item) for item in value]
    return value


class SpmError(Exception):
    """Base class for SPM domain errors with machine-readable codes."""

    def __init__(self, message: str, *, code: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.context = {
            key: _jsonable_context(value)
            for key, value in context.items()
            if value is not None
        }

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.context}


class SpmAuthenticationError(SpmError):
    """Raised when an SPM endpoint request cannot be authenticated."""


class SpmConflictError(SpmError):
    """Raised when an SPM request conflicts with current resource state."""


class SpmControlCatalogError(SpmError):
    """Raised when the static SPM control catalog cannot be loaded."""

    def __init__(
        self,
        message: str = "SPM control catalog error.",
        *,
        code: str,
        path: str | Path | None = None,
        ref: str | uuid.UUID | None = None,
        existing_path: str | Path | None = None,
    ) -> None:
        self.path = path
        self.ref = ref
        self.existing_path = existing_path
        super().__init__(
            message,
            code=code,
            path=path,
            ref=ref,
            existing_path=existing_path,
        )


class SpmNotFoundError(SpmError):
    """Raised when an SPM resource is not found."""
