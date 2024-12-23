from typing import Any


class TracecatException(Exception):
    """Tracecat generic user-facing exception"""

    def __init__(self, *args, detail: Any | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.detail = detail


class RegistryActionError(TracecatException):
    """Exception raised when a registry UDF error occurs."""
