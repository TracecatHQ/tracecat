"""Action gateway FastAPI application."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from pydantic_core import to_jsonable_python

from tracecat.contexts import ctx_role
from tracecat.logger import logger

router = APIRouter(
    prefix="/internal",
    tags=["action-gateway"],
    include_in_schema=False,
)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Log action gateway requests without query strings."""
    started_at = time.perf_counter()
    response: Response | None = None
    try:
        response = await call_next(request)
        return response
    finally:
        status_code = response.status_code if response is not None else 500
        logger.info(
            "Action Gateway request",
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )


def validation_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle validation errors for action gateway-served internal routes."""
    errors = (
        to_jsonable_python(exc.errors(), fallback=str)
        if isinstance(exc, RequestValidationError)
        else str(exc)
    )
    logger.error(
        "Action Gateway validation error",
        method=request.method,
        path=request.url.path,
        errors=errors,
    )
    return ORJSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errors},
    )


def entitlement_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle entitlement errors for action gateway-served internal routes."""
    detail = getattr(exc, "detail", None)
    logger.warning(
        "Action Gateway entitlement required",
        path=request.url.path,
        detail=detail,
    )
    return ORJSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "type": "EntitlementRequired",
            "message": str(exc),
            "detail": detail,
        },
    )


def scope_denied_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle scope errors for action gateway-served internal routes."""
    required_scopes = getattr(exc, "required_scopes", ())
    missing_scopes = getattr(exc, "missing_scopes", ())
    logger.warning(
        "Action Gateway scope denied",
        required_scopes=required_scopes,
        missing_scopes=missing_scopes,
        path=request.url.path,
        role=ctx_role.get(),
    )
    return ORJSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "error": {
                "code": "insufficient_scope",
                "message": str(exc),
                "required_scopes": required_scopes,
                "missing_scopes": missing_scopes,
            }
        },
    )


def _include_internal_routers(app: FastAPI) -> None:
    """Mount the API's executor-facing internal routers onto the action gateway."""
    from tracecat.agent.internal_router import router as internal_agent_router
    from tracecat.agent.preset.internal_router import (
        router as internal_agent_preset_router,
    )
    from tracecat.agent.skill.internal_router import (
        router as internal_agent_skill_router,
    )
    from tracecat.cases.attachments.internal_router import (
        router as internal_case_attachments_router,
    )
    from tracecat.cases.internal_router import (
        comments_router as internal_comments_router,
    )
    from tracecat.cases.internal_router import router as internal_cases_router
    from tracecat.cases.rows.internal_router import router as internal_case_rows_router
    from tracecat.cases.tag_definitions.internal_router import (
        router as internal_case_tag_definitions_router,
    )
    from tracecat.cases.tags.internal_router import router as internal_case_tags_router
    from tracecat.deduplicate.internal_router import (
        router as internal_deduplicate_router,
    )
    from tracecat.tables.internal_router import router as internal_tables_router
    from tracecat.variables.internal_router import router as internal_variables_router
    from tracecat.workflow.executions.internal_router import (
        router as internal_workflows_router,
    )

    app.include_router(internal_agent_router)
    app.include_router(internal_agent_preset_router)
    app.include_router(internal_agent_skill_router)
    app.include_router(internal_case_attachments_router)
    app.include_router(internal_cases_router)
    app.include_router(internal_deduplicate_router)
    app.include_router(internal_case_rows_router)
    app.include_router(internal_comments_router)
    app.include_router(internal_case_tags_router)
    app.include_router(internal_case_tag_definitions_router)
    app.include_router(internal_tables_router)
    app.include_router(internal_variables_router)
    app.include_router(internal_workflows_router)


def _add_exception_handlers(app: FastAPI) -> None:
    """Install API-compatible exception handlers on the action gateway."""
    from fastapi import HTTPException

    from tracecat.api.common import (
        generic_exception_handler,
        http_exception_handler,
        tracecat_exception_handler,
    )
    from tracecat.exceptions import (
        EntitlementRequired,
        ScopeDeniedError,
        TracecatException,
    )

    app.add_exception_handler(Exception, generic_exception_handler)
    app.add_exception_handler(TracecatException, tracecat_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(EntitlementRequired, entitlement_exception_handler)
    app.add_exception_handler(ScopeDeniedError, scope_denied_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)


def create_app(**kwargs) -> FastAPI:
    """Create the executor-local action gateway app."""
    app = FastAPI(
        title="Tracecat Action Gateway",
        version="0",
        default_response_class=ORJSONResponse,
        **kwargs,
    )

    app.middleware("http")(request_logging_middleware)
    app.include_router(router)
    _include_internal_routers(app)
    _add_exception_handlers(app)
    return app
