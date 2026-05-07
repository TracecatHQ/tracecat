from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn

from fastapi import HTTPException, Request, Response, status
from fastapi.exception_handlers import http_exception_handler as default_http_handler
from fastapi.responses import ORJSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
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

from tracecat.auth.types import Role
from tracecat.config import TEMPORAL__CLUSTER_NAMESPACE
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import TracecatException
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.workflow.executions.enums import TemporalSearchAttr


@dataclass(frozen=True)
class KnownDatabaseError:
    status_code: int
    code: str
    message: str


_GENERIC_DATABASE_MESSAGE_ERRORS = {
    "expected str, got list": KnownDatabaseError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="DATABASE_VALUE_TYPE_MISMATCH",
        message="A field received a value with an incompatible type.",
    )
}


def _exception_text(exc: Exception) -> str:
    orig = getattr(exc, "orig", None)
    if orig is None:
        return str(exc)
    return f"{exc} {orig}"


def known_database_error(
    exc: Exception,
    *,
    constraint_errors: Mapping[str, KnownDatabaseError] | None = None,
    message_errors: Mapping[str, KnownDatabaseError] | None = None,
) -> KnownDatabaseError | None:
    text = _exception_text(exc)
    if isinstance(exc, IntegrityError) and constraint_errors is not None:
        for constraint, error in constraint_errors.items():
            if constraint in text:
                return error
    if isinstance(exc, DBAPIError) and message_errors is not None:
        for message, error in message_errors.items():
            if message in text:
                return error
    return None


def known_database_http_exception(
    exc: Exception,
    *,
    constraint_errors: Mapping[str, KnownDatabaseError] | None = None,
    message_errors: Mapping[str, KnownDatabaseError] | None = None,
) -> HTTPException | None:
    error = known_database_error(
        exc, constraint_errors=constraint_errors, message_errors=message_errors
    )
    if error is None:
        return None
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    )


async def raise_known_database_http_exception(
    session: AsyncSession,
    exc: Exception,
    *,
    constraint_errors: Mapping[str, KnownDatabaseError] | None = None,
    message_errors: Mapping[str, KnownDatabaseError] | None = None,
) -> NoReturn:
    await session.rollback()
    if http_exc := known_database_http_exception(
        exc, constraint_errors=constraint_errors, message_errors=message_errors
    ):
        raise http_exc from exc
    raise exc


def _known_database_error_response(request: Request, exc: Exception) -> Response | None:
    error = known_database_error(exc, message_errors=_GENERIC_DATABASE_MESSAGE_ERRORS)
    if error is None:
        return None

    logger.warning(
        "Known database error",
        code=error.code,
        status_code=error.status_code,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
    )
    return ORJSONResponse(
        status_code=error.status_code,
        content={"detail": {"code": error.code, "message": error.message}},
    )


def generic_exception_handler(request: Request, exc: Exception) -> Response:
    if response := _known_database_error_response(request, exc):
        return response

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


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    """Log HTTP exceptions with tenant context for observability."""
    http_exc = exc if isinstance(exc, HTTPException) else HTTPException(500, str(exc))
    role = ctx_role.get()
    log_method = logger.warning if http_exc.status_code < 500 else logger.error
    log_method(
        "HTTP error",
        status_code=http_exc.status_code,
        detail=http_exc.detail,
        path=request.url.path,
        method=request.method,
        role=role,
    )
    return await default_http_handler(request, http_exc)


def bootstrap_role(organization_id: OrganizationID | None = None) -> Role:
    """Role to bootstrap Tracecat services.

    Args:
        organization_id: Optional organization ID to scope the bootstrap role to.
            If None, creates a role without org scope (for platform-level operations).

    Returns:
        Role: A service role with platform superuser privileges for the specified organization.
    """
    return Role(
        type="service",
        service_id="tracecat-bootstrap",
        organization_id=organization_id,
        is_platform_superuser=True,
        scopes=frozenset({"*"}),
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
        TemporalSearchAttr.CORRELATION_ID.value: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
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
                    TemporalSearchAttr.CORRELATION_ID.value,
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
                TemporalSearchAttr.CORRELATION_ID.value,
                TemporalSearchAttr.EXECUTION_TYPE.value,
            ],
        )
