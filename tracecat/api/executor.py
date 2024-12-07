from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from tracecat import config
from tracecat.api.common import (
    custom_generate_unique_id,
    generic_exception_handler,
    setup_oss_models,
    tracecat_exception_handler,
)
from tracecat.logger import logger
from tracecat.middleware import RequestLoggingMiddleware
from tracecat.registry.executor import get_executor, router
from tracecat.types.exceptions import TracecatException


@asynccontextmanager
async def lifespan(app: FastAPI):
    await setup_oss_models()
    executor = get_executor()
    try:
        yield
    finally:
        executor.shutdown()


def create_app(**kwargs) -> FastAPI:
    if config.TRACECAT__ALLOW_ORIGINS is not None:
        allow_origins = config.TRACECAT__ALLOW_ORIGINS.split(",")
    else:
        allow_origins = ["*"]
    app = FastAPI(
        title="Tracecat Executor",
        description="Action executor for Tracecat.",
        summary="Tracecat Executor",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        generate_unique_id_function=custom_generate_unique_id,
        root_path="/api/executor",
        **kwargs,
    )
    app.logger = logger  # type: ignore

    # Routers
    app.include_router(router)

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
        "Executor service started",
        env=config.TRACECAT__APP_ENV,
        origins=allow_origins,
        auth_types=config.TRACECAT__AUTH_TYPES,
    )

    return app


app = create_app()


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the executor."}
