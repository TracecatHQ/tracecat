from typing import Any

from asyncpg import UniqueViolationError
from sqlalchemy.exc import IntegrityError


def unique_violation(constraint_name: str) -> IntegrityError:
    """Build a minimal IntegrityError that ``is_unique_violation`` recognizes."""
    orig: Any = UniqueViolationError("duplicate key value violates unique constraint")
    orig.constraint_name = constraint_name
    return IntegrityError("INSERT ...", {}, orig)
