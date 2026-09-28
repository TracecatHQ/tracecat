"""Safe, structured errors for user-provided table rows."""

from typing import Literal, TypedDict

from tracecat.exceptions import TracecatException

type TableRowErrorCode = Literal[
    "missing_required_column",
    "null_not_allowed",
    "unknown_column",
    "invalid_value",
    "duplicate_value",
]


class TableRowErrorDetail(TypedDict):
    code: TableRowErrorCode
    column: str | None
    message: str


class TableRowError(TracecatException):
    """A row rejected by its table schema or a uniqueness constraint."""

    def __init__(
        self,
        code: TableRowErrorCode,
        message: str,
        *,
        column: str | None = None,
    ) -> None:
        self.code = code
        detail: TableRowErrorDetail = {
            "code": code,
            "column": column,
            "message": message,
        }
        super().__init__(message, detail=detail)
        self.detail: TableRowErrorDetail = detail
