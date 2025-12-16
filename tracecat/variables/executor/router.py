from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import VariableID
from tracecat.variables.schemas import (
    VariableRead,
    VariableReadMinimal,
    VariableSearch,
)
from tracecat.variables.service import VariablesService

router = APIRouter(
    prefix="/internal/variables", tags=["internal-variables"], include_in_schema=False
)


@router.get("/search", response_model=list[VariableRead])
async def executor_search_variables(
    *,
    role: ExecutorWorkspaceRole,
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
async def executor_list_variables(
    *,
    role: ExecutorWorkspaceRole,
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
async def executor_get_variable_by_name(
    *,
    role: ExecutorWorkspaceRole,
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
