"""Tracecat exceptions

Note
----
This module contains exceptions that are user-facing, meaning they are
meant to be displayed to the user in a user-friendly way. We expose these
through FastAPI exception handlers, which match the exception type.
"""

from typing import Any


class TracecatException(Exception):
    """Tracecat generic user-facing exception"""

    def __init__(self, *args, detail: Any | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.detail = detail


class TracecatValidationError(TracecatException):
    """Tracecat user-facting validation error"""


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
