from typing import Any


class TracecatException(Exception):
    """Tracecat generic user-facing exception"""

    def __init__(self, *args, detail: Any | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.detail = detail


class RegistryActionError(TracecatException):
    """Exception raised when a registry UDF error occurs."""


class ActionIsInterfaceError(RegistryActionError):
    """Exception raised when attempting to directly call an action that only defines an interface."""

    def __init__(self):
        super().__init__(
            "This UDF only defines an interface and cannot be invoked directly. "
            "If you are seeing this error, please contact your administrator."
        )
