import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import EntityField
from tracecat.entities.enums import FieldType
from tracecat.entities.models import (
    EntityCreate,
    EntityFieldCreate,
    EntityFieldOptionRead,
    EntityFieldRead,
    EntityFieldUpdate,
    EntityRead,
    EntityUpdate,
    coerce_default_value,
)
from tracecat.entities.service import EntityService
from tracecat.records.model import RecordCreate, RecordRead, RecordUpdate
from tracecat.records.service import RecordService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError
from tracecat.types.pagination import (
    CursorPaginatedResponse,
    CursorPaginationParams,
)

router = APIRouter(prefix="/entities", tags=["entities"])


WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.get("", response_model=list[EntityRead])
async def list_entities(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    include_inactive: bool = Query(False),
) -> list[EntityRead]:
    service = EntityService(session, role)
    entities = await service.list_entities(include_inactive=include_inactive)
    return [EntityRead.model_validate(e, from_attributes=True) for e in entities]


@router.get("/{entity_id}", response_model=EntityRead)
async def get_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
) -> EntityRead:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return EntityRead.model_validate(entity, from_attributes=True)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: EntityCreate,
) -> None:
    service = EntityService(session, role)
    try:
        await service.create_entity(params)
    except IntegrityError as e:
        # Likely unique constraint on (owner_id, key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Entity key already exists in this workspace",
        ) from e


@router.patch("/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    params: EntityUpdate,
) -> None:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        await service.update_entity(entity, params)
    except IntegrityError as e:
        # Catch any DB constraint errors on update (defensive)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflict updating entity due to database constraints",
        ) from e


@router.delete("/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
) -> None:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await service.delete_entity(entity)


@router.patch("/{entity_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
) -> None:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await service.deactivate_entity(entity)


@router.patch("/{entity_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
async def activate_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
) -> None:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await service.activate_entity(entity)


@router.post("/{entity_id}/fields", status_code=status.HTTP_201_CREATED)
async def create_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    params: EntityFieldCreate,
) -> None:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    # params validated via Pydantic (default_value coerced in model)
    # Extra enforcement: default must exist in options for select types is handled in model
    try:
        await service.fields.create_field(entity, params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except IntegrityError as e:
        # Likely unique constraint on (entity_id, key) or option key
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Field or option key already exists",
        ) from e


@router.patch("/{entity_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    field_id: uuid.UUID,
    params: EntityFieldUpdate,
) -> None:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    try:
        field: EntityField = await service.fields.get_field(entity, field_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    # Coerce default_value against existing field.type if provided in PATCH
    if "default_value" in params.model_fields_set:
        try:
            params.default_value = coerce_default_value(
                field.type, params.default_value
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
            ) from e

    # Enforce default membership for SELECT/MULTI_SELECT
    # 1) If default_value is being changed, enforce against provided/new options
    # 2) If options are being changed, ensure existing default remains valid
    if (
        "default_value" in params.model_fields_set or params.options is not None
    ) and field.type in (
        FieldType.SELECT,
        FieldType.MULTI_SELECT,
    ):
        # Option keys after update: if options provided, use those; else use DB
        if params.options is not None:
            option_keys = {opt.key for opt in params.options}
        else:
            option_keys = {opt.key for opt in field.options}
        # Determine which default to validate: new (if provided) or existing
        default_to_check = (
            params.default_value
            if "default_value" in params.model_fields_set
            else field.default_value
        )
        if field.type == FieldType.SELECT and default_to_check is not None:
            if default_to_check not in option_keys:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Default value must match one of the option keys",
                )
        if field.type == FieldType.MULTI_SELECT and default_to_check is not None:
            invalid = [v for v in default_to_check if v not in option_keys]
            if invalid:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Default values not in options: {', '.join(invalid)}",
                )

    try:
        await service.fields.update_field(field, params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except IntegrityError as e:
        # Likely unique constraint on option keys per field
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Field or option key already exists",
        ) from e


@router.get("/{entity_id}/fields", response_model=list[EntityFieldRead])
async def list_fields(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    include_inactive: bool = Query(False),
) -> list[EntityFieldRead]:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    fields = await service.fields.list_fields(entity, include_inactive=include_inactive)
    out: list[EntityFieldRead] = []
    for f in fields:
        options = [
            EntityFieldOptionRead.model_validate(opt, from_attributes=True)
            for opt in f.options
        ]
        out.append(
            EntityFieldRead(
                id=f.id,
                entity_id=f.entity_id,
                key=f.key,
                type=f.type,
                display_name=f.display_name,
                description=f.description,
                is_active=f.is_active,
                default_value=f.default_value,
                created_at=f.created_at,
                updated_at=f.updated_at,
                options=options,
            )
        )
    return out


@router.get("/{entity_id}/fields/{field_id}", response_model=EntityFieldRead)
async def get_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    field_id: uuid.UUID,
) -> EntityFieldRead:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        field = await service.fields.get_field(entity, field_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return EntityFieldRead(
        id=field.id,
        entity_id=field.entity_id,
        key=field.key,
        type=field.type,
        display_name=field.display_name,
        description=field.description,
        is_active=field.is_active,
        default_value=field.default_value,
        created_at=field.created_at,
        updated_at=field.updated_at,
        options=[
            EntityFieldOptionRead.model_validate(opt, from_attributes=True)
            for opt in field.options
        ],
    )


@router.delete("/{entity_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    field_id: uuid.UUID,
) -> None:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        field = await service.fields.get_field(entity, field_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await service.fields.delete_field(field)


@router.patch(
    "/{entity_id}/fields/{field_id}/deactivate",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    field_id: uuid.UUID,
) -> None:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        field = await service.fields.get_field(entity, field_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await service.fields.deactivate_field(field)


@router.patch(
    "/{entity_id}/fields/{field_id}/activate",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def activate_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: uuid.UUID,
    field_id: uuid.UUID,
) -> None:
    service = EntityService(session, role)
    try:
        entity = await service.get_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    try:
        field = await service.fields.get_field(entity, field_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await service.fields.activate_field(field)


# Record endpoints


@router.get("/{entity_id}/records", response_model=CursorPaginatedResponse[RecordRead])
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


@router.get("/{entity_id}/records/{record_id}", response_model=RecordRead)
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


@router.post("/{entity_id}/records", status_code=status.HTTP_201_CREATED)
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


@router.patch(
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


@router.delete(
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
