"""User-facing errors for table and custom-field values."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from tracecat.tables.enums import SqlType


class FieldValueErrorCode(StrEnum):
    """Machine-readable codes for field value validation failures."""

    TABLE_ROW_INVALID_VALUE = "TABLE_ROW_INVALID_VALUE"
    TABLE_ROW_NULL_NOT_ALLOWED = "TABLE_ROW_NULL_NOT_ALLOWED"
    TABLE_ROW_UNKNOWN_COLUMN = "TABLE_ROW_UNKNOWN_COLUMN"
    CASE_FIELD_INVALID_VALUE = "CASE_FIELD_INVALID_VALUE"


_EXPECTED_VALUE_HINTS: dict[SqlType, str] = {
    SqlType.INTEGER: "a whole number",
    SqlType.NUMERIC: "a finite number",
    SqlType.DATE: "an ISO 8601 date (for example, 2026-01-30)",
    SqlType.BOOLEAN: "true, false, 1, or 0",
    SqlType.TIMESTAMPTZ: (
        "an ISO 8601 datetime (for example, 2026-01-30T12:00:00Z) or a Unix timestamp"
    ),
    SqlType.JSONB: "a JSON-serializable value",
    SqlType.SELECT: "one of the configured options",
    SqlType.MULTI_SELECT: "a list of configured options",
    SqlType.TEXT: "text",
}


class FieldValueValidationError(ValueError):
    """Base error for invalid values written to typed dynamic fields."""

    def __init__(
        self,
        message: str,
        *,
        code: FieldValueErrorCode,
        field_name: str,
        expected_type: SqlType | None = None,
        field_key: str,
    ) -> None:
        super().__init__(message)
        detail: dict[str, str] = {
            "code": code.value,
            "message": message,
            field_key: field_name,
        }
        if expected_type is not None:
            detail["expected_type"] = expected_type.value
        self.detail = detail

    @staticmethod
    def _invalid_value_message(
        *,
        label: str,
        field_name: str,
        expected_type: SqlType,
        value: Any,
    ) -> str:
        expected = _EXPECTED_VALUE_HINTS[expected_type]
        message = f"{label} '{field_name}' expects {expected_type.value} ({expected})."
        if value == "":
            return (
                f"{message} Received an empty string; use null to leave a nullable "
                "field empty."
            )
        return f"{message} Received {type(value).__name__}."


class TableRowValidationError(FieldValueValidationError):
    """Raised when a table row does not match its column definitions."""

    @classmethod
    def invalid_value(
        cls,
        *,
        column_name: str,
        expected_type: SqlType,
        value: Any,
    ) -> TableRowValidationError:
        return cls(
            cls._invalid_value_message(
                label="Column",
                field_name=column_name,
                expected_type=expected_type,
                value=value,
            ),
            code=FieldValueErrorCode.TABLE_ROW_INVALID_VALUE,
            field_name=column_name,
            expected_type=expected_type,
            field_key="column",
        )

    @classmethod
    def null_not_allowed(cls, *, column_name: str) -> TableRowValidationError:
        return cls(
            f"Column '{column_name}' does not allow null values.",
            code=FieldValueErrorCode.TABLE_ROW_NULL_NOT_ALLOWED,
            field_name=column_name,
            field_key="column",
        )

    @classmethod
    def unknown_column(
        cls, *, column_name: str, table_name: str
    ) -> TableRowValidationError:
        return cls(
            f"Column '{column_name}' does not exist in table '{table_name}'.",
            code=FieldValueErrorCode.TABLE_ROW_UNKNOWN_COLUMN,
            field_name=column_name,
            field_key="column",
        )


class CaseFieldValidationError(FieldValueValidationError):
    """Raised when a case custom-field value has the wrong type."""

    @classmethod
    def invalid_value(
        cls,
        *,
        field_name: str,
        expected_type: SqlType,
        value: Any,
    ) -> CaseFieldValidationError:
        return cls(
            cls._invalid_value_message(
                label="Custom field",
                field_name=field_name,
                expected_type=expected_type,
                value=value,
            ),
            code=FieldValueErrorCode.CASE_FIELD_INVALID_VALUE,
            field_name=field_name,
            expected_type=expected_type,
            field_key="field",
        )
