"""Field types."""

from enum import StrEnum


class FieldType(StrEnum):
    """Supported field types for entities."""

    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    TEXT = "TEXT"
    BOOL = "BOOL"
    JSON = "JSON"
    """Structured data (dict/list with depth limits)"""
    DATETIME = "DATETIME"
    """Date/Time (stored as ISO strings)"""
    DATE = "DATE"
    """Date (stored as ISO strings)"""
    SELECT = "SELECT"
    """Single select AKA enum. Stored as a string (option ref)."""
    MULTI_SELECT = "MULTI_SELECT"
    """Multi-select AKA multi-enum. Stored as list of strings (option refs)."""
