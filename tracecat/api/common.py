from fastapi import Request, Response, status
from fastapi.responses import ORJSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    RemoveSearchAttributesRequest,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.auth.types import AccessLevel, Role
from tracecat.config import TEMPORAL__CLUSTER_NAMESPACE
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import TracecatException
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.workflow.executions.enums import TemporalSearchAttr


def generic_exception_handler(request: Request, exc: Exception) -> Response:
    logger.error(
        "Unexpected error",
        exc=exc,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "An unexpected error occurred. Please try again later."},
    )


def bootstrap_role(organization_id: OrganizationID | None = None) -> Role:
    """Role to bootstrap Tracecat services.

    Args:
        organization_id: Optional organization ID to scope the bootstrap role to.
            If None, creates a role without org scope (for platform-level operations).

    Returns:
        Role: A service role with ADMIN access for the specified organization.
    """
    return Role(
        type="service",
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-bootstrap",
        organization_id=organization_id,
    )


async def get_default_organization_id(session: AsyncSession) -> OrganizationID:
    """Get the default (first) organization ID.

    This is used by auth modules that need to read platform-level settings
    before user authentication provides an org context.

    Args:
        session: Database session.

    Returns:
        OrganizationID: The ID of the first organization.

    Raises:
        ValueError: If no organizations exist.
    """
    # Import here to avoid circular imports
    from tracecat.db.models import Organization

    result = await session.execute(
        select(Organization).order_by(Organization.created_at).limit(1)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise ValueError("No organizations exist. Run bootstrap first.")
    return org.id


def tracecat_exception_handler(request: Request, exc: Exception) -> Response:
    """Generic exception handler for Tracecat exceptions.

    We can customize exceptions to expose only what should be user facing.
    """
    tracecat_exc = (
        exc if isinstance(exc, TracecatException) else TracecatException(str(exc))
    )
    msg = str(tracecat_exc)
    logger.error(
        msg,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
        detail=tracecat_exc.detail,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "type": type(exc).__name__,
            "message": msg,
            "detail": tracecat_exc.detail,
        },
    )


def custom_generate_unique_id(route: APIRoute):
    if route.tags:
        return f"{route.tags[0]}-{route.name}"
    return route.name


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=5, min=5, max=20),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def add_temporal_search_attributes():
    """Add search attributes to the Temporal cluster.

    This is an idempotent operation and is safe to run multiple times.
    """
    client = await get_temporal_client()
    namespace = TEMPORAL__CLUSTER_NAMESPACE
    attrs = {
        TemporalSearchAttr.TRIGGER_TYPE.value: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        TemporalSearchAttr.TRIGGERED_BY_USER_ID.value: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        TemporalSearchAttr.WORKSPACE_ID.value: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        TemporalSearchAttr.ALIAS.value: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        TemporalSearchAttr.EXECUTION_TYPE.value: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    }
    try:
        await client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                search_attributes=attrs,
                namespace=namespace,
            )
        )
    except Exception as e:
        logger.error(
            "Error adding temporal search attributes",
            exc=e,
            namespace=namespace,
        )
    else:
        logger.info(
            "Temporal search attributes added",
            namespace=namespace,
            search_attributes=list(attrs.keys()),
        )


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=5, min=5, max=20),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def remove_temporal_search_attributes():
    """Remove search attributes from the Temporal cluster.

    This is an idempotent operation and is safe to run multiple times.
    """
    client = await get_temporal_client()
    namespace = TEMPORAL__CLUSTER_NAMESPACE
    try:
        await client.operator_service.remove_search_attributes(
            RemoveSearchAttributesRequest(
                search_attributes=[
                    TemporalSearchAttr.TRIGGER_TYPE.value,
                    TemporalSearchAttr.TRIGGERED_BY_USER_ID.value,
                    TemporalSearchAttr.WORKSPACE_ID.value,
                    TemporalSearchAttr.ALIAS.value,
                    TemporalSearchAttr.EXECUTION_TYPE.value,
                ],
                namespace=namespace,
            )
        )
    except Exception as e:
        logger.error(
            "Error removing temporal search attributes",
            exc=e,
            namespace=namespace,
        )
    else:
        logger.info(
            "Temporal search attributes removed",
            namespace=namespace,
            search_attributes=[
                TemporalSearchAttr.TRIGGER_TYPE.value,
                TemporalSearchAttr.TRIGGERED_BY_USER_ID.value,
                TemporalSearchAttr.WORKSPACE_ID.value,
                TemporalSearchAttr.ALIAS.value,
                TemporalSearchAttr.EXECUTION_TYPE.value,
            ],
        )
