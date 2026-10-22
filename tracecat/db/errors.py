from collections.abc import Iterator
from typing import cast

from asyncpg import PostgresError, UniqueViolationError
from psycopg.errors import UniqueViolation
from sqlalchemy.exc import DBAPIError

from tracecat.db.types import PostgresConstraintError


def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, DBAPIError) and isinstance(current.orig, BaseException):
            pending.append(current.orig)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)


def get_postgres_error[T: PostgresError](
    error: BaseException, error_type: type[T]
) -> T | None:
    """Find a typed PostgreSQL error through SQLAlchemy and driver wrappers."""
    return next(
        (item for item in _exception_chain(error) if isinstance(item, error_type)),
        None,
    )


def is_unique_violation(error: BaseException, *constraint_names: str) -> bool:
    """Match only unique violations with one of the specified constraint names."""
    for item in _exception_chain(error):
        if isinstance(item, UniqueViolationError):
            name = cast(PostgresConstraintError, item).constraint_name
        elif isinstance(item, UniqueViolation):
            name = item.diag.constraint_name
        else:
            continue
        if name in constraint_names:
            return True
    return False
