from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from httpx_oauth.clients.google import GoogleOAuth2
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.api.common import (
    bootstrap_role,
    custom_generate_unique_id,
    generic_exception_handler,
    setup_registry,
    tracecat_exception_handler,
)
from tracecat.auth.constants import AuthType
from tracecat.auth.models import UserCreate, UserRead, UserUpdate
from tracecat.auth.router import router as users_router
from tracecat.auth.users import (
    FastAPIUsersException,
    InvalidDomainException,
    auth_backend,
    fastapi_users,
)
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.editor.router import router as editor_router
from tracecat.logger import logger
from tracecat.middleware import RequestLoggingMiddleware
from tracecat.organization.router import router as org_router
from tracecat.registry.actions.router import router as registry_actions_router
from tracecat.registry.repositories.router import router as registry_repos_router
from tracecat.secrets.router import router as secrets_router
from tracecat.tags.router import router as tags_router
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatException
from tracecat.webhooks.router import router as webhook_router
from tracecat.workflow.actions.router import router as workflow_actions_router
from tracecat.workflow.executions.router import router as workflow_executions_router
from tracecat.workflow.management.router import router as workflow_management_router
from tracecat.workflow.schedules.router import router as schedules_router
from tracecat.workflow.tags.router import router as workflow_tags_router
from tracecat.workspaces.router import router as workspaces_router
from tracecat.workspaces.service import WorkspaceService


@asynccontextmanager
async def lifespan(app: FastAPI):
    role = bootstrap_role()
    async with get_async_session_context_manager() as session:
        await setup_workspace_defaults(session, role)
        await setup_registry(session, role)
    yield


async def setup_workspace_defaults(session: AsyncSession, admin_role: Role):
    ws_service = WorkspaceService(session, role=admin_role)
    workspaces = await ws_service.admin_list_workspaces()
    n_workspaces = len(workspaces)
    logger.info(f"{n_workspaces} workspaces found")
    if n_workspaces == 0:
        # Create default workspace if there are no workspaces
        try:
            default_workspace = await ws_service.create_workspace("Default Workspace")
            logger.info("Default workspace created", workspace=default_workspace)
        except IntegrityError:
            logger.info("Default workspace already exists, skipping")


# Catch-all exception handler to prevent stack traces from leaking
def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Improves visiblity of 422 errors."""
    exc_str = f"{exc}".replace("\n", " ").replace("   ", " ")
    logger.error(f"{request}: {exc_str}")
    return ORJSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=exc_str
    )


def fastapi_users_auth_exception_handler(request: Request, exc: FastAPIUsersException):
    msg = str(exc)
    logger.warning(
        "Handling FastAPI Users exception",
        msg=msg,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
    )
    match exc:
        case InvalidDomainException():
            status_code = status.HTTP_400_BAD_REQUEST
        case _:
            status_code = status.HTTP_401_UNAUTHORIZED
    return ORJSONResponse(status_code=status_code, content={"detail": msg})


def create_app(**kwargs) -> FastAPI:
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
        ],
        generate_unique_id_function=custom_generate_unique_id,
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        root_path=config.TRACECAT__API_ROOT_PATH,
        **kwargs,
    )
    app.logger = logger  # type: ignore

    # Routers
    app.include_router(webhook_router)
    app.include_router(workspaces_router)
    app.include_router(workflow_management_router)
    app.include_router(workflow_executions_router)
    app.include_router(workflow_actions_router)
    app.include_router(workflow_tags_router)
    app.include_router(secrets_router)
    app.include_router(schedules_router)
    app.include_router(tags_router)
    app.include_router(users_router)
    app.include_router(org_router)
    app.include_router(editor_router)
    app.include_router(registry_repos_router)
    app.include_router(registry_actions_router)
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )

    if AuthType.BASIC in config.TRACECAT__AUTH_TYPES:
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

    if AuthType.GOOGLE_OAUTH in config.TRACECAT__AUTH_TYPES:
        oauth_client = GoogleOAuth2(
            client_id=config.OAUTH_CLIENT_ID, client_secret=config.OAUTH_CLIENT_SECRET
        )
        # This is the frontend URL that the user will be redirected to after authenticating
        redirect_url = f"{config.TRACECAT__PUBLIC_APP_URL}/auth/oauth/callback"
        logger.info("OAuth redirect URL", url=redirect_url)
        app.include_router(
            fastapi_users.get_oauth_router(
                oauth_client,
                auth_backend,
                config.USER_AUTH_SECRET,
                # XXX(security): See https://fastapi-users.github.io/fastapi-users/13.0/configuration/oauth/#existing-account-association
                associate_by_email=True,
                is_verified_by_default=True,
                # Points the user back to the login page
                redirect_url=redirect_url,
            ),
            prefix="/auth/oauth",
            tags=["auth"],
        )

    if AuthType.SAML in config.TRACECAT__AUTH_TYPES:
        from tracecat.auth.saml import router as saml_router

        logger.info("SAML auth type enabled")
        app.include_router(saml_router)

    if AuthType.BASIC not in config.TRACECAT__AUTH_TYPES:
        # Need basic auth router for `logout` endpoint
        app.include_router(
            fastapi_users.get_logout_router(auth_backend),
            prefix="/auth",
            tags=["auth"],
        )

    # Exception handlers
    app.add_exception_handler(Exception, generic_exception_handler)
    app.add_exception_handler(TracecatException, tracecat_exception_handler)  # type: ignore  # type: ignore
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore
    app.add_exception_handler(
        FastAPIUsersException,
        fastapi_users_auth_exception_handler,  # type: ignore
    )

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
        "App started",
        env=config.TRACECAT__APP_ENV,
        origins=allow_origins,
        auth_types=config.TRACECAT__AUTH_TYPES,
    )
    return app


app = create_app()


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the API."}


@app.get("/info", include_in_schema=False)
def info() -> dict[str, str]:
    return {"public_app_url": config.TRACECAT__PUBLIC_APP_URL}


@app.get("/health", tags=["public"])
def check_health() -> dict[str, str]:
    return {"message": "Hello world. I am the API. This is the health endpoint."}
