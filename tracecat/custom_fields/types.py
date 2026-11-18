"""Types for the per-process custom field column cache."""

from dataclasses import dataclass

from sqlalchemy.engine.interfaces import ReflectedColumn

# (schema, table)
type ColumnsCacheKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ColumnsCacheEntry:
    """Reflected user-visible columns and the catalog fingerprint they match.

    The fingerprint is the catalog content itself, so a hit is right for any
    transaction that sees that content: DDL from another process or a
    rolled-back transaction changes the fingerprint instead of needing an
    invalidation.
    """

    fingerprint: str
    columns: tuple[ReflectedColumn, ...]
