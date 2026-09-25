"""Unit tests for the SCIM 2.0 protocol surface."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, get_args

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.scim.credentials import ScimConnectionRole
from tracecat_ee.scim.protocol import (
    is_scim_path,
    router,
    scim_error_response,
    scim_http_exception_handler,
    scim_validation_exception_handler,
)
from tracecat_ee.scim.schemas import (
    SCIM_CONTENT_TYPE,
    ScimSchema,
)
from tracecat_ee.scim.service import SCIMService

from tests.support.membership import (
    grant_org_membership,
    grant_org_membership_via_group,
)
from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.authz.membership import ensure_member
from tracecat.db.engine import get_async_session
from tracecat.db.models import (
    AccessToken,
    ExternalGroup,
    ExternalGroupMapping,
    ExternalGroupMember,
    ExternalUser,
    Group,
    GroupMember,
    Organization,
    OrganizationMembership,
    ScimConnection,
    User,
)
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)

LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


@pytest.fixture(autouse=True)
def auth_session(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point auth at the test session and allow synthetic provisioning addresses."""
    monkeypatch.setattr(
        config, "TRACECAT__AUTH_ALLOWED_DOMAINS", {"tracecat.com", "example.com"}
    )

    @asynccontextmanager
    async def _session_cm() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        "tracecat.auth.users.get_async_session_auth_context_manager",
        _session_cm,
    )


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="SCIM Org", slug=f"scim-org-{org_id.hex[:8]}")
    session.add(org)
    await session.flush()
    # An existing user keeps provisioning off the first-user superadmin path.
    session.add(
        User(
            id=uuid.uuid4(),
            email=f"seed-{uuid.uuid4().hex[:8]}@tracecat.com",
            hashed_password="x",
        )
    )
    # An activated connection; a pending one links without admitting anyone.
    session.add(
        ScimConnection(
            id=uuid.uuid4(),
            organization_id=org_id,
            key_id=uuid.uuid4().hex[:16],
            hashed="x",
            salt="y",
            preview="scim_...",
            status=ScimConnectionStatus.ACTIVE,
        )
    )
    await session.flush()
    return org


@pytest.fixture
async def scim_role(org: Organization) -> Role:
    return Role(
        type="scim",
        organization_id=org.id,
        scim_connection_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"org:member:remove"}),
    )


@pytest.fixture
async def client(
    session: AsyncSession, scim_role: Role
) -> AsyncIterator[httpx.AsyncClient]:
    """Mount the protocol router with the connection token already resolved.

    Token verification has its own coverage; these tests exercise the protocol
    behaviour behind it. ASGITransport keeps the app on the test's event loop,
    which the session's connection is bound to.
    """
    app = FastAPI()
    app.include_router(router)

    role_dependency = get_args(ScimConnectionRole)[1].dependency

    async def override_role() -> Role:
        return scim_role

    async def override_session() -> AsyncSession:
        return session

    app.dependency_overrides[role_dependency] = override_role
    app.dependency_overrides[get_async_session] = override_session
    _install_handlers(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as test_client:
        yield test_client


def _install_handlers(app: FastAPI) -> None:
    """Mirror the app's SCIM error-envelope wiring."""

    def _http(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, HTTPException)
        return scim_http_exception_handler(request, exc)

    def _validation(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, RequestValidationError)
        return scim_validation_exception_handler(request, exc)

    def _not_found(request: Request, exc: Exception) -> Response:
        return scim_error_response(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )

    def _invalid(request: Request, exc: Exception) -> Response:
        return scim_error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
            scim_type="invalidValue",
        )

    app.add_exception_handler(HTTPException, _http)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(
        TracecatConflictError,
        lambda request, exc: scim_error_response(status_code=409, detail=str(exc)),
    )
    app.add_exception_handler(TracecatNotFoundError, _not_found)
    app.add_exception_handler(TracecatValidationError, _invalid)


async def _post_user(client: httpx.AsyncClient, email: str, **kwargs: Any) -> Any:
    body: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": email,
        "active": True,
        **kwargs,
    }
    return await client.post("/scim/v2/Users", json=body)


async def _is_member(
    session: AsyncSession, *, user_id: uuid.UUID, organization_id: uuid.UUID
) -> bool:
    stmt = select(OrganizationMembership).where(
        OrganizationMembership.user_id == user_id,
        OrganizationMembership.organization_id == organization_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _resource_is_member(
    session: AsyncSession, *, resource_id: uuid.UUID, organization_id: uuid.UUID
) -> bool:
    """Whether the user behind a SCIM resource id is present in the org."""
    user_id = (
        await session.execute(
            select(ExternalUser.user_id).where(ExternalUser.id == resource_id)
        )
    ).scalar_one()
    return await _is_member(session, user_id=user_id, organization_id=organization_id)


# =============================================================================
# Users
# =============================================================================


@pytest.mark.anyio
async def test_post_user_provisions(
    client: httpx.AsyncClient, org: Organization
) -> None:
    """A pushed user is created and returned as a SCIM User resource."""
    email = f"alice-{uuid.uuid4().hex[:8]}@tracecat.com"

    response = await _post_user(client, email, externalId="idp-alice")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.headers["content-type"].startswith(SCIM_CONTENT_TYPE)
    body = response.json()
    assert body["userName"] == email
    assert body["externalId"] == "idp-alice"
    assert body["active"] is True
    assert body["meta"]["resourceType"] == "User"


@pytest.mark.anyio
async def test_duplicate_post_links_rather_than_conflicting(
    client: httpx.AsyncClient, org: Organization
) -> None:
    """A repeated POST is the provider retrying, so it links to the same user."""
    email = f"bob-{uuid.uuid4().hex[:8]}@tracecat.com"

    first = await _post_user(client, email, externalId="idp-bob")
    second = await _post_user(client, email, externalId="idp-bob")

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.anyio
async def test_filter_matches_username_case_insensitively(
    client: httpx.AsyncClient, org: Organization
) -> None:
    """Okta queries before creating; a case-sensitive match would duplicate."""
    email = f"carol-{uuid.uuid4().hex[:8]}@tracecat.com"
    await _post_user(client, email)

    response = await client.get(
        "/scim/v2/Users", params={"filter": f'userName eq "{email.upper()}"'}
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["schemas"] == [LIST_RESPONSE_SCHEMA]
    assert body["totalResults"] == 1
    assert body["startIndex"] == 1
    assert body["itemsPerPage"] == 1
    assert body["Resources"][0]["userName"] == email


@pytest.mark.anyio
async def test_filter_with_no_match_returns_empty_list(
    client: httpx.AsyncClient, org: Organization
) -> None:
    """An unknown user is an empty ListResponse, never a 404."""
    response = await client.get(
        "/scim/v2/Users", params={"filter": 'userName eq "nobody@tracecat.com"'}
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["totalResults"] == 0
    assert body["Resources"] == []


@pytest.mark.anyio
async def test_unsupported_filter_is_a_scim_error(
    client: httpx.AsyncClient, org: Organization
) -> None:
    """An unsupported expression fails loudly rather than returning everything."""
    response = await client.get(
        "/scim/v2/Users", params={"filter": 'displayName eq "x"'}
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["schemas"] == [ScimSchema.ERROR]


@pytest.mark.anyio
async def test_get_user_by_id(client: httpx.AsyncClient, org: Organization) -> None:
    email = f"dave-{uuid.uuid4().hex[:8]}@tracecat.com"
    created = (await _post_user(client, email)).json()

    response = await client.get(f"/scim/v2/Users/{created['id']}")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["userName"] == email


@pytest.mark.anyio
async def test_get_unknown_user_returns_scim_404(
    client: httpx.AsyncClient, org: Organization
) -> None:
    """A 404 body must be the SCIM error envelope, which the SPEC suite reads."""
    response = await client.get(f"/scim/v2/Users/{uuid.uuid4()}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.headers["content-type"].startswith(SCIM_CONTENT_TYPE)
    body = response.json()
    assert body["schemas"] == [ScimSchema.ERROR]
    assert body["status"] == "404"
    assert "detail" in body


@pytest.mark.anyio
async def test_malformed_body_returns_scim_error_envelope(
    client: httpx.AsyncClient, org: Organization
) -> None:
    """FastAPI's own validation shape must not leak to the provider."""
    response = await client.post("/scim/v2/Users", json={"active": True})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body["schemas"] == [ScimSchema.ERROR]
    assert body["status"] == "400"
    assert body["scimType"] == "invalidValue"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "attribute",
    ["active", f"{ScimSchema.USER}:active", f"{ScimSchema.USER}:active".upper()],
)
@pytest.mark.parametrize("object_value", [False, True])
async def test_patch_active_false_deprovisions(
    client: httpx.AsyncClient,
    session: AsyncSession,
    org: Organization,
    attribute: str,
    object_value: bool,
) -> None:
    """``active=false`` revokes access to this tenant."""
    email = f"erin-{uuid.uuid4().hex[:8]}@tracecat.com"
    created = (await _post_user(client, email)).json()
    user_id = uuid.UUID(created["id"])
    assert await _resource_is_member(
        session, resource_id=user_id, organization_id=org.id
    )

    response = await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "value": {attribute: False}}
                if object_value
                else {"op": "replace", "path": attribute, "value": False}
            ],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["active"] is False
    assert not await _resource_is_member(
        session, resource_id=user_id, organization_id=org.id
    )


@pytest.mark.anyio
async def test_patch_azure_shape_deprovisions(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    """Azure sends no path and a dictionary body; Okta sends a path."""
    email = f"frank-{uuid.uuid4().hex[:8]}@tracecat.com"
    created = (await _post_user(client, email)).json()
    user_id = uuid.UUID(created["id"])

    response = await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "Replace", "value": {"active": False}}],
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert not await _resource_is_member(
        session, resource_id=user_id, organization_id=org.id
    )


@pytest.mark.anyio
async def test_put_active_false_deprovisions(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    email = f"grace-{uuid.uuid4().hex[:8]}@tracecat.com"
    created = (await _post_user(client, email)).json()
    user_id = uuid.UUID(created["id"])

    response = await client.put(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": email,
            "active": False,
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert not await _resource_is_member(
        session, resource_id=user_id, organization_id=org.id
    )


@pytest.mark.anyio
async def test_delete_user_deprovisions(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    email = f"heidi-{uuid.uuid4().hex[:8]}@tracecat.com"
    created = (await _post_user(client, email)).json()
    user_id = uuid.UUID(created["id"])

    response = await client.delete(f"/scim/v2/Users/{user_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not await _resource_is_member(
        session, resource_id=user_id, organization_id=org.id
    )


@pytest.mark.anyio
async def test_delete_already_removed_user_is_204(
    client: httpx.AsyncClient, org: Organization
) -> None:
    """Deprovisioning is idempotent to the provider, which retries."""
    email = f"ivan-{uuid.uuid4().hex[:8]}@tracecat.com"
    created = (await _post_user(client, email)).json()

    first = await client.delete(f"/scim/v2/Users/{created['id']}")
    second = await client.delete(f"/scim/v2/Users/{created['id']}")

    assert first.status_code == status.HTTP_204_NO_CONTENT
    assert second.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_delete_unknown_user_is_204(
    client: httpx.AsyncClient, org: Organization
) -> None:
    """A user this tenant never had is already in the desired state."""
    response = await client.delete(f"/scim/v2/Users/{uuid.uuid4()}")

    assert response.status_code == status.HTTP_204_NO_CONTENT


# =============================================================================
# Groups
# =============================================================================


@pytest.mark.anyio
@pytest.mark.parametrize("rename_method", ["post", "patch"])
async def test_group_lifecycle(
    client: httpx.AsyncClient, session: AsyncSession, rename_method: str
) -> None:
    """Create, read, rename, and delete a synced group."""
    created = await client.post(
        "/scim/v2/Groups",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Engineering",
            "externalId": "idp-eng",
        },
    )
    assert created.status_code == status.HTTP_201_CREATED
    group_id = created.json()["id"]
    assert created.json()["displayName"] == "Engineering"

    fetched = await client.get(f"/scim/v2/Groups/{group_id}")
    assert fetched.status_code == status.HTTP_200_OK

    old_modified = datetime(2000, 1, 1, tzinfo=UTC)
    await session.execute(
        update(ExternalGroup)
        .where(ExternalGroup.id == uuid.UUID(group_id))
        .values(updated_at=old_modified)
    )
    if rename_method == "post":
        renamed = await client.post(
            "/scim/v2/Groups",
            json={"externalId": "idp-eng", "displayName": "Platform"},
        )
        assert renamed.status_code == status.HTTP_201_CREATED
    else:
        renamed = await client.patch(
            f"/scim/v2/Groups/{group_id}",
            json={
                "Operations": [
                    {"op": "replace", "path": "displayName", "value": "Platform"}
                ]
            },
        )
        assert renamed.status_code == status.HTTP_200_OK
    assert renamed.json()["id"] == group_id
    assert renamed.json()["displayName"] == "Platform"
    assert renamed.json()["meta"]["created"] == created.json()["meta"]["created"]
    assert datetime.fromisoformat(renamed.json()["meta"]["lastModified"]) > old_modified
    fetched = await client.get(f"/scim/v2/Groups/{group_id}")
    assert fetched.json()["displayName"] == "Platform"
    assert fetched.json()["meta"] == renamed.json()["meta"]

    listed = await client.get("/scim/v2/Groups")
    assert listed.json()["totalResults"] == 1

    deleted = await client.delete(f"/scim/v2/Groups/{group_id}")
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert (await client.get(f"/scim/v2/Groups/{group_id}")).status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize(
    "prefix", ["", f"{ScimSchema.GROUP}:", f"{ScimSchema.GROUP}:".upper()]
)
async def test_group_membership_projects_into_tracecat_group(
    client: httpx.AsyncClient,
    session: AsyncSession,
    org: Organization,
    scim_role: Role,
    prefix: str,
) -> None:
    """A mapped IdP group grants membership, and removal revokes it."""
    email = f"judy-{uuid.uuid4().hex[:8]}@tracecat.com"
    user_id = uuid.UUID((await _post_user(client, email)).json()["id"])

    created = await client.post(
        "/scim/v2/Groups",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Mapped",
            "externalId": "idp-mapped",
        },
    )
    external_group_id = uuid.UUID(created.json()["id"])

    group = Group(id=uuid.uuid4(), name="tc-group", organization_id=org.id)
    session.add(group)
    await session.flush()
    # An admin authors the mapping; the connection role cannot, so this seeds
    # it with the org scopes rather than the token's.
    admin_role = scim_role.model_copy(update={"scopes": frozenset({"org:scim:manage"})})
    await SCIMService(session, admin_role).create_mapping(
        external_group_id=external_group_id, group_id=group.id
    )
    await session.flush()
    old_modified = datetime(2000, 1, 1, tzinfo=UTC)
    age_group = (
        update(ExternalGroup)
        .where(ExternalGroup.id == external_group_id)
        .values(updated_at=old_modified)
    )
    await session.execute(age_group)

    added = await client.patch(
        f"/scim/v2/Groups/{external_group_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {
                    "op": "add",
                    "path": f"{prefix}members",
                    "value": [{"value": str(user_id)}],
                }
            ],
        },
    )
    assert added.status_code == status.HTTP_200_OK
    assert await _idp_member_count(session, group.id, user_id) == 1
    assert datetime.fromisoformat(added.json()["meta"]["lastModified"]) > old_modified

    await session.execute(age_group)

    removed = await client.patch(
        f"/scim/v2/Groups/{external_group_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {
                    "op": "remove",
                    "path": f"{prefix}members",
                    "value": [{"value": str(user_id)}],
                }
            ],
        },
    )
    assert removed.status_code == status.HTTP_200_OK
    assert await _idp_member_count(session, group.id, user_id) == 0
    assert datetime.fromisoformat(removed.json()["meta"]["lastModified"]) > old_modified
    fetched = await client.get(f"/scim/v2/Groups/{external_group_id}")
    assert fetched.json()["meta"] == removed.json()["meta"]


async def _idp_member_count(
    session: AsyncSession, group_id: uuid.UUID, resource_id: uuid.UUID
) -> int:
    """Count the live IdP paths from a mapped group to a SCIM resource."""
    count = await session.scalar(
        select(func.count())
        .select_from(ExternalGroupMember)
        .join(
            ExternalGroupMapping,
            ExternalGroupMapping.external_group_id
            == ExternalGroupMember.external_group_id,
        )
        .join(ExternalUser, ExternalUser.id == ExternalGroupMember.external_user_id)
        .where(
            ExternalGroupMapping.group_id == group_id,
            ExternalGroupMember.external_user_id == resource_id,
            ExternalUser.active,
        )
    )
    return count or 0


@pytest.mark.anyio
async def test_put_group_replaces_member_list(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    """PUT is a wholesale replacement of the provider's member list."""
    first = uuid.UUID(
        (await _post_user(client, f"k-{uuid.uuid4().hex[:8]}@tracecat.com")).json()[
            "id"
        ]
    )
    second = uuid.UUID(
        (await _post_user(client, f"l-{uuid.uuid4().hex[:8]}@tracecat.com")).json()[
            "id"
        ]
    )
    created = await client.post(
        "/scim/v2/Groups",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Replaceable",
            "externalId": "idp-replace",
            "members": [{"value": str(first)}],
        },
    )
    group_id = created.json()["id"]

    assert created.json()["meta"]["created"] == created.json()["meta"]["lastModified"]
    old_modified = datetime(2000, 1, 1, tzinfo=UTC)
    await session.execute(
        update(ExternalGroup)
        .where(ExternalGroup.id == uuid.UUID(group_id))
        .values(updated_at=old_modified)
    )

    replaced = await client.put(
        f"/scim/v2/Groups/{group_id}",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Replaceable",
            "members": [{"value": str(second)}],
        },
    )

    assert replaced.status_code == status.HTTP_200_OK
    values = {m["value"] for m in replaced.json()["members"]}
    assert values == {str(second)}
    assert (
        datetime.fromisoformat(replaced.json()["meta"]["lastModified"]) > old_modified
    )
    fetched = await client.get(f"/scim/v2/Groups/{group_id}")
    assert fetched.json()["meta"] == replaced.json()["meta"]


@pytest.mark.anyio
async def test_unknown_group_returns_scim_404(
    client: httpx.AsyncClient, org: Organization
) -> None:
    response = await client.get(f"/scim/v2/Groups/{uuid.uuid4()}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["schemas"] == [ScimSchema.ERROR]


# =============================================================================
# Tenancy
# =============================================================================


@pytest.mark.anyio
async def test_other_tenants_user_is_not_visible(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    """The linkage is per-tenant, so another org's user is simply not there."""
    other_org = Organization(
        id=uuid.uuid4(), name="Other", slug=f"other-{uuid.uuid4().hex[:8]}"
    )
    outsider = User(
        id=uuid.uuid4(),
        email=f"outsider-{uuid.uuid4().hex[:8]}@tracecat.com",
        hashed_password="x",
    )
    session.add_all([other_org, outsider])
    await session.flush()
    await grant_org_membership(
        session, user_id=outsider.id, organization_id=other_org.id
    )
    session.add(
        ExternalUser(
            organization_id=other_org.id, user_id=outsider.id, external_id="idp-out"
        )
    )
    await session.flush()

    response = await client.get(f"/scim/v2/Users/{outsider.id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Discovery
# =============================================================================


@pytest.mark.anyio
async def test_service_provider_config(client: httpx.AsyncClient) -> None:
    """Advertise exactly what this surface implements."""
    response = await client.get("/scim/v2/ServiceProviderConfig")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["patch"]["supported"] is True
    assert body["bulk"]["supported"] is False
    assert body["filter"]["supported"] is True
    assert body["filter"]["maxResults"] > 0
    assert body["changePassword"]["supported"] is False
    assert body["sort"]["supported"] is False
    assert body["etag"]["supported"] is False
    assert body["authenticationSchemes"][0]["type"] == "oauthbearertoken"


@pytest.mark.anyio
async def test_resource_types(client: httpx.AsyncClient) -> None:
    response = await client.get("/scim/v2/ResourceTypes")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert {r["id"] for r in body["Resources"]} == {"User", "Group"}


@pytest.mark.anyio
async def test_schemas_document(client: httpx.AsyncClient) -> None:
    response = await client.get("/scim/v2/Schemas")

    assert response.status_code == status.HTTP_200_OK
    ids = {r["id"] for r in response.json()["Resources"]}
    assert "urn:ietf:params:scim:schemas:core:2.0:User" in ids
    assert "urn:ietf:params:scim:schemas:core:2.0:Group" in ids
    user_schema = next(r for r in response.json()["Resources"] if r["name"] == "User")
    username = next(a for a in user_schema["attributes"] if a["name"] == "userName")
    assert username["mutability"] == "readWrite"


def test_is_scim_path_only_matches_the_protocol_surface() -> None:
    """The envelope must not rewrite errors for the rest of the API."""

    class _URL:
        def __init__(self, path: str) -> None:
            self.path = path

    class _Request:
        def __init__(self, path: str) -> None:
            self.url = _URL(path)

    assert is_scim_path(_Request("/scim/v2/Users"))  # pyright: ignore[reportArgumentType]
    assert not is_scim_path(_Request("/organization/members"))  # pyright: ignore[reportArgumentType]
    assert not is_scim_path(_Request("/scim/connection"))  # pyright: ignore[reportArgumentType]


# =============================================================================
# Authentication
# =============================================================================


@pytest.fixture
async def unauthenticated_client(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    """Mount the router with real token verification in place."""
    app = FastAPI()
    app.include_router(router)

    async def override_session() -> AsyncSession:
        return session

    # Token verification opens its own session, which the override above does
    # not reach; without this it would query the developer's database.
    @asynccontextmanager
    async def _auth_session() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        "tracecat_ee.scim.credentials.get_async_session_auth_context_manager",
        _auth_session,
    )
    app.dependency_overrides[get_async_session] = override_session
    _install_handlers(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no_bearer"),
        pytest.param({"Authorization": "Bearer tc_scim_sk_dead_beef"}, id="unknown"),
        pytest.param({"Authorization": "Bearer not-a-token"}, id="malformed"),
    ],
)
@pytest.mark.anyio
async def test_requests_without_a_valid_token_are_rejected(
    unauthenticated_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    """Every protocol route is behind the connection token."""
    response = await unauthenticated_client.get("/scim/v2/Users", headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.anyio
async def test_rejection_body_is_a_scim_error(
    unauthenticated_client: httpx.AsyncClient,
) -> None:
    """Even an auth failure answers in the envelope on a SCIM path."""
    response = await unauthenticated_client.get("/scim/v2/Users")

    assert response.json()["schemas"] == [ScimSchema.ERROR]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "prefix", ["", f"{ScimSchema.GROUP}:", f"{ScimSchema.GROUP}:".upper()]
)
async def test_filtered_removal_preserves_peers(
    client: httpx.AsyncClient, org: Organization, prefix: str
) -> None:
    users = [
        (
            await client.post(
                "/scim/v2/Users", json={"userName": f"filter-{i}@example.com"}
            )
        ).json()["id"]
        for i in range(3)
    ]
    group = (
        await client.post(
            "/scim/v2/Groups",
            json={
                "displayName": "Filtered removal",
                "members": [{"value": uid} for uid in users],
            },
        )
    ).json()
    response = await client.patch(
        f"/scim/v2/Groups/{group['id']}",
        json={
            "Operations": [
                {"op": "remove", "path": f'{prefix}members[value eq "{users[1]}"]'}
            ]
        },
    )
    assert response.status_code == 200
    assert {member["value"] for member in response.json()["members"]} == {
        users[0],
        users[2],
    }


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_position", [0, 1, 2])
@pytest.mark.parametrize("invalid_kind", ["path", "reference", "shape", "schema"])
async def test_invalid_patch_is_atomic(
    client: httpx.AsyncClient,
    org: Organization,
    invalid_position: int,
    invalid_kind: str,
) -> None:
    users = [
        (
            await client.post(
                "/scim/v2/Users", json={"userName": f"atomic-{i}@example.com"}
            )
        ).json()["id"]
        for i in range(3)
    ]
    group = (
        await client.post(
            "/scim/v2/Groups",
            json={"displayName": "Atomic group", "members": [{"value": users[0]}]},
        )
    ).json()
    invalid = {
        "path": {"op": "replace", "path": "unsupported", "value": "bad"},
        "reference": {
            "op": "add",
            "path": "members",
            "value": [{"value": str(uuid.uuid4())}],
        },
        "shape": {"op": "replace", "path": "members", "value": [{}]},
        "schema": {"op": "remove", "path": "urn:example:unsupported:members"},
    }[invalid_kind]
    operations = [
        {"op": "add", "path": "members", "value": [{"value": uid}]} for uid in users[1:]
    ]
    operations.insert(invalid_position, invalid)
    response = await client.patch(
        f"/scim/v2/Groups/{group['id']}", json={"Operations": operations}
    )
    assert response.status_code == 400
    current = (await client.get(f"/scim/v2/Groups/{group['id']}")).json()
    assert current["members"] == group["members"]


@pytest.mark.anyio
async def test_schema_qualified_group_object_patch(client: httpx.AsyncClient) -> None:
    user = (await _post_user(client, "qualified@example.com")).json()
    group = (
        await client.post("/scim/v2/Groups", json={"displayName": "Original"})
    ).json()
    response = await client.patch(
        f"/scim/v2/Groups/{group['id']}",
        json={
            "Operations": [
                {
                    "op": "replace",
                    "value": {
                        f"{ScimSchema.GROUP}:displayName": "Updated",
                        f"{ScimSchema.GROUP}:members": [{"value": user["id"]}],
                    },
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["displayName"] == "Updated"
    assert [member["value"] for member in response.json()["members"]] == [user["id"]]


@pytest.mark.anyio
async def test_unknown_user_schema_cannot_deprovision(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    user = (await _post_user(client, "unknown-schema@example.com")).json()
    response = await client.patch(
        f"/scim/v2/Users/{user['id']}",
        json={
            "Operations": [
                {
                    "op": "replace",
                    "path": "urn:example:unsupported:active",
                    "value": False,
                }
            ]
        },
    )
    assert response.status_code == 400
    assert await _resource_is_member(
        session, resource_id=uuid.UUID(user["id"]), organization_id=org.id
    )


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_pending_deactivation_survives_activation(
    client: httpx.AsyncClient, org: Organization, session: AsyncSession, method: str
) -> None:
    connection = (
        await session.execute(
            select(ScimConnection).where(ScimConnection.organization_id == org.id)
        )
    ).scalar_one()
    connection.status = ScimConnectionStatus.PENDING
    existing = User(
        id=uuid.uuid4(),
        email=f"pending-deactivate-{uuid.uuid4().hex}@example.com",
        hashed_password="test",
    )
    session.add(existing)
    await session.flush()
    group = await grant_org_membership_via_group(
        session, user_id=existing.id, organization_id=org.id
    )
    token = AccessToken(token=uuid.uuid4().hex, user_id=existing.id)
    session.add(token)
    await session.commit()
    user = (
        await client.post("/scim/v2/Users", json={"userName": existing.email})
    ).json()
    payload = {
        "PUT": {"userName": existing.email, "active": False},
        "PATCH": {"Operations": [{"op": "replace", "path": "active", "value": False}]},
        "DELETE": None,
    }[method]
    response = await client.request(
        method, f"/scim/v2/Users/{user['id']}", json=payload
    )
    assert response.status_code == (204 if method == "DELETE" else 200)
    assert (await client.get(f"/scim/v2/Users/{user['id']}")).json()["active"] is False
    assert await _is_member(session, user_id=existing.id, organization_id=org.id)
    assert (
        await session.scalar(
            select(GroupMember.user_id).where(
                GroupMember.group_id == group.id, GroupMember.user_id == existing.id
            )
        )
        == existing.id
    )
    assert (
        await session.scalar(
            select(AccessToken.__table__.c.token).where(
                AccessToken.__table__.c.token == token.token
            )
        )
        == token.token
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=org.id,
        scopes=frozenset({"org:scim:manage"}),
    )
    await SCIMService(session, role).activate([])
    external = (
        await session.execute(
            select(ExternalUser).where(ExternalUser.id == uuid.UUID(user["id"]))
        )
    ).scalar_one()
    assert await session.get(OrganizationMembership, (external.user_id, org.id)) is None
    assert (
        await session.scalar(
            select(AccessToken.__table__.c.token).where(
                AccessToken.__table__.c.token == token.token
            )
        )
        is None
    )
    assert await session.get(User, existing.id) is not None


@pytest.mark.anyio
@pytest.mark.parametrize("resource", ["Users", "Groups"])
@pytest.mark.parametrize(
    "query,index",
    [("count=-1", 1), ("startIndex=0&count=0", 1), ("startIndex=-10&count=0", 1)],
)
async def test_pagination_normalizes_numeric_bounds(
    client: httpx.AsyncClient, org: Organization, resource: str, query: str, index: int
) -> None:
    response = await client.get(f"/scim/v2/{resource}?{query}")
    assert response.status_code == 200
    assert response.json()["startIndex"] == index
    assert response.json()["Resources"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("resource", ["Users", "Groups"])
async def test_creation_location_matches_readable_resource(
    client: httpx.AsyncClient,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_API_URL", "http://test")
    body = (
        {"userName": "location@example.com"}
        if resource == "Users"
        else {"displayName": "Location group"}
    )
    response = await client.post(f"/scim/v2/{resource}", json=body)
    assert response.status_code == 201
    location = response.headers["Location"]
    assert response.json()["meta"]["location"] == location
    read = await client.get(location)
    assert read.status_code == 200
    assert read.json()["id"] == response.json()["id"]


@pytest.mark.anyio
async def test_repeated_inactive_push_removes_restored_admission(
    client: httpx.AsyncClient, org: Organization, session: AsyncSession
) -> None:
    user = (
        await client.post(
            "/scim/v2/Users", json={"userName": "inactive-replay@example.com"}
        )
    ).json()
    path = f"/scim/v2/Users/{user['id']}"
    payload = {"Operations": [{"op": "replace", "path": "active", "value": False}]}
    assert (await client.patch(path, json=payload)).status_code == 200
    external = await session.get(ExternalUser, uuid.UUID(user["id"]))
    assert external is not None
    await ensure_member(session, org.id, external.user_id)
    await session.commit()
    assert (await client.patch(path, json=payload)).status_code == 200
    assert await session.get(OrganizationMembership, (external.user_id, org.id)) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "expression",
    [
        'displayName ne "Engineering"',
        'externalId eq "missing"',
        "displayName eq unquoted",
        'displayName eq "x" or displayName eq "y"',
        'displayName eq "unterminated',
        "",
    ],
)
async def test_group_filter_rejects_unsupported_grammar(
    client: httpx.AsyncClient, org: Organization, expression: str
) -> None:
    response = await client.get("/scim/v2/Groups", params={"filter": expression})
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidFilter"


@pytest.mark.anyio
async def test_group_pages_with_identical_names_are_stable(
    client: httpx.AsyncClient, org: Organization
) -> None:
    ids = []
    for i in range(3):
        response = await client.post(
            "/scim/v2/Groups",
            json={"displayName": 'Shared "name"', "externalId": f"page-{i}"},
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    pages = []
    for index in range(1, 4):
        response = await client.get(
            "/scim/v2/Groups",
            params={
                "count": 1,
                "startIndex": index,
                "filter": 'displayName eq "Shared \\"name\\""',
            },
        )
        assert response.status_code == 200
        pages.append(response.json()["Resources"][0]["id"])
    assert pages == sorted(ids)


@pytest.mark.anyio
async def test_group_list_batches_memberships(
    client: httpx.AsyncClient, org: Organization, session: AsyncSession
) -> None:
    users = [
        (await _post_user(client, f"batch-{i}@tracecat.com")).json()["id"]
        for i in range(2)
    ]
    groups = [
        ExternalGroup(
            id=uuid.uuid4(),
            organization_id=org.id,
            external_id=f"batch-{i}",
            display_name=f"Batch {i:03}",
        )
        for i in range(200)
    ]
    session.add_all(groups)
    await session.flush()
    for i in range(2):
        session.add(
            ExternalGroupMember(
                organization_id=org.id,
                external_group_id=groups[i].id,
                external_user_id=uuid.UUID(users[i]),
            )
        )
    await session.commit()
    queries: list[str] = []

    def capture(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        response = await client.get("/scim/v2/Groups", params={"count": 200})
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert response.status_code == 200
    resources = response.json()["Resources"]
    assert len(resources) == 200
    assert len(queries) == 3
    assert [m["value"] for m in resources[0]["members"]] == [users[0]]
    assert [m["value"] for m in resources[1]["members"]] == [users[1]]
    assert all(not group["members"] for group in resources[2:])


@pytest.mark.anyio
async def test_put_group_updates_identifier_without_replacing_resource(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    created = await client.post(
        "/scim/v2/Groups",
        json={"displayName": "Original", "externalId": "old-identifier", "members": []},
    )
    assert created.status_code == 201
    resource_id = created.json()["id"]
    for _ in range(2):
        updated = await client.put(
            f"/scim/v2/Groups/{resource_id}",
            json={
                "displayName": "Renamed",
                "externalId": "new-identifier",
                "members": [],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["id"] == resource_id
        assert updated.json()["externalId"] == "new-identifier"
    stored = await client.get(f"/scim/v2/Groups/{resource_id}")
    assert stored.json()["externalId"] == "new-identifier"
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ExternalGroup)
            .where(ExternalGroup.organization_id == org.id)
        )
        == 1
    )
    await client.post(
        "/scim/v2/Groups",
        json={"displayName": "Other", "externalId": "occupied", "members": []},
    )
    rejected = await client.put(
        f"/scim/v2/Groups/{resource_id}",
        json={
            "displayName": "Must not change",
            "externalId": "occupied",
            "members": [],
        },
    )
    assert rejected.status_code == 409
    unchanged = await client.get(f"/scim/v2/Groups/{resource_id}")
    assert unchanged.json()["externalId"] == "new-identifier"
    assert unchanged.json()["displayName"] == "Renamed"


@pytest.mark.anyio
async def test_put_user_updates_external_id_without_relinking(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    email = f"rename-{uuid.uuid4().hex}@example.com"
    created = await _post_user(client, email, externalId="original-user")
    resource_id = created.json()["id"]
    linked_user_id = await session.scalar(
        select(ExternalUser.user_id).where(ExternalUser.id == uuid.UUID(resource_id))
    )
    for _ in range(2):
        updated = await client.put(
            f"/scim/v2/Users/{resource_id}",
            json={"userName": email, "externalId": "renamed-user", "active": True},
        )
        assert updated.status_code == 200
        assert updated.json()["externalId"] == "renamed-user"
        assert updated.json()["id"] == resource_id
    assert (await client.get(f"/scim/v2/Users/{resource_id}")).json()[
        "externalId"
    ] == "renamed-user"
    assert (
        await session.scalar(
            select(ExternalUser.user_id).where(
                ExternalUser.id == uuid.UUID(resource_id)
            )
        )
        == linked_user_id
    )
    await _post_user(
        client, f"other-{uuid.uuid4().hex}@example.com", externalId="occupied-user"
    )
    conflict = await client.put(
        f"/scim/v2/Users/{resource_id}",
        json={"userName": email, "externalId": "occupied-user", "active": False},
    )
    assert conflict.status_code == 409
    unchanged = (await client.get(f"/scim/v2/Users/{resource_id}")).json()
    assert unchanged["externalId"] == "renamed-user"
    assert unchanged["active"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["PUT", "PATCH"])
async def test_user_rename_updates_the_account_email(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization, method: str
) -> None:
    """The provider owns logins at the organization's domains, so a rename sticks."""
    email = f"original-{uuid.uuid4().hex}@example.com"
    renamed = f"Renamed-{uuid.uuid4().hex}@tracecat.com"
    created = await _post_user(client, email)
    resource_id = created.json()["id"]
    before = (await client.get(f"/scim/v2/Users/{resource_id}")).json()
    payload = (
        {
            "userName": renamed,
            "emails": [{"value": renamed, "primary": True}],
            "active": True,
        }
        if method == "PUT"
        else {
            "Operations": [
                {"op": "replace", "path": "userName", "value": renamed},
                {
                    "op": "replace",
                    "path": 'emails[type eq "work"].value',
                    "value": renamed,
                },
            ]
        }
    )
    response = await client.request(
        method, f"/scim/v2/Users/{resource_id}", json=payload
    )
    assert response.status_code == 200
    assert response.json()["id"] == resource_id
    assert response.json()["userName"] == renamed.lower()
    assert before["userName"] == email
    user = (
        await session.execute(
            select(User).where(func.lower(User.email) == renamed.lower())
        )
    ).scalar_one()
    assert await _is_member(session, user_id=user.id, organization_id=org.id)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"userName": "renamed@outside.io"},
        {"emails": [{"value": "mismatch@example.com", "primary": True}]},
    ],
)
async def test_put_user_rejects_invalid_rename_without_partial_updates(
    client: httpx.AsyncClient,
    session: AsyncSession,
    org: Organization,
    changes: dict[str, Any],
) -> None:
    email = f"original-{uuid.uuid4().hex}@example.com"
    created = await _post_user(client, email, externalId="original-id")
    resource_id = created.json()["id"]
    response = await client.put(
        f"/scim/v2/Users/{resource_id}",
        json={
            "userName": email,
            "externalId": "changed-id",
            "active": False,
            **changes,
        },
    )
    assert response.status_code == 400
    assert response.json()["schemas"] == [ScimSchema.ERROR]
    assert response.json()["scimType"] == "invalidValue"
    stored = (await client.get(f"/scim/v2/Users/{resource_id}")).json()
    assert stored["userName"] == email
    assert stored["externalId"] == "original-id"
    assert stored["active"] is True
    assert await _resource_is_member(
        session, resource_id=uuid.UUID(resource_id), organization_id=org.id
    )


@pytest.mark.anyio
async def test_patch_group_rejects_oversized_display_name(
    client: httpx.AsyncClient,
) -> None:
    """PATCH is bounded like POST/PUT: 400 invalidValue, not a database 500."""
    created = await client.post(
        "/scim/v2/Groups", json={"externalId": "idp-long", "displayName": "Short"}
    )
    group_id = created.json()["id"]
    response = await client.patch(
        f"/scim/v2/Groups/{group_id}",
        json={
            "Operations": [{"op": "replace", "path": "displayName", "value": "x" * 256}]
        },
    )
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidValue"
    fetched = await client.get(f"/scim/v2/Groups/{group_id}")
    assert fetched.json()["displayName"] == "Short"


@pytest.mark.anyio
async def test_user_rename_conflicts_with_another_account(
    client: httpx.AsyncClient,
) -> None:
    email = f"original-{uuid.uuid4().hex}@example.com"
    taken = f"taken-{uuid.uuid4().hex}@example.com"
    await _post_user(client, taken, externalId="taken-id")
    created = await _post_user(client, email, externalId="original-id")
    resource_id = created.json()["id"]
    response = await client.put(
        f"/scim/v2/Users/{resource_id}",
        json={"userName": taken.upper(), "active": False},
    )
    assert response.status_code == 409
    stored = (await client.get(f"/scim/v2/Users/{resource_id}")).json()
    assert stored["userName"] == email
    assert stored["active"] is True


@pytest.mark.anyio
async def test_post_user_rejects_an_unowned_domain_without_linking(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    """An existing account outside the owned domains is never pulled in."""
    user = User(
        id=uuid.uuid4(),
        email=f"outsider-{uuid.uuid4().hex}@outside.io",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    response = await _post_user(client, user.email)
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidValue"
    linked = await session.scalar(
        select(ExternalUser.id).where(ExternalUser.user_id == user.id)
    )
    assert linked is None
    assert not await _is_member(session, user_id=user.id, organization_id=org.id)


@pytest.mark.anyio
@pytest.mark.parametrize("secondary", [{"primary": False}, {}])
async def test_put_user_ignores_secondary_emails(
    client: httpx.AsyncClient, secondary: dict[str, bool]
) -> None:
    email = f"original-{uuid.uuid4().hex}@example.com"
    emails = [
        {"value": email, "primary": True},
        {"value": "alias@example.com", **secondary},
    ]
    created = await _post_user(client, email, emails=emails)
    assert created.status_code == 201
    resource_id = created.json()["id"]
    replaced = await client.put(
        f"/scim/v2/Users/{resource_id}",
        json={"userName": email, "emails": emails},
    )
    assert replaced.status_code == 200
    assert replaced.json()["id"] == resource_id
    assert replaced.json()["userName"] == email
    assert replaced.json()["emails"] == created.json()["emails"]
    fetched = await client.get(f"/scim/v2/Users/{resource_id}")
    assert fetched.json()["emails"] == created.json()["emails"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation",
    [
        {"op": "replace", "path": "userName", "value": "renamed@outside.io"},
        {"op": "add", "value": {"userName": "renamed@outside.io"}},
        {"op": "remove", "path": "userName"},
        {
            "op": "replace",
            "path": "urn:ietf:params:scim:schemas:core:2.0:User:USERNAME",
            "value": 42,
        },
        {
            "op": "replace",
            "path": "emails",
            "value": [{"value": "mismatch@example.com", "primary": True}],
        },
        {
            "op": "replace",
            "value": {"emails": [{"value": "mismatch@example.com"}]},
        },
        {
            "op": "replace",
            "path": 'emails[type eq "work"].value',
            "value": "mismatch@example.com",
        },
        {"op": "remove", "path": "emails.value"},
    ],
)
async def test_patch_user_rejects_invalid_rename_without_deprovisioning(
    client: httpx.AsyncClient,
    session: AsyncSession,
    org: Organization,
    operation: dict[str, Any],
) -> None:
    email = f"original-{uuid.uuid4().hex}@example.com"
    created = await _post_user(client, email)
    resource_id = created.json()["id"]
    response = await client.patch(
        f"/scim/v2/Users/{resource_id}",
        json={
            "Operations": [
                {"op": "replace", "path": "active", "value": False},
                operation,
            ]
        },
    )
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidValue"
    stored = (await client.get(f"/scim/v2/Users/{resource_id}")).json()
    assert stored["userName"] == email
    assert stored["active"] is True
    assert await _resource_is_member(
        session, resource_id=uuid.UUID(resource_id), organization_id=org.id
    )


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["PUT", "PATCH"])
async def test_user_update_accepts_unchanged_username_case_insensitively(
    client: httpx.AsyncClient, method: str
) -> None:
    email = f"unchanged-{uuid.uuid4().hex}@example.com"
    created = await _post_user(client, email)
    payload = (
        {"userName": email.upper(), "emails": [{"value": email.upper()}]}
        if method == "PUT"
        else {"Operations": [{"op": "replace", "value": {"userName": email.upper()}}]}
    )
    response = await client.request(
        method, f"/scim/v2/Users/{created.json()['id']}", json=payload
    )
    assert response.status_code == 200
    assert response.json()["userName"] == email
