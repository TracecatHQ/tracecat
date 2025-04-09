from enum import StrEnum


class SqlType(StrEnum):
    """Supported SQL types."""

    TEXT = "TEXT"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    JSONB = "JSONB"
