import asyncio
import uuid
from collections.abc import Callable
from inspect import unwrap
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from asyncpg import ForeignKeyViolationError, UniqueViolationError
from authlib.integrations.base_client.errors import OAuthError
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tracecat import config
from tracecat.api.common import generic_exception_handler
from tracecat.auth.types import Role
from tracecat.db.models import (
    MCPIntegration,
    OAuthIntegration,
    Organization,
    RegistryRepository,
)
from tracecat.integrations import router as integrations
from tracecat.integrations import service as integration_service
from tracecat.integrations.dependencies import ProviderInfo
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import (
    IntegrationConflictError,
    IntegrationService,
    IntegrationWorkspaceNotFoundError,
)
from tracecat.integrations.types import TokenResponse
from tracecat.registry.repositories import router as repositories
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.repositories.schemas import (
    RegistryRepositoryCreate,
    RegistryRepositoryUpdate,
)
from tracecat.secrets import router as secrets
from tracecat.secrets.schemas import SecretCreate, SecretUpdate

pytestmark = pytest.mark.anyio

SECRET_CONSTRAINT = "uq_secret_name_environment_workspace_id"
ORG_SECRET_CONSTRAINT = "uq_organization_secret_organization_id_name_environment"
REPOSITORY_CONSTRAINT = "uq_registry_repository_organization_id_origin"
SYNTHETIC_ORIGIN = "git+ssh://git@git.example.com/synthetic/registry.git"
GENERIC_ERROR = {"message": "An unexpected error occurred. Please try again later."}


def integrity_error(constraint: str | None) -> IntegrityError:
    original = (
        UniqueViolationError.new(
            {"C": "23505", "n": constraint, "M": "synthetic-driver-detail"}
        )
        if constraint is not None
        else ForeignKeyViolationError("synthetic-driver-detail")
    )
    return IntegrityError(
        "synthetic-statement", {"fixture": "synthetic-bound"}, original
    )


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"*"}),
    )


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )


def route_app(
    endpoint: Callable[..., Any],
    *,
    path: str,
    method: str,
    role: Role,
    session: AsyncSession,
    provider_info: object | None = None,
) -> FastAPI:
    app = FastAPI()
    app.router.route_class = integrations.IntegrationRoute
    app.add_exception_handler(Exception, generic_exception_handler)
    app.add_api_route(path, endpoint, methods=[method])
    route = cast(APIRoute, app.routes[-1])
    for dependency in route.dependant.dependencies:
        assert dependency.call is not None
        if dependency.name == "role":
            app.dependency_overrides[dependency.call] = lambda: role
        elif dependency.name == "session":
            app.dependency_overrides[dependency.call] = lambda: session
        elif dependency.name == "provider_info":
            app.dependency_overrides[dependency.call] = lambda: provider_info
    return app


@pytest.mark.parametrize("org", [False, True])
@pytest.mark.parametrize("update", [False, True])
@pytest.mark.parametrize("failure", ["duplicate", "other_unique", "foreign_key"])
@pytest.mark.parametrize("workspace_path", [False, True])
async def test_secret_integrity_responses(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    org: bool,
    update: bool,
    failure: str,
    workspace_path: bool,
) -> None:
    constraint = ORG_SECRET_CONSTRAINT if org else SECRET_CONSTRAINT
    error = integrity_error(
        constraint
        if failure == "duplicate"
        else "uq_synthetic_other"
        if failure == "other_unique"
        else None
    )
    session = AsyncMock(spec=AsyncSession)
    service = MagicMock()
    operation = ("update" if update else "create") + (
        "_org_secret" if org else "_secret"
    )
    setattr(service, operation, AsyncMock(side_effect=error))
    service.get_secret = AsyncMock()
    service.get_org_secret = AsyncMock()
    monkeypatch.setattr(secrets, "SecretsService", MagicMock(return_value=service))
    endpoint = (
        (secrets.update_org_secret_by_id if update else secrets.create_org_secret)
        if org
        else (secrets.update_secret_by_id if update else secrets.create_secret)
    )
    path = "/organization/secrets" if org else "/secrets"
    if workspace_path and not org:
        path = f"/workspaces/{role.workspace_id}{path}"
    template = f"{path}/{{secret_id}}" if update else path
    url = f"{path}/{uuid.uuid4()}" if update else path
    app = route_app(endpoint, path=template, method="POST", role=role, session=session)
    body = (
        {"name": "synthetic-secret"}
        if update
        else {
            "name": "synthetic-secret",
            "keys": [{"key": "VALUE", "value": "synthetic-value"}],
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(url, json=body)
    assert response.status_code == (409 if failure == "duplicate" else 500)
    assert response.json() == (
        {
            "detail": "Organization secret already exists"
            if org
            else "Secret already exists"
        }
        if failure == "duplicate"
        else GENERIC_ERROR
    )
    session.rollback.assert_awaited_once()


@pytest.mark.parametrize("update", [False, True])
@pytest.mark.parametrize("failure", ["duplicate", "other_unique", "foreign_key"])
async def test_repository_integrity_responses(
    monkeypatch: pytest.MonkeyPatch, role: Role, update: bool, failure: str
) -> None:
    error = integrity_error(
        REPOSITORY_CONSTRAINT
        if failure == "duplicate"
        else "uq_synthetic_other"
        if failure == "other_unique"
        else None
    )
    session = AsyncMock(spec=AsyncSession)
    service = MagicMock()
    service.get_repository_by_id = AsyncMock(
        return_value=RegistryRepository(
            organization_id=role.organization_id, origin=SYNTHETIC_ORIGIN
        )
    )
    service.create_repository = AsyncMock(side_effect=error)
    service.update_repository = AsyncMock(side_effect=error)
    entitlement = AsyncMock()
    monkeypatch.setattr(repositories, "check_entitlement", entitlement)
    monkeypatch.setattr(
        repositories, "RegistryReposService", MagicMock(return_value=service)
    )
    endpoint = (
        repositories.update_registry_repository
        if update
        else repositories.create_registry_repository
    )
    app = route_app(
        endpoint,
        path="/registry/{repository_id}" if update else "/registry",
        method="PATCH" if update else "POST",
        role=role,
        session=session,
    )
    url = f"/registry/{uuid.uuid4()}" if update else "/registry"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.request(
            "PATCH" if update else "POST", url, json={"origin": SYNTHETIC_ORIGIN}
        )
    assert response.status_code == (409 if failure == "duplicate" else 500)
    assert response.json() == (
        {"detail": "Registry repository already exists"}
        if failure == "duplicate"
        else GENERIC_ERROR
    )
    session.rollback.assert_awaited_once()
    entitlement.assert_awaited_once()


@pytest.mark.parametrize("workspace_path", [False, True])
@pytest.mark.parametrize(
    "failure",
    [
        "integrity",
        "provider_validation",
        "provider_oauth",
        "provider_network",
        "unexpected",
        "conflict",
        "success",
    ],
)
async def test_client_credentials_error_boundary(
    monkeypatch: pytest.MonkeyPatch, role: Role, workspace_path: bool, failure: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    service = MagicMock()
    service.get_integration = AsyncMock(return_value=MagicMock())
    service.store_integration = AsyncMock()
    provider = MagicMock()
    provider.get_client_credentials_token = AsyncMock(
        return_value=TokenResponse(access_token=SecretStr("synthetic-access"))
    )
    impl = MagicMock()
    impl.id = "synthetic-provider"
    impl.instantiate = AsyncMock(return_value=provider)
    info = ProviderInfo.model_construct(
        impl=impl,
        key=ProviderKey(id=impl.id, grant_type=OAuthGrantType.CLIENT_CREDENTIALS),
    )
    if failure == "integrity":
        service.store_integration.side_effect = integrity_error(None)
    elif failure == "conflict":
        service.store_integration.side_effect = IntegrationConflictError(
            "Integration resource already exists"
        )
    elif failure == "provider_validation":
        impl.instantiate.side_effect = ValueError("Client credentials are required")
    elif failure == "provider_oauth":
        provider.get_client_credentials_token.side_effect = OAuthError(
            error="invalid_client", description="synthetic-upstream-detail"
        )
    elif failure == "provider_network":
        provider.get_client_credentials_token.side_effect = httpx.ConnectError(
            "synthetic-upstream-detail"
        )
    elif failure == "unexpected":
        impl.instantiate.side_effect = RuntimeError("synthetic-unexpected-detail")
    monkeypatch.setattr(
        integrations, "IntegrationService", MagicMock(return_value=service)
    )
    prefix = f"/workspaces/{role.workspace_id}" if workspace_path else ""
    path = f"{prefix}/integrations/{{provider_id}}/test"
    app = route_app(
        integrations.test_connection,
        path=path,
        method="POST",
        role=role,
        session=session,
        provider_info=info,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(f"{prefix}/integrations/synthetic-provider/test")
    if failure in {"integrity", "unexpected"}:
        assert response.status_code == 500
        assert response.json() == GENERIC_ERROR
    elif failure == "conflict":
        assert response.status_code == 409
        assert response.json() == {"detail": "Integration resource already exists"}
    else:
        assert response.status_code == 200
        assert response.json()["success"] is (failure == "success")
        if failure == "provider_validation":
            assert response.json()["error"] == "Client credentials are required"
        elif failure in {"provider_oauth", "provider_network"}:
            assert (
                response.json()["error"]
                == "Could not authenticate with the OAuth provider"
            )
    if failure.startswith("provider_") or failure == "unexpected":
        service.store_integration.assert_not_awaited()
    else:
        service.store_integration.assert_awaited_once()


@pytest.mark.parametrize(
    "constraint",
    [
        "uq_oauth_integration_workspace_provider_user_flow",
        "uq_oauth_provider_workspace_provider_grant_type",
        "uq_mcp_integration_workspace_slug",
        "uq_synthetic_other",
        None,
    ],
)
async def test_integration_commit_classifies_only_known_unique_conflicts(
    role: Role, constraint: str | None
) -> None:
    session = AsyncMock(spec=AsyncSession)
    error = integrity_error(constraint)
    session.commit.side_effect = error
    service = IntegrationService(session, role=role)
    expected = (
        IntegrityError
        if constraint in {None, "uq_synthetic_other"}
        else IntegrationConflictError
    )
    with pytest.raises(expected) as exc_info:
        await service._commit_integration()
    if expected is IntegrityError:
        assert exc_info.value is error
    else:
        assert str(exc_info.value) == "Integration resource already exists"
    session.rollback.assert_awaited_once()


async def test_mcp_probe_does_not_publish_database_errors(
    monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    session = AsyncMock(spec=AsyncSession)
    service = IntegrationService(session, role=role)
    error = integrity_error(None)
    monkeypatch.setattr(
        service, "resolve_mcp_http_server_config", AsyncMock(side_effect=error)
    )
    integration = MCPIntegration(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        name="Synthetic MCP",
        slug="synthetic-mcp",
        server_type="http",
        auth_type="none",
        server_uri="https://mcp.example.com/synthetic",
    )
    with pytest.raises(IntegrityError) as exc_info:
        await service.verify_mcp_integration(mcp_integration=integration)
    assert exc_info.value is error
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


async def test_refresh_database_error_is_not_a_soft_token_failure(
    monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    session = AsyncMock(spec=AsyncSession)
    service = IntegrationService(session, role=role)
    integration = MagicMock(spec=OAuthIntegration)
    integration.id = uuid.uuid4()
    integration.needs_refresh = True
    integration.grant_type = OAuthGrantType.CLIENT_CREDENTIALS
    refresh_session = MagicMock(spec=AsyncSession)
    refresh_session.scalar = AsyncMock(return_value=integration)
    refresh_session.commit = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=refresh_session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        integration_service, "get_async_session_context_manager", lambda: context
    )
    error = integrity_error(None)
    monkeypatch.setattr(
        IntegrationService, "_refresh_cc_integration", AsyncMock(side_effect=error)
    )
    with pytest.raises(IntegrityError) as exc_info:
        await service.refresh_token_if_needed(integration)
    assert exc_info.value is error
    refresh_session.commit.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "first_kind,second_kind",
    [
        ("config", "config"),
        ("token", "token"),
        ("config", "token"),
        ("token", "config"),
    ],
)
async def test_workspace_integration_writes_serialize(
    monkeypatch: pytest.MonkeyPatch,
    test_db_engine: AsyncEngine,
    test_admin_role: Role,
    first_kind: str,
    second_kind: str,
) -> None:
    key = ProviderKey(
        id="synthetic-serialized", grant_type=OAuthGrantType.CLIENT_CREDENTIALS
    )
    ready = asyncio.Event()
    release = asyncio.Event()

    async def write(service: IntegrationService, kind: str) -> OAuthIntegration:
        if kind == "config":
            return await service.store_provider_config(
                provider_key=key, client_id="synthetic-client"
            )
        return await service.store_integration(
            provider_key=key, access_token=SecretStr("synthetic-access")
        )

    async with (
        AsyncSession(test_db_engine, expire_on_commit=False) as first_session,
        AsyncSession(test_db_engine, expire_on_commit=False) as second_session,
    ):
        first = IntegrationService(first_session, role=test_admin_role)
        second = IntegrationService(second_session, role=test_admin_role)
        commit = first._commit_integration

        async def hold_commit() -> None:
            ready.set()
            await release.wait()
            await commit()

        monkeypatch.setattr(first, "_commit_integration", hold_commit)
        first_task = asyncio.create_task(write(first, first_kind))
        await asyncio.wait_for(ready.wait(), timeout=5)
        second_task = asyncio.create_task(write(second, second_kind))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(second_task), timeout=0.2)
        finally:
            release.set()
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task), timeout=10
        )
        assert results[0].id == results[1].id

    async with AsyncSession(test_db_engine) as check:
        rows = (
            await check.scalars(
                select(OAuthIntegration).where(
                    OAuthIntegration.workspace_id == test_admin_role.workspace_id,
                    OAuthIntegration.provider_id == key.id,
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].user_id is None
        service = IntegrationService(check, role=test_admin_role)
        if "config" in (first_kind, second_kind):
            assert rows[0].encrypted_client_id is not None
            assert (
                service.decrypt_client_credential(rows[0].encrypted_client_id)
                == "synthetic-client"
            )
        if "token" in (first_kind, second_kind):
            assert (
                service._decrypt_token(rows[0].encrypted_access_token)
                == "synthetic-access"
            )


@pytest.mark.parametrize("operation", ["get", "config", "token"])
async def test_existing_duplicate_integrations_require_cleanup(
    test_db_engine: AsyncEngine, test_admin_role: Role, operation: str
) -> None:
    key = ProviderKey(
        id="synthetic-duplicates", grant_type=OAuthGrantType.CLIENT_CREDENTIALS
    )
    async with AsyncSession(test_db_engine) as session:
        session.add_all(
            [
                OAuthIntegration(
                    workspace_id=test_admin_role.workspace_id,
                    provider_id=key.id,
                    grant_type=key.grant_type,
                    encrypted_access_token=b"synthetic-unchanged",
                )
                for _ in range(2)
            ]
        )
        await session.commit()
        service = IntegrationService(session, role=test_admin_role)
        with pytest.raises(
            IntegrationConflictError, match="Remove duplicate configurations"
        ):
            if operation == "get":
                await service.get_integration(provider_key=key)
            elif operation == "config":
                await service.store_provider_config(
                    provider_key=key, client_id="synthetic-new"
                )
            else:
                await service.store_integration(
                    provider_key=key, access_token=SecretStr("synthetic-new")
                )
        rows = (
            await session.scalars(
                select(OAuthIntegration).where(
                    OAuthIntegration.workspace_id == test_admin_role.workspace_id,
                    OAuthIntegration.provider_id == key.id,
                )
            )
        ).all()
        assert len(rows) == 2
        assert all(row.encrypted_access_token == b"synthetic-unchanged" for row in rows)
        if operation == "get":
            info = ProviderInfo.model_construct(impl=MagicMock(), key=key)
            app = route_app(
                unwrap(integrations.get_integration),
                path="/integrations/synthetic-duplicates",
                method="GET",
                role=test_admin_role,
                session=session,
                provider_info=info,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                response = await client.get("/integrations/synthetic-duplicates")
            assert response.status_code == 409
            assert response.json() == {
                "detail": "Multiple integrations exist for this provider. Remove duplicate configurations before continuing."
            }


@pytest.mark.parametrize("field", ["organization_id", "workspace_id"])
@pytest.mark.parametrize("operation", ["config", "token"])
async def test_integration_lock_requires_tenant_workspace(
    test_db_engine: AsyncEngine, test_admin_role: Role, field: str, operation: str
) -> None:
    role = test_admin_role.model_copy(update={field: uuid.uuid4()})
    key = ProviderKey(
        id="synthetic-missing", grant_type=OAuthGrantType.CLIENT_CREDENTIALS
    )
    async with AsyncSession(test_db_engine) as session:
        service = IntegrationService(session, role=role)
        with pytest.raises(
            IntegrationWorkspaceNotFoundError, match="Workspace not found"
        ):
            if operation == "config":
                await service.store_provider_config(
                    provider_key=key, client_id="synthetic"
                )
            else:
                await service.store_integration(
                    provider_key=key, access_token=SecretStr("synthetic")
                )
        assert not (
            await session.scalars(
                select(OAuthIntegration).where(OAuthIntegration.provider_id == key.id)
            )
        ).all()


@pytest.mark.parametrize("failure", ["missing", "duplicates", "database"])
@pytest.mark.parametrize("workspace_prefix", [False, True])
async def test_integration_domain_error_http_boundary(
    monkeypatch: pytest.MonkeyPatch, role: Role, failure: str, workspace_prefix: bool
) -> None:
    session = AsyncMock(spec=AsyncSession)
    info = MagicMock()
    info.key = ProviderKey(id="synthetic", grant_type=OAuthGrantType.CLIENT_CREDENTIALS)
    errors = {
        "missing": IntegrationWorkspaceNotFoundError("Workspace not found"),
        "duplicates": IntegrationConflictError(
            "Remove duplicate configurations before continuing."
        ),
        "database": integrity_error(None),
    }
    mock_service = MagicMock(spec=IntegrationService)
    mock_service.store_provider_config = AsyncMock(side_effect=errors[failure])
    monkeypatch.setattr(
        integrations, "IntegrationService", lambda *args, **kwargs: mock_service
    )
    path = (
        f"/workspaces/{role.workspace_id}/integrations/synthetic"
        if workspace_prefix
        else "/integrations/synthetic"
    )
    app = route_app(
        unwrap(integrations.update_integration),
        method="PUT",
        path=path,
        role=role,
        session=session,
        provider_info=info,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.put(
            path, json={"client_id": "synthetic", "grant_type": "client_credentials"}
        )
    assert (
        response.status_code
        == {"missing": 404, "duplicates": 409, "database": 500}[failure]
    ), response.text
    if failure == "database":
        assert response.json() == GENERIC_ERROR
    else:
        assert response.json() == {"detail": str(errors[failure])}


async def test_missing_workspace_cannot_create_integration(role: Role) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = None
    service = IntegrationService(session, role=role)
    with pytest.raises(IntegrationWorkspaceNotFoundError, match="Workspace not found"):
        await unwrap(service.store_integration)(
            service,
            provider_key=ProviderKey(
                id="synthetic", grant_type=OAuthGrantType.CLIENT_CREDENTIALS
            ),
            access_token=SecretStr("synthetic"),
        )
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", ["duplicate", "foreign_key", "missing_after_conflict"]
)
async def test_platform_repository_get_or_create_race(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    service = PlatformRegistryReposService(session)
    repository = MagicMock()
    get_repository = AsyncMock(
        side_effect=[None, repository if failure == "duplicate" else None]
    )
    error = integrity_error(
        None if failure == "foreign_key" else "uq_platform_registry_repository_origin"
    )
    monkeypatch.setattr(service, "get_repository", get_repository)
    monkeypatch.setattr(service, "create_repository", AsyncMock(side_effect=error))
    if failure == "duplicate":
        assert await service.get_or_create_repository(SYNTHETIC_ORIGIN) is repository
    else:
        with pytest.raises(IntegrityError) as exc_info:
            await service.get_or_create_repository(SYNTHETIC_ORIGIN)
        assert exc_info.value is error
    session.rollback.assert_awaited_once()


@pytest.mark.parametrize("model", [RegistryRepositoryCreate, RegistryRepositoryUpdate])
@pytest.mark.parametrize(
    "origin",
    [
        None,
        123,
        "https://git.example.com/synthetic/registry.git",
        "git+ssh://invalid",
        "",
    ],
)
def test_repository_origin_validation(
    model: type[RegistryRepositoryCreate] | type[RegistryRepositoryUpdate],
    origin: object,
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"origin": origin})


def test_secret_validation_matches_database_columns() -> None:
    body = {
        "name": "synthetic-secret",
        "keys": [{"key": "VALUE", "value": "synthetic-value"}],
    }
    for field, value in [
        ("description", "a" * 256),
        ("environment", "a" * 256),
        ("environment", ""),
    ]:
        with pytest.raises(ValidationError):
            SecretCreate.model_validate({**body, field: value})
    with pytest.raises(ValidationError):
        SecretUpdate.model_validate({"type": None})
    assert SecretUpdate().model_dump(exclude_unset=True) == {}
    assert RegistryRepositoryUpdate().model_dump(exclude_unset=True) == {}


async def test_repository_conflicts_use_postgres_and_preserve_state(
    session: AsyncSession, test_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repositories, "check_entitlement", AsyncMock())
    session.add(
        Organization(
            id=test_role.organization_id,
            name="Synthetic organization",
            slug="synthetic-conflicts",
            is_active=True,
        )
    )
    await session.commit()
    create = unwrap(repositories.create_registry_repository)
    update = unwrap(repositories.update_registry_repository)
    first = await create(
        role=test_role,
        session=session,
        params=RegistryRepositoryCreate(origin=SYNTHETIC_ORIGIN),
    )
    first_id = first.id
    second_origin = "git+ssh://git@git.example.com/synthetic/other.git"
    second = await create(
        role=test_role,
        session=session,
        params=RegistryRepositoryCreate(origin=second_origin),
    )
    for operation in [
        lambda: create(
            role=test_role,
            session=session,
            params=RegistryRepositoryCreate(origin=SYNTHETIC_ORIGIN),
        ),
        lambda: update(
            role=test_role,
            session=session,
            repository_id=second.id,
            params=RegistryRepositoryUpdate(origin=SYNTHETIC_ORIGIN),
        ),
    ]:
        with pytest.raises(HTTPException) as exc_info:
            await operation()
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Registry repository already exists"
    rows = (
        (
            await session.execute(
                select(RegistryRepository.id, RegistryRepository.origin)
            )
        )
        .tuples()
        .all()
    )
    assert (first_id, SYNTHETIC_ORIGIN) in rows
    assert len(rows) == 2
    assert second_origin in [origin for _, origin in rows]
