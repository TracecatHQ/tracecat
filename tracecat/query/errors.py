"""Typed, user-facing errors raised by shared query execution."""

from __future__ import annotations

from enum import StrEnum

from tracecat.exceptions import TracecatException, TracecatValidationError


class QueryErrorCode(StrEnum):
    """Stable machine-readable query error codes."""

    TIMEOUT = "query_timeout"
    NUMERIC_OVERFLOW = "query_numeric_overflow"


class TracecatQueryTimeoutError(TracecatException):
    """Raised when PostgreSQL cancels a query at its statement timeout."""

    code = QueryErrorCode.TIMEOUT

    def __init__(
        self,
        message: str = (
            "Aggregation timed out. Narrow filters or reduce group cardinality."
        ),
    ) -> None:
        super().__init__(
            message,
            detail={"code": self.code.value, "message": message},
        )


class TracecatQueryOverflowError(TracecatValidationError):
    """Raised when a query result exceeds its supported numeric range."""

    code = QueryErrorCode.NUMERIC_OVERFLOW

    def __init__(
        self,
        message: str = "Aggregation result exceeds the supported numeric range.",
    ) -> None:
        super().__init__(
            message,
            detail={"code": self.code.value, "message": message},
        )
