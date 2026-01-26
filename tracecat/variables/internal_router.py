"""Internal router for variable operations (SDK/UDF use)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.variables.schemas import VariableReadMinimal
from tracecat.variables.service import VariablesService

router = APIRouter(
    prefix="/internal/variables", tags=["internal-variables"], include_in_schema=False
)


@router.get("/{variable_name}", response_model=VariableReadMinimal)
async def get_variable_by_name(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    variable_name: str,
    environment: str | None = Query(default=None),
) -> VariableReadMinimal:
    """Get a variable by name.

    Args:
        variable_name: The variable name to look up.
        environment: Optional environment filter.

    Returns:
        Variable metadata including id, name, description, values, and environment.
    """
    service = VariablesService(session, role=role)
    try:
        variable = await service.get_variable_by_name(
            variable_name, environment=environment
        )
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return VariableReadMinimal(
        id=variable.id,
        name=variable.name,
        description=variable.description,
        values=variable.values,
        environment=variable.environment,
    )


@router.get("/{variable_name}/value")
async def get_variable_value(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    variable_name: str,
    key: str = Query(...),
    environment: str | None = Query(default=None),
) -> Any:
    """Get a specific key's value from a variable.

    Args:
        variable_name: The variable name.
        key: The key to retrieve from the variable's values.
        environment: Optional environment filter.

    Returns:
        The value for the specified key, or null if not found.
    """
    service = VariablesService(session, role=role)
    return await service.get_variable_value(variable_name, key, environment=environment)
