from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from fastapi.routing import APIRoute
from httpx_oauth.clients.google import GoogleOAuth2

from tracecat import config
from tracecat.api.routers.actions import router as actions_router
from tracecat.api.routers.cases.actions import router as case_actions_router
from tracecat.api.routers.cases.contexts import router as case_contexts_router
from tracecat.api.routers.cases.management import router as cases_router
from tracecat.api.routers.public.callbacks import router as callback_router
from tracecat.api.routers.public.webhooks import router as webhook_router
from tracecat.api.routers.schedules import router as schedules_router
from tracecat.api.routers.secrets import router as secrets_router
from tracecat.api.routers.udfs import router as udfs_router
from tracecat.api.routers.users import router as users_router
from tracecat.api.routers.validation import router as validation_router
from tracecat.auth.constants import AuthType
from tracecat.auth.schemas import UserCreate, UserRead, UserUpdate
from tracecat.auth.users import auth_backend, fastapi_users
from tracecat.contexts import ctx_role
from tracecat.db.engine import initialize_db
from tracecat.logging import logger
from tracecat.middleware import RequestLoggingMiddleware
from tracecat.types.exceptions import TracecatException
from tracecat.workflow.executions.router import router as workflow_executions_router
from tracecat.workflow.management.router import router as workflow_management_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_db()
    yield


def custom_generate_unique_id(route: APIRoute):
    logger.trace("Generating unique ID for route", tags=route.tags, name=route.name)
    if route.tags:
        return f"{route.tags[0]}-{route.name}"
    return route.name


# Catch-all exception handler to prevent stack traces from leaking
def generic_exception_handler(request: Request, exc: Exception):
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


def tracecat_exception_handler(request: Request, exc: TracecatException):
    """Generic exception handler for Tracecat exceptions.

    We can customize exceptions to expose only what should be user facing.
    """
    msg = str(exc)
    logger.error(
        msg,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"type": type(exc).__name__, "message": msg, "detail": exc.detail},
    )


def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Improves visiblity of 422 errors."""
    exc_str = f"{exc}".replace("\n", " ").replace("   ", " ")
    logger.error(f"{request}: {exc_str}")
    return ORJSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=exc_str
    )


def create_app(**kwargs) -> FastAPI:
    global logger
    if config.TRACECAT__ALLOW_ORIGINS is not None:
        allow_origins = config.TRACECAT__ALLOW_ORIGINS.split(",")
    else:
        allow_origins = ["*"]
    app = FastAPI(
        title="Tracecat API",
        description=(
            "Tracecat is the open source Tines / Splunk SOAR alternative."
            " You can operate Tracecat in headless mode by using the API to create, manage, and run workflows."
        ),
        summary="Tracecat API",
        version="1",
        terms_of_service="https://docs.google.com/document/d/e/2PACX-1vQvDe3SoVAPoQc51MgfGCP71IqFYX_rMVEde8zC4qmBCec5f8PLKQRdxa6tsUABT8gWAR9J-EVs2CrQ/pub",
        contact={"name": "Tracecat Founders", "email": "founders@tracecat.com"},
        license_info={
            "name": "AGPL-3.0",
            "url": "https://www.gnu.org/licenses/agpl-3.0.html",
        },
        openapi_tags=[
            {"name": "public", "description": "Public facing endpoints"},
            {"name": "workflows", "description": "Workflow management"},
            {"name": "actions", "description": "Action management"},
            {"name": "triggers", "description": "Workflow triggers"},
            {"name": "secrets", "description": "Secret management"},
            {"name": "udfs", "description": "User-defined functions"},
            {"name": "events", "description": "Event management"},
            {"name": "cases", "description": "Case management"},
        ],
        generate_unique_id_function=custom_generate_unique_id,
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        root_path=config.TRACECAT__API_ROOT_PATH,
        **kwargs,
    )
    app.logger = logger

    # Routers
    app.include_router(webhook_router)
    app.include_router(callback_router)
    app.include_router(workflow_management_router)
    app.include_router(workflow_executions_router)
    app.include_router(actions_router)
    app.include_router(udfs_router)
    app.include_router(cases_router)
    app.include_router(case_actions_router)
    app.include_router(case_contexts_router)
    app.include_router(secrets_router)
    app.include_router(schedules_router)
    app.include_router(users_router)
    app.include_router(validation_router)

    if config.TRACECAT__AUTH_TYPE == AuthType.DISABLED:
        # Server logs this during auth setup verification step
        pass

    elif config.TRACECAT__AUTH_TYPE == AuthType.BASIC:
        app.include_router(
            fastapi_users.get_auth_router(auth_backend),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            fastapi_users.get_register_router(UserRead, UserCreate),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            fastapi_users.get_reset_password_router(),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            fastapi_users.get_verify_router(UserRead),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            fastapi_users.get_users_router(UserRead, UserUpdate),
            prefix="/users",
            tags=["users"],
        )

    elif config.TRACECAT__AUTH_TYPE == AuthType.GOOGLE_OAUTH:
        oauth_client = GoogleOAuth2(
            client_id=config.OAUTH_CLIENT_ID, client_secret=config.OAUTH_CLIENT_SECRET
        )
        app.include_router(
            fastapi_users.get_oauth_router(
                oauth_client,
                auth_backend,
                config.USER_AUTH_SECRET,
                # XXX(security): See https://fastapi-users.github.io/fastapi-users/13.0/configuration/oauth/#existing-account-association
                associate_by_email=True,
                is_verified_by_default=True,
                # Points the user back to the login page
                redirect_url=f"{config.TRACECAT__API_URL}/auth/oauth/callback",
            ),
            prefix="/auth/oauth",
            tags=["auth"],
        )
        # Need basic auth router for `logout` endpoint
        app.include_router(
            fastapi_users.get_logout_router(auth_backend),
            prefix="/auth",
            tags=["auth"],
        )

    # Exception handlers
    app.add_exception_handler(Exception, generic_exception_handler)
    app.add_exception_handler(TracecatException, tracecat_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # Middleware
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger.info("App started", env=config.TRACECAT__APP_ENV, origins=allow_origins)
    return app


app = create_app()


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the API."}


@app.get("/health", tags=["public"])
def check_health() -> dict[str, str]:
    return {"message": "Hello world. I am the API. This is the health endpoint."}
