import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Path
from starlette.status import HTTP_404_NOT_FOUND, HTTP_422_UNPROCESSABLE_CONTENT

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError


async def executor_case_id_path_dependency(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: Annotated[str, Path()],
) -> uuid.UUID:
    service = CasesService(session, role)
    try:
        return await service.resolve_case_id(case_id)
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except TracecatNotFoundError as exc:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc


ExecutorCaseIDPath = Annotated[uuid.UUID, Depends(executor_case_id_path_dependency)]
"""A case ID path param that accepts a UUID or a short ID (``CASE-0042`` or ``42``)."""
