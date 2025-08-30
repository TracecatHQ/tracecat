import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.entities.router import router as entities_router
from tracecat.entities.service import EntityService
from tracecat.records.model import RecordCreate, RecordRead, RecordUpdate
from tracecat.records.service import RecordService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError
from tracecat.types.pagination import (
    CursorPaginatedResponse,
    CursorPaginationParams,
)

records_router = APIRouter(prefix="/records", tags=["records"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@entities_router.get(
    "/{entity_id}/records", response_model=CursorPaginatedResponse[RecordRead]
)
async def list_entity_records(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    reverse: bool = Query(False, description="Reverse pagination direction"),
) -> CursorPaginatedResponse[RecordRead]:
    entities = EntityService(session, role)
    records = RecordService(session, role)
    try:
        entity = await entities.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    params = CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
    return await records.list_entity_records(entity, params)


@entities_router.get("/{entity_id}/records/{record_id}", response_model=RecordRead)
async def get_entity_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    record_id: uuid.UUID,
) -> RecordRead:
    entities = EntityService(session, role)
    records = RecordService(session, role)
    try:
        entity = await entities.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        record = await records.get_record(entity, record_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return RecordRead.model_validate(record, from_attributes=True)


@entities_router.post("/{entity_id}/records", status_code=status.HTTP_201_CREATED)
async def create_entity_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    params: RecordCreate,
) -> None:
    entities = EntityService(session, role)
    records = RecordService(session, role)
    try:
        entity = await entities.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        await records.create_record(entity, params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e


@entities_router.patch(
    "/{entity_id}/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def update_entity_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    record_id: uuid.UUID,
    params: RecordUpdate,
) -> None:
    entities = EntityService(session, role)
    records = RecordService(session, role)
    try:
        entity = await entities.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        record = await records.get_record(entity, record_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        await records.update_record(record, params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e


@entities_router.delete(
    "/{entity_id}/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_entity_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    record_id: uuid.UUID,
) -> None:
    entities = EntityService(session, role)
    records = RecordService(session, role)
    try:
        entity = await entities.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        record = await records.get_record(entity, record_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await records.delete_record(record)


@records_router.get("", response_model=CursorPaginatedResponse[RecordRead])
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


@records_router.get("/{record_id}", response_model=RecordRead)
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
