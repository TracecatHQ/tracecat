import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.records.model import RecordRead
from tracecat.records.service import RecordService
from tracecat.types.exceptions import TracecatNotFoundError
from tracecat.types.pagination import CursorPaginatedResponse, CursorPaginationParams

router = APIRouter(prefix="/records", tags=["records"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.get("/records", response_model=CursorPaginatedResponse[RecordRead])
async def list_records(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(20, ge=1, le=100, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    reverse: bool = Query(False, description="Reverse pagination direction"),
) -> CursorPaginatedResponse[RecordRead]:
    service = RecordService(session, role)
    params = CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
    return await service.list_records(params, entity_id=entity_id)


@router.get("/records/{record_id}", response_model=RecordRead)
async def get_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: uuid.UUID,
) -> RecordRead:
    service = RecordService(session, role)
    try:
        record = await service.get_record_by_id(record_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return RecordRead.model_validate(record, from_attributes=True)
