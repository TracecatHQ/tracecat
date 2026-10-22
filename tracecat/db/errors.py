from collections.abc import Iterator

from asyncpg import DataError as AsyncpgDataError
from asyncpg import UniqueViolationError
from psycopg.errors import DataError as PsycopgDataError
from psycopg.errors import UniqueViolation
from sqlalchemy.exc import DBAPIError


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


def is_unique_violation(error: BaseException, *constraint_names: str) -> bool:
    """Match only unique violations with one of the specified constraint names."""
    for item in _exception_chain(error):
        if isinstance(item, UniqueViolationError):
            name = item.as_dict().get("constraint_name")
        elif isinstance(item, UniqueViolation):
            name = item.diag.constraint_name
        else:
            continue
        if name in constraint_names:
            return True
    return False


def is_data_error(error: BaseException) -> bool:
    """Match PostgreSQL data exceptions, such as NUL bytes or overlong strings."""
    return any(
        isinstance(item, AsyncpgDataError | PsycopgDataError)
        for item in _exception_chain(error)
    )
