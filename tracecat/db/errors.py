from collections.abc import Iterator

from asyncpg import (
    CharacterNotInRepertoireError,
    DataError,
    InvalidTextRepresentationError,
    StringDataRightTruncationError,
    UniqueViolationError,
    UntranslatableCharacterError,
)
from psycopg.errors import (
    CharacterNotInRepertoire,
    InvalidTextRepresentation,
    StringDataRightTruncation,
    UniqueViolation,
    UntranslatableCharacter,
)
from sqlalchemy.exc import DBAPIError

# Input the database cannot store; other data exceptions are server defects.
_UNSTORABLE_INPUT_ERRORS = (
    CharacterNotInRepertoireError,
    InvalidTextRepresentationError,
    StringDataRightTruncationError,
    UntranslatableCharacterError,
    CharacterNotInRepertoire,
    InvalidTextRepresentation,
    StringDataRightTruncation,
    UntranslatableCharacter,
)


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


def find_db_error[T: BaseException](
    error: BaseException, error_type: type[T]
) -> T | None:
    """Find a driver error through SQLAlchemy wrappers and exception chaining."""
    return next(
        (item for item in _exception_chain(error) if isinstance(item, error_type)),
        None,
    )


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
    """Match unstorable input, such as NUL bytes, malformed text, or overlong strings."""
    return any(
        isinstance(item, _UNSTORABLE_INPUT_ERRORS)
        # asyncpg raises the bare base class when it cannot encode an argument.
        or type(item) is DataError
        for item in _exception_chain(error)
    )
