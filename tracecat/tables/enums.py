from enum import StrEnum


class SqlType(StrEnum):
    """Supported SQL types."""

    TEXT = "TEXT"
    VARCHAR = "VARCHAR"
    INTEGER = "INTEGER"
    BIGINT = "BIGINT"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    TIMESTAMP = "TIMESTAMP"
    TIMESTAMPTZ = "TIMESTAMPTZ"
    JSONB = "JSONB"
    UUID = "UUID"
