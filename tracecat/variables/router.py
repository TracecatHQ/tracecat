from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import VariableID
from tracecat.logger import logger
from tracecat.variables.schemas import (
    VariableCreate,
    VariableRead,
    VariableReadMinimal,
    VariableSearch,
    VariableUpdate,
)
from tracecat.variables.service import VariablesService

router = APIRouter(prefix="/variables", tags=["variables"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]

WorkspaceAdminUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.get("/search", response_model=list[VariableRead])
@require_scope("variable:read")
async def search_variables(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    environment: str | None = Query(None),
    names: set[str] | None = Query(
        None, alias="name", description="Filter by variable name"
    ),
    ids: set[VariableID] | None = Query(
        None, alias="id", description="Filter by variable ID"
    ),
) -> list[VariableRead]:
    service = VariablesService(session, role=role)
    params: dict[str, Any] = {}
    if environment is not None:
        params["environment"] = environment
    if names:
        params["names"] = names
    if ids:
        params["ids"] = ids
    variables = await service.search_variables(VariableSearch(**params))
    return [VariableRead.from_database(variable) for variable in variables]


@router.get("", response_model=list[VariableReadMinimal])
@require_scope("variable:read")
async def list_variables(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    environment: str | None = Query(None),
) -> list[VariableReadMinimal]:
    service = VariablesService(session, role=role)
    variables = await service.list_variables(environment=environment)
    return [
        VariableReadMinimal(
            id=variable.id,
            name=variable.name,
            description=variable.description,
            values=variable.values,
            environment=variable.environment,
        )
        for variable in variables
    ]


@router.get("/{variable_name}", response_model=VariableRead)
@require_scope("variable:read")
async def get_variable_by_name(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    variable_name: str,
    environment: str | None = Query(None),
) -> VariableRead:
    service = VariablesService(session, role=role)
    try:
        variable = await service.get_variable_by_name(
            variable_name, environment=environment
        )
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Variable not found"
        ) from exc
    return VariableRead.from_database(variable)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=VariableRead)
@require_scope("variable:create")
async def create_variable(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    params: VariableCreate,
) -> VariableRead:
    service = VariablesService(session, role=role)
    try:
        variable = await service.create_variable(params)
    except IntegrityError as exc:
        logger.error("Variable integrity error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Variable creation integrity error",
        ) from exc
    return VariableRead.from_database(variable)


@router.post("/{variable_id}", response_model=VariableRead)
@require_scope("variable:update")
async def update_variable_by_id(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    variable_id: VariableID,
    params: VariableUpdate,
) -> VariableRead:
    service = VariablesService(session, role=role)
    try:
        variable = await service.get_variable(variable_id)
        updated = await service.update_variable(variable, params)
    except TracecatNotFoundError as exc:
        logger.error("Variable not found", variable_id=variable_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Variable does not exist"
        ) from exc
    except IntegrityError as exc:
        logger.info("Variable already exists", variable_id=variable_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Variable already exists"
        ) from exc
    return VariableRead.from_database(updated)


@router.delete("/{variable_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("variable:delete")
async def delete_variable_by_id(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    variable_id: VariableID,
) -> None:
    service = VariablesService(session, role=role)
    try:
        variable = await service.get_variable(variable_id)
        await service.delete_variable(variable)
    except TracecatNotFoundError as exc:
        logger.info("Variable not found", variable_id=variable_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Variable does not exist"
        ) from exc
