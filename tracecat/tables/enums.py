from enum import StrEnum


class SqlType(StrEnum):
    """Supported SQL types."""

    TEXT = "TEXT"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    TIMESTAMP = "TIMESTAMP"
    TIMESTAMPTZ = "TIMESTAMPTZ"
    JSONB = "JSONB"
    UUID = "UUID"
