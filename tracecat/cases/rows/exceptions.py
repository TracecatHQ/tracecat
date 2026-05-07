from typing import Final, NoReturn

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_409_CONFLICT

_CASE_ROW_LINK_CONSTRAINT: Final = "uq_case_table_row_link"
_CASE_ROW_ALREADY_LINKED_CODE: Final = "CASE_ROW_ALREADY_LINKED"
_CASE_ROW_ALREADY_LINKED_MESSAGE: Final = (
    "This table row is already linked to the case."
)


def _exception_text(exc: Exception) -> str:
    orig = getattr(exc, "orig", None)
    if orig is None:
        return str(exc)
    return f"{exc} {orig}"


def _constraint_name(exc: IntegrityError) -> str | None:
    for err in (exc.orig, exc.__cause__, exc.__context__):
        name = getattr(err, "constraint_name", None)
        if isinstance(name, str):
            return name
    return None


def is_duplicate_case_row_link_error(exc: IntegrityError) -> bool:
    if _constraint_name(exc) == _CASE_ROW_LINK_CONSTRAINT:
        return True
    return _CASE_ROW_LINK_CONSTRAINT in _exception_text(exc)


async def raise_case_row_link_integrity_error(
    session: AsyncSession, exc: IntegrityError
) -> NoReturn:
    await session.rollback()
    if is_duplicate_case_row_link_error(exc):
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail={
                "code": _CASE_ROW_ALREADY_LINKED_CODE,
                "message": _CASE_ROW_ALREADY_LINKED_MESSAGE,
            },
        ) from exc
    raise exc
