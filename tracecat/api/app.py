import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager

import tracecat_registry
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.router import router as admin_router
from tracecat_ee.agent.approvals.router import router as approvals_router

from tracecat import __version__ as APP_VERSION
from tracecat import config
from tracecat.admin.registry.router import router as admin_registry_router
from tracecat.agent.internal_router import router as internal_agent_router
from tracecat.agent.preset.internal_router import (
    router as internal_agent_preset_router,
)
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
from tracecat.auth.dependencies import (
    require_any_auth_type_enabled,
    require_auth_type_enabled,
)
from tracecat.auth.discovery import router as auth_discovery_router
from tracecat.auth.enums import AuthType
from tracecat.auth.oidc import create_platform_oauth_client, oidc_auth_type_enabled
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
from tracecat.cases.dropdowns.router import definitions_router as case_dropdowns_router
from tracecat.cases.dropdowns.router import values_router as case_dropdown_values_router
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
from tracecat.cases.triggers.consumer import start_case_trigger_consumer
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.editor.router import router as editor_router
from tracecat.exceptions import EntitlementRequired, ScopeDeniedError, TracecatException
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
from tracecat.organization.management import (
    ensure_default_organization,
    get_default_organization_id,
)
from tracecat.organization.router import router as org_router
from tracecat.registry.actions.router import router as registry_actions_router
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.repositories.router import router as registry_repos_router
from tracecat.registry.sync.jobs import sync_platform_registry_on_startup
from tracecat.secrets.router import org_router as org_secrets_router
from tracecat.secrets.router import router as secrets_router
from tracecat.settings.router import router as org_settings_router
from tracecat.settings.service import SettingsService, get_setting_override
from tracecat.storage.blob import configure_bucket_lifecycle, ensure_bucket_exists
from tracecat.tables.internal_router import router as internal_tables_router
from tracecat.tables.router import router as tables_router
from tracecat.tags.router import router as tags_router
from tracecat.variables.internal_router import router as internal_variables_router
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


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    await ensure_default_organization()

    # Spawn platform registry sync as background task (non-blocking)
    # Uses leader election to prevent race conditions across multiple API processes
    registry_sync_task = asyncio.create_task(
        sync_platform_registry_on_startup(),
        name="platform_registry_sync",
    )
    logger.debug("Spawned background task for platform registry sync")

    case_trigger_task = None
    if config.TRACECAT__CASE_TRIGGERS_ENABLED:
        case_trigger_task = asyncio.create_task(
            start_case_trigger_consumer(),
            name="case_trigger_consumer",
        )
        logger.debug("Spawned background task for case trigger consumer")

    logger.info(
        "Feature flags", feature_flags=[f.value for f in config.TRACECAT__FEATURE_FLAGS]
    )
    logger.info("API startup complete")

    yield

    # Gracefully handle the registry sync task during shutdown
    if not registry_sync_task.done():
        logger.info("Waiting for platform registry sync task to complete...")
        try:
            # Give the task a reasonable time to complete
            await asyncio.wait_for(registry_sync_task, timeout=10.0)
            logger.info("Platform registry sync task completed")
        except TimeoutError:
            logger.warning(
                "Platform registry sync task did not complete in time, cancelling"
            )
            registry_sync_task.cancel()
            try:
                await registry_sync_task
            except asyncio.CancelledError:
                logger.debug("Platform registry sync task cancelled")
        except Exception as e:
            logger.warning(
                "Platform registry sync task failed during shutdown", error=e
            )
    else:
        # Task already completed - retrieve result to surface any exceptions
        try:
            registry_sync_task.result()
            logger.debug("Platform registry sync task had already completed")
        except Exception as e:
            logger.warning(
                "Platform registry sync task failed before shutdown", error=e
            )

    if case_trigger_task is not None:
        case_trigger_task.cancel()
        try:
            await case_trigger_task
        except asyncio.CancelledError:
            logger.debug("Case trigger consumer task cancelled")
        except Exception as e:
            logger.warning("Case trigger consumer stopped with error", error=e)


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
def validation_exception_handler(request: Request, exc: Exception) -> Response:
    """Improves visiblity of 422 errors."""
    if not isinstance(exc, RequestValidationError):
        return ORJSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )
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


def fastapi_users_auth_exception_handler(request: Request, exc: Exception) -> Response:
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


def entitlement_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle EntitlementRequired exceptions with a 403 Forbidden response."""
    if not isinstance(exc, EntitlementRequired):
        return ORJSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc)},
        )
    logger.warning(
        "Entitlement required",
        entitlement=exc.entitlement,
        path=request.url.path,
    )
    return ORJSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "type": "EntitlementRequired",
            "message": str(exc),
            "detail": exc.detail,
        },
    )


def scope_denied_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle ScopeDeniedError exceptions with a 403 Forbidden response.

    Returns a machine-readable error response with:
    - code: "insufficient_scope"
    - message: Human-readable error message
    - required_scopes: Scopes that were required for the operation
    - missing_scopes: Scopes that the user was missing
    """
    if not isinstance(exc, ScopeDeniedError):
        return ORJSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc)},
        )
    logger.warning(
        "Scope denied",
        required_scopes=exc.required_scopes,
        missing_scopes=exc.missing_scopes,
        path=request.url.path,
        role=ctx_role.get(),
    )
    return ORJSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "error": {
                "code": "insufficient_scope",
                "message": str(exc),
                "required_scopes": exc.required_scopes,
                "missing_scopes": exc.missing_scopes,
            }
        },
    )


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
    app.state.logger = logger

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
    app.include_router(admin_registry_router, prefix="/admin")
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
        case_dropdowns_router,
        dependencies=[Depends(feature_flag_dep(FeatureFlag.CASE_DROPDOWNS))],
    )
    app.include_router(
        case_dropdown_values_router,
        dependencies=[Depends(feature_flag_dep(FeatureFlag.CASE_DROPDOWNS))],
    )
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
    app.include_router(internal_agent_router)
    app.include_router(internal_agent_preset_router)
    app.include_router(internal_case_attachments_router)
    app.include_router(internal_cases_router)
    app.include_router(internal_comments_router)
    app.include_router(internal_case_tags_router)
    app.include_router(internal_case_tag_definitions_router)
    app.include_router(internal_tables_router)
    app.include_router(internal_variables_router)
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

    if oidc_auth_type_enabled():
        oauth_client = create_platform_oauth_client()
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
            dependencies=[
                require_any_auth_type_enabled([AuthType.OIDC, AuthType.GOOGLE_OAUTH])
            ],
        )
    app.include_router(
        saml_router,
        dependencies=[require_auth_type_enabled(AuthType.SAML)],
    )
    app.include_router(auth_discovery_router)

    if AuthType.BASIC not in config.TRACECAT__AUTH_TYPES:
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
    app.add_exception_handler(
        FastAPIUsersException,
        fastapi_users_auth_exception_handler,
    )
    app.add_exception_handler(EntitlementRequired, entitlement_exception_handler)
    app.add_exception_handler(ScopeDeniedError, scope_denied_exception_handler)

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


class RegistryStatus(BaseModel):
    synced: bool
    expected_version: str
    current_version: str | None


class ReadinessResponse(BaseModel):
    status: str
    registry: RegistryStatus


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
    ee_multi_tenant: bool


@app.get("/info", include_in_schema=False)
async def info(session: AsyncDBSession) -> AppInfo:
    """Non-sensitive information about the platform, for frontend configuration."""

    keys = {"auth_basic_enabled", "oauth_google_enabled", "saml_enabled"}

    # Get the default organization for platform-level settings
    org_id = await get_default_organization_id(session)
    service = SettingsService(session, role=bootstrap_role(org_id))
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
        ee_multi_tenant=config.TRACECAT__EE_MULTI_TENANT,
    )


@app.get("/health", tags=["public"])
def check_health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/ready", tags=["public"])
async def check_ready(session: AsyncDBSession) -> ReadinessResponse:
    """Readiness check - returns 200 only after startup and registry sync complete.

    Use this endpoint for Docker healthchecks to ensure the API has finished
    initializing and the platform registry is synced before accepting traffic.

    Returns a detailed response including registry sync status.
    """
    expected_version = tracecat_registry.__version__

    # Check registry sync status
    repos_service = PlatformRegistryReposService(session)
    repo = await repos_service.get_repository(DEFAULT_REGISTRY_ORIGIN)

    if repo is None or repo.current_version is None:
        registry_status = RegistryStatus(
            synced=False,
            expected_version=expected_version,
            current_version=None,
        )
    else:
        registry_status = RegistryStatus(
            synced=repo.current_version.version == expected_version,
            expected_version=expected_version,
            current_version=repo.current_version.version,
        )

    # Not ready if registry is not synced
    if not registry_status.synced:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ReadinessResponse(
                status="not_ready",
                registry=registry_status,
            ).model_dump(),
        )

    return ReadinessResponse(
        status="ready",
        registry=registry_status,
    )
