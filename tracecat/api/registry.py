from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from tracecat import config
from tracecat.api.common import (
    custom_generate_unique_id,
    generic_exception_handler,
    setup_oss_models,
    setup_registry,
    tracecat_exception_handler,
)
from tracecat.db.engine import get_async_session_context_manager
from tracecat.logger import logger
from tracecat.middleware import RequestLoggingMiddleware
from tracecat.registry.actions.router import router as registry_actions_router
from tracecat.registry.executor import get_executor
from tracecat.registry.repositories.router import router as registry_repos_router
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatException


@asynccontextmanager
async def lifespan(app: FastAPI):
    admin_role = Role(
        type="service",
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-registry",
    )
    async with get_async_session_context_manager() as session:
        await setup_registry(session, admin_role)
    await setup_oss_models()
    try:
        executor = get_executor()
        yield
    finally:
        executor.shutdown()


def create_app(**kwargs) -> FastAPI:
    if config.TRACECAT__ALLOW_ORIGINS is not None:
        allow_origins = config.TRACECAT__ALLOW_ORIGINS.split(",")
    else:
        allow_origins = ["*"]
    app = FastAPI(
        title="Tracecat Registry",
        description="Registry action executor.",
        summary="Tracecat Registry",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        generate_unique_id_function=custom_generate_unique_id,
        root_path="/api/registry",
        **kwargs,
    )
    app.logger = logger  # type: ignore

    # Routers
    app.include_router(registry_repos_router)
    app.include_router(registry_actions_router)

    # Exception handlers
    app.add_exception_handler(Exception, generic_exception_handler)
    app.add_exception_handler(TracecatException, tracecat_exception_handler)  # type: ignore

    # Middleware
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger.info(
        "Registry service started",
        env=config.TRACECAT__APP_ENV,
        origins=allow_origins,
        auth_types=config.TRACECAT__AUTH_TYPES,
    )

    return app


app = create_app()


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the registry."}
