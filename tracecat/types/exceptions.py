"""Tracecat exceptions

Note
----
This module contains exceptions that are user-facing, meaning they are
meant to be displayed to the user in a user-friendly way. We expose these
through FastAPI exception handlers, which match the exception type.
"""


class TracecatException(Exception):
    """Tracecat generic user-facing exception"""

    pass


class TracecatValidationError(TracecatException):
    """Tracecat user-facting validation error"""

    pass


class TracecatDSLError(TracecatValidationError):
    """Tracecat user-facing DSL error"""

    pass


class TracecatExpressionError(TracecatException):
    """Tracecat user-facing expression error"""

    pass


class TracecatCredentialsError(TracecatException):
    """Tracecat user-facing credentials error"""

    pass
