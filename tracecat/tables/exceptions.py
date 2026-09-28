"""Safe, structured errors for user-provided table rows."""

from collections.abc import Awaitable, Sequence
from typing import Literal, TypedDict

from asyncpg.exceptions import NotNullViolationError, UniqueViolationError
from sqlalchemy.exc import IntegrityError

from tracecat.db.models import TableColumn
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


async def execute_row_write[T](
    operation: Awaitable[T], columns: Sequence[TableColumn]
) -> T:
    """Translate known row constraints without exposing SQL or submitted values.

    Unknown NOT NULL columns and constraint types remain server errors.
    """
    error: TableRowError | None = None
    try:
        return await operation
    except IntegrityError as exc:
        cause: BaseException | None = exc.orig
        while cause is not None:
            if isinstance(cause, NotNullViolationError):
                column_name = cause.as_dict().get("column_name")
                column = next((c for c in columns if c.name == column_name), None)
                if column is not None:
                    error = TableRowError(
                        "null_not_allowed",
                        f"Column '{column.name}' cannot be null.",
                        column=column.name,
                    )
                break
            if isinstance(cause, UniqueViolationError):
                error = TableRowError(
                    "duplicate_value", "A value already exists in a unique column."
                )
                break
            cause = cause.__cause__
        if error is None:
            raise
    # Raise outside the handler so raw database values are not retained in the
    # exception context consumed by workflow and observability code.
    assert error is not None
    raise error
