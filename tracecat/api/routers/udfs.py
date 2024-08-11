from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config, validation
from tracecat.auth.credentials import authenticate_user_for_workspace
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import UDFSpec
from tracecat.logging import logger
from tracecat.types.api import UDFArgsValidationResponse
from tracecat.types.auth import Role

router = APIRouter(prefix="/udfs")


@router.get("", tags=["udfs"])
async def list_udfs(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    limit: int | None = None,
    ns: list[str] | None = Query(None),
    session: AsyncSession = Depends(get_async_session),
) -> list[UDFSpec]:
    """List all user-defined function specifications for a user."""
    statement = select(UDFSpec).where(
        or_(
            UDFSpec.owner_id == config.TRACECAT__DEFAULT_ORG_ID,
            UDFSpec.owner_id == role.workspace_id,
        )
    )
    if ns:
        ns_conds = [UDFSpec.key.startswith(n) for n in ns]
        statement = statement.where(or_(*ns_conds))
    if limit:
        statement = statement.limit(limit)
    result = await session.exec(statement)
    udfs = result.all()
    return udfs


@router.get("/{udf_key}", tags=["udfs"])
async def get_udf(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    udf_key: str,
    namespace: str = Query(None),
    session: AsyncSession = Depends(get_async_session),
) -> UDFSpec:
    """Get a user-defined function specification."""
    statement = select(UDFSpec).where(
        or_(
            UDFSpec.owner_id == config.TRACECAT__DEFAULT_ORG_ID,
            UDFSpec.owner_id == role.workspace_id,
        ),
        UDFSpec.key == udf_key,
    )
    if namespace:
        statement = statement.where(UDFSpec.namespace == namespace)
    result = await session.exec(statement)
    udf = result.one_or_none()
    if udf is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="udf not found"
        )
    return udf


@router.post("/{udf_key}", tags=["udfs"])
async def create_udf(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    udf_key: str,
    session: AsyncSession = Depends(get_async_session),
) -> UDFSpec:
    """Create a user-defined function specification."""
    _, platform, name = udf_key.split(".")
    statement = select(UDFSpec).where(
        UDFSpec.owner_id == role.workspace_id,
        UDFSpec.platform == platform,
        UDFSpec.name == name,
    )
    result = await session.exec(statement)
    udf = result.one_or_none()
    if udf is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="udf not found"
        )
    return udf


@router.post("/{udf_key}/validate", tags=["udfs"])
async def validate_udf_args(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    udf_key: str,
    args: dict[str, Any],
) -> UDFArgsValidationResponse:
    """Validate user-defined function's arguments."""
    try:
        result = validation.vadliate_udf_args(udf_key, args)
        if result.status == "error":
            logger.error(
                "Error validating UDF args",
                message=result.msg,
                details=result.detail,
            )
        return UDFArgsValidationResponse.from_validation_result(result)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"UDF {udf_key!r} not found"
        ) from e
    except Exception as e:
        logger.opt(exception=e).error("Error validating UDF args")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unexpected error validating UDF args",
        ) from e
