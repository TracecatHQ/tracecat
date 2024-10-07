from typing import Any

from pydantic_core import ValidationError


class TracecatException(Exception):
    """Tracecat generic user-facing exception"""

    def __init__(self, *args, detail: Any | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.detail = detail


class RegistryActionError(TracecatException):
    """Exception raised when a registry UDF error occurs."""


class RegistryValidationError(TracecatException):
    """Exception raised when a registry validation error occurs."""

    def __init__(self, *args, key: str, err: ValidationError | str | None = None):
        super().__init__(*args)
        self.key = key
        self.err = err
