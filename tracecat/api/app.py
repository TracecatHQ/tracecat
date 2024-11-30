from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from fastapi.routing import APIRoute
from httpx_oauth.clients.google import GoogleOAuth2
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
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
from tracecat.logger import logger
from tracecat.middleware import RequestLoggingMiddleware
from tracecat.registry.actions.router import router as registry_actions_router
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import (
    CUSTOM_REPOSITORY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.registry.repositories.router import router as registry_repos_router
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.repository import safe_url
from tracecat.secrets.router import router as secrets_router
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatException
from tracecat.webhooks.router import router as webhook_router
from tracecat.workflow.actions.router import router as workflow_actions_router
from tracecat.workflow.executions.router import router as workflow_executions_router
from tracecat.workflow.management.router import router as workflow_management_router
from tracecat.workflow.schedules.router import router as schedules_router
from tracecat.workspaces.router import router as workspaces_router
from tracecat.workspaces.service import WorkspaceService


@asynccontextmanager
async def lifespan(app: FastAPI):
    admin_role = Role(
        type="service",
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-api",
    )
    async with get_async_session_context_manager() as session:
        await setup_defaults(session, admin_role)
        await setup_registry(session, admin_role)
    await setup_oss_models()
    yield


async def setup_registry(session: AsyncSession, admin_role: Role):
    logger.info("Setting up base registry repository")
    repos_service = RegistryReposService(session, role=admin_role)
    # Setup Tracecat base repository
    base_origin = DEFAULT_REGISTRY_ORIGIN
    # Check if the base registry repository already exists
    # NOTE: Should we sync the base repo every time?
    if await repos_service.get_repository(base_origin) is None:
        base_repo = await repos_service.create_repository(
            RegistryRepositoryCreate(origin=base_origin)
        )
        logger.info("Created base registry repository", origin=base_origin)
        actions_service = RegistryActionsService(session, role=admin_role)
        await actions_service.sync_actions_from_repository(base_repo)
    else:
        logger.info("Base registry repository already exists", origin=base_origin)

    # Setup custom repository
    custom_origin = CUSTOM_REPOSITORY_ORIGIN
    if await repos_service.get_repository(custom_origin) is None:
        await repos_service.create_repository(
            RegistryRepositoryCreate(origin=custom_origin)
        )
        logger.info("Created custom repository", origin=custom_origin)
    else:
        logger.info("Custom repository already exists", origin=custom_origin)

    # Setup custom remote repository
    if (remote_url := config.TRACECAT__REMOTE_REPOSITORY_URL) is not None:
        parsed_url = urlparse(remote_url)
        logger.info("Setting up remote registry repository", url=parsed_url)
        # Create it if it doesn't exist

        cleaned_url = safe_url(remote_url)
        if await repos_service.get_repository(cleaned_url) is None:
            await repos_service.create_repository(
                RegistryRepositoryCreate(origin=cleaned_url)
            )
            logger.info("Created remote registry repository", url=cleaned_url)
        else:
            logger.info("Remote registry repository already exists", url=cleaned_url)
        # Load remote repository
    else:
        logger.info("Remote registry repository not set, skipping")

    repos = await repos_service.list_repositories()
    logger.info(
        "Found registry repositories",
        n=len(repos),
        repos=[repo.origin for repo in repos],
    )


async def setup_defaults(session: AsyncSession, admin_role: Role):
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


async def setup_oss_models():
    if not (preload_models := config.TRACECAT__PRELOAD_OSS_MODELS):
        return
    from tracecat.llm import preload_ollama_models

    logger.info(
        f"Preloading {len(preload_models)} models",
        models=preload_models,
    )
    await preload_ollama_models(preload_models)
    logger.info("Preloaded models", models=preload_models)


def custom_generate_unique_id(route: APIRoute):
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
    app.include_router(workspaces_router)
    app.include_router(workflow_management_router)
    app.include_router(workflow_executions_router)
    app.include_router(workflow_actions_router)
    app.include_router(secrets_router)
    app.include_router(schedules_router)
    app.include_router(users_router)
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
        # Need basic auth router for `logout` endpoint
        app.include_router(
            fastapi_users.get_logout_router(auth_backend),
            prefix="/auth",
            tags=["auth"],
        )
    if AuthType.SAML in config.TRACECAT__AUTH_TYPES:
        from tracecat.auth.saml import router as saml_router

        logger.info("SAML auth type enabled")
        app.include_router(saml_router)

    # Development endpoints
    if config.TRACECAT__APP_ENV == "development":
        # XXX(security): This is a security risk. Do not run this in production.
        from tracecat.testing.registry import router as registry_testing_router

        app.include_router(registry_testing_router)
        logger.warning("Development endpoints enabled. Do not run this in production.")

    # Exception handlers
    app.add_exception_handler(Exception, generic_exception_handler)
    app.add_exception_handler(TracecatException, tracecat_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(
        FastAPIUsersException, fastapi_users_auth_exception_handler
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


@app.get("/health", tags=["public"])
def check_health() -> dict[str, str]:
    return {"message": "Hello world. I am the API. This is the health endpoint."}
