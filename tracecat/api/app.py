import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from httpx_oauth.clients.google import GoogleOAuth2
from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.router import router as admin_router
from tracecat_ee.agent.approvals.router import router as approvals_router

from tracecat import __version__ as APP_VERSION
from tracecat import config
from tracecat.agent.preset.router import router as agent_preset_router
from tracecat.agent.router import router as agent_router
from tracecat.agent.session.router import router as agent_session_router
from tracecat.api.common import (
    add_temporal_search_attributes,
    bootstrap_role,
    custom_generate_unique_id,
    generic_exception_handler,
    tracecat_exception_handler,
)
from tracecat.auth.dependencies import require_auth_type_enabled
from tracecat.auth.enums import AuthType
from tracecat.auth.router import router as users_router
from tracecat.auth.saml import router as saml_router
from tracecat.auth.schemas import UserCreate, UserRead, UserUpdate
from tracecat.auth.types import Role
from tracecat.auth.users import (
    FastAPIUsersException,
    InvalidEmailException,
    auth_backend,
    fastapi_users,
)
from tracecat.cases.attachments.internal_router import (
    router as internal_case_attachments_router,
)
from tracecat.cases.attachments.router import router as case_attachments_router
from tracecat.cases.durations.router import router as case_durations_router
from tracecat.cases.internal_router import (
    comments_router as internal_comments_router,
)
from tracecat.cases.internal_router import (
    router as internal_cases_router,
)
from tracecat.cases.router import case_fields_router as case_fields_router
from tracecat.cases.router import cases_router as cases_router
from tracecat.cases.tag_definitions.internal_router import (
    router as internal_case_tag_definitions_router,
)
from tracecat.cases.tag_definitions.router import (
    router as case_tag_definitions_router,
)
from tracecat.cases.tags.internal_router import router as internal_case_tags_router
from tracecat.cases.tags.router import router as case_tags_router
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_session_context_manager
from tracecat.editor.router import router as editor_router
from tracecat.exceptions import TracecatException
from tracecat.feature_flags import (
    FeatureFlag,
    FlagLike,
    is_feature_enabled,
)
from tracecat.feature_flags.router import router as feature_flags_router
from tracecat.inbox.router import router as inbox_router
from tracecat.integrations.router import (
    integrations_router,
    mcp_router,
    providers_router,
)
from tracecat.logger import logger
from tracecat.middleware import (
    AuthorizationCacheMiddleware,
    RequestLoggingMiddleware,
)
from tracecat.middleware.security import SecurityHeadersMiddleware
from tracecat.organization.router import router as org_router
from tracecat.registry.actions.router import router as registry_actions_router
from tracecat.registry.common import reload_registry
from tracecat.registry.repositories.router import router as registry_repos_router
from tracecat.secrets.router import org_router as org_secrets_router
from tracecat.secrets.router import router as secrets_router
from tracecat.settings.router import router as org_settings_router
from tracecat.settings.service import SettingsService, get_setting_override
from tracecat.storage.blob import configure_bucket_lifecycle, ensure_bucket_exists
from tracecat.tables.internal_router import router as internal_tables_router
from tracecat.tables.router import router as tables_router
from tracecat.tags.router import router as tags_router
from tracecat.variables.router import router as variables_router
from tracecat.vcs.router import org_router as vcs_router
from tracecat.webhooks.router import router as webhook_router
from tracecat.workflow.actions.router import router as workflow_actions_router
from tracecat.workflow.executions.internal_router import (
    router as internal_workflows_router,
)
from tracecat.workflow.executions.router import router as workflow_executions_router
from tracecat.workflow.graph.router import router as workflow_graph_router
from tracecat.workflow.management.folders.router import (
    router as workflow_folders_router,
)
from tracecat.workflow.management.router import router as workflow_management_router
from tracecat.workflow.schedules.router import router as schedules_router
from tracecat.workflow.store.router import router as workflow_store_router
from tracecat.workflow.tags.router import router as workflow_tags_router
from tracecat.workspaces.router import router as workspaces_router
from tracecat.workspaces.service import WorkspaceService

# Global readiness state - set to True after lifespan startup completes
_app_ready = False


def is_app_ready() -> bool:
    """Check if the API has completed startup."""
    return _app_ready


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _app_ready

    # Temporal
    # Run in background to avoid blocking startup
    asyncio.create_task(add_temporal_search_attributes())
    logger.debug("Spawned lifespan task to add temporal search attributes")

    # Storage
    await ensure_bucket_exists(config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS)
    await ensure_bucket_exists(config.TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY)

    # Workflow bucket with lifecycle expiration
    await ensure_bucket_exists(config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW)
    if config.TRACECAT__WORKFLOW_ARTIFACT_RETENTION_DAYS > 0:
        await configure_bucket_lifecycle(
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW,
            expiration_days=config.TRACECAT__WORKFLOW_ARTIFACT_RETENTION_DAYS,
        )

    # App
    role = bootstrap_role()
    async with get_async_session_context_manager() as session:
        # Org
        await setup_org_settings(session, role)
        try:
            await reload_registry(session, role)
        except Exception as e:
            logger.warning("Error reloading registry", error=e)
        await setup_workspace_defaults(session, role)
    logger.info(
        "Feature flags", feature_flags=[f.value for f in config.TRACECAT__FEATURE_FLAGS]
    )

    # Mark app as ready after all startup tasks complete
    _app_ready = True
    logger.info("API startup complete, marking as ready")

    yield

    # Mark app as not ready during shutdown
    _app_ready = False


async def setup_org_settings(session: AsyncSession, admin_role: Role):
    settings_service = SettingsService(session, role=admin_role)
    await settings_service.init_default_settings()


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
    errors = exc.errors()
    ser_errors = to_jsonable_python(errors, fallback=str)
    logger.error(
        "API Model Validation error",
        method=request.method,
        path=request.url.path,
        errors=ser_errors,
    )
    return ORJSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": ser_errors},
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
        case InvalidEmailException():
            status_code = status.HTTP_400_BAD_REQUEST
        case _:
            status_code = status.HTTP_401_UNAUTHORIZED
    return ORJSONResponse(status_code=status_code, content={"detail": msg})


def feature_flag_dep(flag: FlagLike) -> Callable[..., None]:
    """Check if a feature flag is enabled."""

    def _is_feature_enabled() -> None:
        if not is_feature_enabled(flag):
            logger.debug("Feature flag is not enabled", flag=flag)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Feature not enabled"
            )
        logger.debug("Feature flag is enabled", flag=flag)

    return _is_feature_enabled


def create_app(**kwargs) -> FastAPI:
    if config.TRACECAT__ALLOW_ORIGINS is not None:
        allow_origins = config.TRACECAT__ALLOW_ORIGINS.split(",")
    else:
        allow_origins = ["*"]
    app = FastAPI(
        title="Tracecat API",
        description=("Tracecat is the open source automation platform for enterprise."),
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
            {
                "name": "variables",
                "description": "Workspace variable management",
            },
        ],
        servers=[{"url": config.TRACECAT__API_ROOT_PATH}],
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
    app.include_router(workflow_graph_router)
    app.include_router(workflow_executions_router)
    app.include_router(workflow_actions_router)
    app.include_router(workflow_tags_router)
    app.include_router(workflow_store_router)
    app.include_router(secrets_router)
    app.include_router(variables_router)
    app.include_router(schedules_router)
    app.include_router(tags_router)
    app.include_router(users_router)
    app.include_router(org_router)
    app.include_router(agent_router)
    app.include_router(
        agent_preset_router,
        dependencies=[Depends(feature_flag_dep(FeatureFlag.AGENT_PRESETS))],
    )
    app.include_router(agent_session_router)
    app.include_router(approvals_router)
    app.include_router(admin_router)
    app.include_router(inbox_router)
    app.include_router(editor_router)
    app.include_router(registry_repos_router)
    app.include_router(registry_actions_router)
    app.include_router(org_settings_router)
    app.include_router(org_secrets_router)
    app.include_router(tables_router)
    app.include_router(cases_router)
    app.include_router(case_fields_router)
    app.include_router(case_tags_router)
    app.include_router(case_tag_definitions_router)
    app.include_router(case_attachments_router)
    app.include_router(
        case_durations_router,
        dependencies=[Depends(feature_flag_dep(FeatureFlag.CASE_DURATIONS))],
    )
    app.include_router(workflow_folders_router)
    app.include_router(integrations_router)
    app.include_router(providers_router)
    app.include_router(mcp_router)
    app.include_router(feature_flags_router)
    app.include_router(
        vcs_router,
        dependencies=[Depends(feature_flag_dep(FeatureFlag.GIT_SYNC))],
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )
    # Internal routers
    app.include_router(internal_case_attachments_router)
    app.include_router(internal_cases_router)
    app.include_router(internal_comments_router)
    app.include_router(internal_case_tags_router)
    app.include_router(internal_case_tag_definitions_router)
    app.include_router(internal_tables_router)
    app.include_router(internal_workflows_router)

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
        dependencies=[require_auth_type_enabled(AuthType.GOOGLE_OAUTH)],
    )
    app.include_router(
        saml_router,
        dependencies=[require_auth_type_enabled(AuthType.SAML)],
    )

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
    # Add authorization cache middleware first so it's available for all requests
    app.add_middleware(AuthorizationCacheMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    if config.TRACECAT__APP_ENV != "development":
        app.add_middleware(SecurityHeadersMiddleware)
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


class HealthResponse(BaseModel):
    status: str


@app.get("/", include_in_schema=False)
def root() -> HealthResponse:
    return HealthResponse(status="ok")


class AppInfo(BaseModel):
    version: str
    public_app_url: str
    auth_allowed_types: list[AuthType]
    auth_basic_enabled: bool
    oauth_google_enabled: bool
    saml_enabled: bool


@app.get("/info", include_in_schema=False)
async def info(session: AsyncDBSession) -> AppInfo:
    """Non-sensitive information about the platform, for frontend configuration."""

    keys = {"auth_basic_enabled", "oauth_google_enabled", "saml_enabled"}

    service = SettingsService(session, role=bootstrap_role())
    settings = await service.list_org_settings(keys=keys)
    keyvalues = {s.key: service.get_value(s) for s in settings}
    for key in keys:
        keyvalues[key] = get_setting_override(key) or keyvalues[key]
    return AppInfo(
        version=APP_VERSION,
        public_app_url=config.TRACECAT__PUBLIC_APP_URL,
        auth_allowed_types=list(config.TRACECAT__AUTH_TYPES),
        auth_basic_enabled=keyvalues["auth_basic_enabled"],
        oauth_google_enabled=keyvalues["oauth_google_enabled"],
        saml_enabled=keyvalues["saml_enabled"],
    )


@app.get("/health", tags=["public"])
def check_health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/ready", tags=["public"])
def check_ready() -> HealthResponse:
    """Readiness check - returns 200 only after startup is complete.

    Use this endpoint for Docker healthchecks to ensure the API has finished
    initializing (including registry sync) before accepting traffic.
    """
    if not is_app_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API is not ready yet",
        )
    return HealthResponse(status="ready")
