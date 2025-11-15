from enum import StrEnum


class SqlType(StrEnum):
    """Supported SQL types."""

    TEXT = "TEXT"
    INTEGER = "INTEGER"  # Maps to BIGINT (8 bytes)
    NUMERIC = "NUMERIC"
    DATE = "DATE"
    BOOLEAN = "BOOLEAN"
    TIMESTAMP = "TIMESTAMP"
    TIMESTAMPTZ = "TIMESTAMPTZ"
    JSONB = "JSONB"
    UUID = "UUID"
    SELECT = "SELECT"
    MULTI_SELECT = "MULTI_SELECT"
