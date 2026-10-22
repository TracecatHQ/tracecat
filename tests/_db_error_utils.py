from typing import Any

from asyncpg import UniqueViolationError
from sqlalchemy.exc import IntegrityError


def unique_violation(constraint_name: str) -> IntegrityError:
    """Build an IntegrityError shaped like a SQLAlchemy-wrapped asyncpg violation."""
    orig: Any = UniqueViolationError("duplicate key value violates unique constraint")
    orig.constraint_name = constraint_name
    error = IntegrityError("INSERT ...", {}, orig)
    error.__cause__ = orig
    return error
