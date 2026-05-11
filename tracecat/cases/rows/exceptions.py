from typing import NoReturn

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_409_CONFLICT


async def raise_case_row_link_integrity_error(
    session: AsyncSession, exc: IntegrityError
) -> NoReturn:
    constraint_name = getattr(exc.orig, "constraint_name", None)
    await session.rollback()
    if constraint_name == "uq_case_table_row_link" or "uq_case_table_row_link" in str(
        exc
    ):
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail={
                "code": "CASE_ROW_ALREADY_LINKED",
                "message": "This table row is already linked to the case.",
            },
        ) from exc
    raise exc
