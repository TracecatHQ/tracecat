"""Registry utility exceptions.

These exceptions are used by registry utilities and are independent
of the tracecat core package.
"""

from __future__ import annotations

from typing import Any


class RegistryError(Exception):
    """Base exception for registry errors."""


class ExpressionError(RegistryError):
    """Error evaluating an expression."""

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def __str__(self) -> str:
        return self.message


class TracecatException(RegistryError):
    """Base exception for Tracecat errors.

    This is a local copy used by registry actions to avoid
    importing tracecat.exceptions.
    """

    def __init__(self, message: str, *args: Any) -> None:
        super().__init__(message, *args)
        self.message = message

    def __str__(self) -> str:
        return self.message
