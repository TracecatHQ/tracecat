"""Enterprise CRUD distinguishes named conflicts from unexpected DB failures."""

from collections.abc import Awaitable, Callable
from typing import Literal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from asyncpg import ForeignKeyViolationError, UniqueViolationError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.organizations import router as org_router
from tracecat_ee.admin.organizations.schemas import AdminOrgInvitationCreate, OrgUpdate
from tracecat_ee.admin.organizations.service import AdminOrgService
from tracecat_ee.admin.tiers import router as tier_router
from tracecat_ee.admin.tiers.service import AdminTierService
from tracecat_ee.admin.users import router as user_router
from tracecat_ee.admin.users.schemas import AdminUserCreate
from tracecat_ee.admin.users.service import AdminUserService
from tracecat_ee.rbac import router as rbac_router
from tracecat_ee.rbac.schemas import (
    GroupCreate,
    GroupMemberAdd,
    GroupRoleAssignmentCreate,
    GroupUpdate,
    RoleCreate,
    RoleUpdate,
    ScopeCreate,
)
from tracecat_ee.rbac.service import RBACService
from tracecat_ee.secrets.references import router as reference_router
from tracecat_ee.secrets.references.service import SecretReferencesService
from tracecat_ee.secrets.stores import router as store_router
from tracecat_ee.secrets.stores.service import SecretStoresService

from tracecat.api.common import generic_exception_handler
from tracecat.auth.types import PlatformRole, Role
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.identifiers import SecretUUID
from tracecat.secrets.schemas import (
    AwsSecretKeyMapping,
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    AwsSecretsManagerStoreCreate,
    SecretStoreCreate,
    SecretStoreUpdate,
)
from tracecat.tiers.schemas import TierCreate

SENTINEL = "synthetic-private-db-detail"


def integrity_error(kind: str, constraint: str) -> IntegrityError:
    match kind:
        case "unique":
            original = UniqueViolationError.new(
                {"C": "23505", "n": constraint, "M": SENTINEL}
            )
        case "foreign_key":
            original = ForeignKeyViolationError.new(
                {"C": "23503", "n": constraint, "M": SENTINEL}
            )
        case _:
            original = RuntimeError(f"{SENTINEL} {constraint}")
    adapter = RuntimeError(SENTINEL)
    adapter.__cause__ = original
    return IntegrityError(SENTINEL, {"private": SENTINEL}, adapter)


async def response_for(call: Callable[[], Awaitable[object]]) -> tuple[int, str]:
    app = FastAPI()
    app.add_exception_handler(Exception, generic_exception_handler)

    async def endpoint() -> object:
        return await call()

    app.add_api_route("/resource", endpoint, methods=["POST"])
    async with AsyncClient(
        transport=ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post("/resource")
    assert SENTINEL not in response.text
    if response.status_code == 500:
        assert response.json() == {
            "message": "An unexpected error occurred. Please try again later."
        }
    return response.status_code, response.text


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        organization_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        scopes=frozenset({"*"}),
    )


CONFLICT_CASES = [
    ("scope", "uq_scope_organization_id_name"),
    ("group_create", "uq_group_organization_id_name"),
    ("group_update", "uq_group_organization_id_name"),
    ("member", "pk_group_member"),
    ("assignment", "uq_group_role_assignment_group_id_workspace_id"),
    ("assignment", "ix_group_role_assignment_group_org_unique"),
    ("store_create", "uq_organization_secret_store_organization_id_name"),
    ("store_update", "uq_organization_secret_store_organization_id_name"),
    ("reference_create", "uq_secret_name_environment_workspace_id"),
    ("reference_update", "uq_secret_name_environment_workspace_id"),
    ("invitation", "ix_invitation_org_email_pending_unique"),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("resource", "constraint"), CONFLICT_CASES)
@pytest.mark.parametrize(
    "kind", ["unique", "unrelated_unique", "foreign_key", "message_only"]
)
async def test_router_named_conflicts_only(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    resource: str,
    constraint: str,
    kind: str,
) -> None:
    error = integrity_error(
        "unique" if kind == "unrelated_unique" else kind,
        "uq_unrelated" if kind == "unrelated_unique" else constraint,
    )
    fail = AsyncMock(side_effect=error)
    session = AsyncMock(spec=AsyncSession)

    async def call() -> object:
        match resource:
            case "scope":
                monkeypatch.setattr(RBACService, "create_scope", fail)
                return await rbac_router.create_scope(
                    role=role,
                    session=session,
                    params=ScopeCreate(name="synthetic:read", description=None),
                )
            case "group_create":
                monkeypatch.setattr(RBACService, "create_group", fail)
                return await rbac_router.create_group(
                    role=role,
                    session=session,
                    params=GroupCreate(name="synthetic", description=None),
                )
            case "group_update":
                monkeypatch.setattr(RBACService, "update_group", fail)
                return await rbac_router.update_group(
                    role=role,
                    session=session,
                    group_id=uuid4(),
                    params=GroupUpdate(name="synthetic", description=None),
                )
            case "member":
                monkeypatch.setattr(RBACService, "add_group_member", fail)
                return await rbac_router.add_group_member(
                    role=role,
                    session=session,
                    group_id=uuid4(),
                    params=GroupMemberAdd(user_id=uuid4()),
                )
            case "assignment":
                monkeypatch.setattr(RBACService, "create_group_role_assignment", fail)
                return await rbac_router.create_assignment(
                    role=role,
                    session=session,
                    params=GroupRoleAssignmentCreate(
                        group_id=uuid4(), role_id=uuid4(), workspace_id=None
                    ),
                )
            case "store_create":
                monkeypatch.setattr(SecretStoresService, "create_store", fail)
                return await store_router.create_secret_store(
                    role=role,
                    session=session,
                    params=SecretStoreCreate(
                        name="synthetic",
                        config=AwsSecretsManagerStoreCreate(region="us-east-1"),
                    ),
                )
            case "store_update":
                monkeypatch.setattr(
                    SecretStoresService,
                    "get_store",
                    AsyncMock(return_value=MagicMock()),
                )
                monkeypatch.setattr(SecretStoresService, "update_store", fail)
                return await store_router.update_secret_store(
                    role=role,
                    session=session,
                    store_id=uuid4(),
                    params=SecretStoreUpdate(name="synthetic"),
                )
            case "reference_create":
                monkeypatch.setattr(
                    SecretReferencesService, "create_aws_secret_reference", fail
                )
                return await reference_router.create_aws_secret_reference(
                    role=role,
                    session=session,
                    params=AwsSecretReferenceCreate(
                        name="synthetic",
                        store_id=uuid4(),
                        remote_reference="synthetic",
                        key_mapping=AwsSecretKeyMapping.model_validate(
                            {"mode": "whole_string", "keys": ["TOKEN"]}
                        ),
                    ),
                )
            case "reference_update":
                monkeypatch.setattr(
                    SecretReferencesService,
                    "get_secret",
                    AsyncMock(return_value=MagicMock()),
                )
                monkeypatch.setattr(
                    SecretReferencesService, "update_aws_secret_reference", fail
                )
                return await reference_router.update_aws_secret_reference(
                    role=role,
                    session=session,
                    secret_id=SecretUUID.new(uuid4()),
                    params=AwsSecretReferenceUpdate(name="synthetic"),
                )
            case "invitation":
                monkeypatch.setattr(
                    AdminOrgService, "create_organization_invitation", fail
                )
                return await org_router.create_organization_invitation(
                    role=PlatformRole(
                        type="user", user_id=uuid4(), service_id="tracecat-api"
                    ),
                    session=session,
                    org_id=uuid4(),
                    params=AdminOrgInvitationCreate(email="synthetic@example.com"),
                )
            case _:
                raise AssertionError(resource)

    status_code, _ = await response_for(call)
    fail.assert_awaited_once()
    session.rollback.assert_awaited_once()
    assert status_code == (409 if kind == "unique" else 500)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "constraint",
    [
        "uq_user_role_assignment_user_id_workspace_id",
        "ix_user_role_assignment_user_org_unique",
        "uq_unrelated",
    ],
)
@pytest.mark.parametrize("kind", ["unique", "foreign_key", "message_only"])
async def test_user_assignment_named_conflicts_only(
    monkeypatch: pytest.MonkeyPatch, role: Role, constraint: str, kind: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = integrity_error(kind, constraint)
    monkeypatch.setattr("tracecat_ee.rbac.service.lock_role_changes", AsyncMock())
    service = RBACService(session, role)
    service._user_in_organization = AsyncMock(return_value=True)
    service._ensure_role_assignable = AsyncMock()
    expected = (
        TracecatConflictError
        if kind == "unique" and constraint != "uq_unrelated"
        else IntegrityError
    )
    with pytest.raises(expected):
        await service.create_user_assignment(user_id=uuid4(), role_id=uuid4())
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["create", "update", "tier"])
async def test_nonunique_names_do_not_invent_conflicts(
    monkeypatch: pytest.MonkeyPatch, role: Role, operation: str
) -> None:
    fail = AsyncMock(side_effect=integrity_error("unique", "uq_unrelated"))
    session = AsyncMock(spec=AsyncSession)

    async def call() -> object:
        match operation:
            case "create":
                monkeypatch.setattr(RBACService, "create_role", fail)
                return await rbac_router.create_role(
                    role=role,
                    session=session,
                    params=RoleCreate(name="synthetic", description=None),
                )
            case "update":
                monkeypatch.setattr(RBACService, "update_role", fail)
                return await rbac_router.update_role(
                    role=role,
                    session=session,
                    role_id=uuid4(),
                    params=RoleUpdate(
                        name="synthetic", description=None, scope_ids=None
                    ),
                )
            case _:
                monkeypatch.setattr(tier_router, "_require_multi_tenant", lambda: None)
                monkeypatch.setattr(AdminTierService, "create_tier", fail)
                return await tier_router.create_tier(
                    role=PlatformRole(
                        type="user", user_id=uuid4(), service_id="tracecat-api"
                    ),
                    session=session,
                    params=TierCreate(display_name="synthetic"),
                )

    assert (await response_for(call))[0] == 500
    fail.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["reference", "removal"])
@pytest.mark.parametrize(
    "constraint",
    [
        "fk_secret_store_id_organization_secret_store",
        "fk_secret_store_authorization",
        "fk_unrelated",
    ],
)
@pytest.mark.parametrize("kind", ["foreign_key", "unique", "message_only"])
async def test_only_store_foreign_keys_are_domain_errors(
    role: Role, operation: str, constraint: str, kind: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = integrity_error(kind, constraint)
    expected = (
        (
            TracecatAuthorizationError
            if operation == "reference"
            else TracecatConflictError
        )
        if kind == "foreign_key" and constraint != "fk_unrelated"
        else IntegrityError
    )
    with pytest.raises(expected):
        if operation == "reference":
            await SecretReferencesService(session, role)._commit_reference()
        else:
            await SecretStoresService(session, role)._commit_removal()
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("error_type", [TracecatNotFoundError, ValueError])
async def test_admin_not_found_is_typed(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    monkeypatch.setattr(
        AdminUserService,
        "get_user",
        AsyncMock(side_effect=error_type("synthetic not found")),
    )
    status_code, _ = await response_for(
        lambda: user_router.get_user(
            role=PlatformRole(type="user", user_id=uuid4(), service_id="tracecat-api"),
            session=AsyncMock(spec=AsyncSession),
            user_id=uuid4(),
        )
    )
    assert status_code == (404 if error_type is TracecatNotFoundError else 500)


@pytest.mark.parametrize("field", ["name", "slug", "is_active"])
def test_org_update_rejects_explicit_null(
    field: Literal["name", "slug", "is_active"],
) -> None:
    with pytest.raises(ValidationError):
        OrgUpdate.model_validate({field: None})
    assert OrgUpdate.model_validate({}).model_dump(exclude_unset=True) == {}


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["flush", "commit"])
@pytest.mark.parametrize(
    "kind", ["unique", "foreign_key", "unrelated_unique", "message_only"]
)
async def test_admin_user_creation_catches_flush_and_commit(
    monkeypatch: pytest.MonkeyPatch, stage: str, kind: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    failure = integrity_error(
        "unique" if kind == "unrelated_unique" else kind,
        "uq_unrelated" if kind == "unrelated_unique" else "ix_user_email",
    )
    if stage == "flush":
        session.flush.side_effect = failure
    else:
        session.commit.side_effect = failure
    monkeypatch.setattr(
        "tracecat_ee.admin.users.service.ensure_single_tenant_user_defaults_for_session",
        AsyncMock(),
    )
    status_code, _ = await response_for(
        lambda: user_router.create_user(
            role=PlatformRole(type="user", user_id=uuid4(), service_id="tracecat-api"),
            session=session,
            params=AdminUserCreate(
                email="synthetic@example.com", password="Synthetic-password-123!"
            ),
        )
    )
    assert status_code == (409 if kind == "unique" else 500)
    session.rollback.assert_awaited_once()
