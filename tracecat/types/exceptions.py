"""Tracecat exceptions

Note
----
This module contains exceptions that are user-facing, meaning they are
meant to be displayed to the user in a user-friendly way. We expose these
through FastAPI exception handlers, which match the exception type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_core import ValidationError

if TYPE_CHECKING:
    from tracecat.executor.models import ExecutorActionErrorInfo


class TracecatException(Exception):
    """Tracecat generic user-facing exception"""

    def __init__(self, *args, detail: Any | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.detail = detail


class TracecatValidationError(TracecatException):
    """Tracecat user-facing validation error"""


class TracecatDSLError(TracecatValidationError):
    """Tracecat user-facing DSL error"""


class TracecatExpressionError(TracecatException):
    """Tracecat user-facing expression error"""


class TracecatCredentialsError(TracecatException):
    """Tracecat user-facing credentials error"""


class TracecatAuthorizationError(TracecatException):
    """Tracecat user-facing authorization error"""


class TracecatManagementError(TracecatException):
    """Tracecat user-facing management error"""


class TracecatNotFoundError(TracecatException):
    """Raised when a resource is not found in the Tracecat database."""


class TracecatServiceError(TracecatException):
    """Tracecat generic user-facing service error"""


class RegistryError(TracecatException):
    """Generic exception raised when a registry error occurs."""


class RegistryActionError(RegistryError):
    """Exception raised when a registry action error occurs."""


class RegistryValidationError(RegistryError):
    """Exception raised when a registry validation error occurs."""

    def __init__(
        self, *args, key: str | None = None, err: ValidationError | str | None = None
    ):
        super().__init__(*args)
        self.key = key
        self.err = err

    def __reduce__(self):
        return (self.__class__, (self.key, self.err))


class RegistryNotFound(RegistryError):
    """Exception raised when a registry is not found."""


class TaskUnreachable(TracecatException):
    """Raised when a task is unreachable."""


class ExecutorClientError(TracecatException):
    """Exception raised when an error occurs in the executor client."""


class ExecutionError(TracecatException):
    """Exception raised when an error occurs during action execution.
    Use this to wrap errors from the executor so that we should reraise"""

    def __init__(self, info: ExecutorActionErrorInfo):
        self.info = info


class LoopExecutionError(TracecatException):
    """Exception raised when an error occurs during loop execution."""

    def __init__(self, loop_errors: list[ExecutionError]):
        self.loop_errors = loop_errors


class TracecatSettingsError(TracecatException):
    """Exception raised when a setting error occurs."""
