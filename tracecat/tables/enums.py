from enum import StrEnum


class SqlType(StrEnum):
    """Supported SQL types."""

    TEXT = "TEXT"
    BIGINT = "BIGINT"
    INTEGER = "INTEGER"
    NUMERIC = "NUMERIC"
    BOOLEAN = "BOOLEAN"
    TIMESTAMP = "TIMESTAMP"
    TIMESTAMPTZ = "TIMESTAMPTZ"
    JSONB = "JSONB"
    UUID = "UUID"
    ENUM = "ENUM"
