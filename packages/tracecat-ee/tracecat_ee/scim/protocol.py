"""The SCIM 2.0 protocol surface (EE).

Every route authenticates with a connection token, never a user session. The
provider is the source of truth for who exists and who is in which group; this
module translates its pushes onto ``ScimProvisioningService`` and
``SCIMService`` and returns spec-shaped resources.

Responses carry ``application/scim+json`` and every failure is rendered as the
SCIM error envelope, including FastAPI's own validation failures, which the
SPEC suite checks.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import orjson
from fastapi import APIRouter, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from tracecat.auth.types import Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import ExternalGroup, ExternalGroupMember, ExternalUser, User
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat_ee.scim.credentials import ScimConnectionRole
from tracecat_ee.scim.provisioning import ScimProvisioningService
from tracecat_ee.scim.schemas import (
    ERROR_SCHEMA,
    GROUP_SCHEMA,
    RESOURCE_TYPE_SCHEMA,
    SCIM_CONTENT_TYPE,
    SERVICE_PROVIDER_CONFIG_SCHEMA,
    USER_SCHEMA,
    ScimCount,
    ScimEmail,
    ScimError,
    ScimGroupMemberRef,
    ScimGroupRequest,
    ScimGroupResource,
    ScimListResponse,
    ScimMeta,
    ScimPatchOp,
    ScimStartIndex,
    ScimUserRequest,
    ScimUserResource,
)
from tracecat_ee.scim.service import SCIMService

SCIM_PREFIX = "/scim/v2"
MAX_FILTER_RESULTS = 200


class ScimJSONResponse(ORJSONResponse):
    """A JSON response tagged with the media type the specification requires."""

    media_type = SCIM_CONTENT_TYPE


router = APIRouter(
    prefix=SCIM_PREFIX,
    tags=["scim"],
    default_response_class=ScimJSONResponse,
)


# =============================================================================
# Error envelope
# =============================================================================


def scim_error_response(
    *, status_code: int, detail: str, scim_type: str | None = None
) -> ScimJSONResponse:
    """Render a SCIM error envelope."""
    error = ScimError.build(status_code=status_code, detail=detail, scim_type=scim_type)
    return ScimJSONResponse(
        status_code=status_code,
        content=error.model_dump(by_alias=True, exclude_none=True),
    )


def is_scim_path(request: Request) -> bool:
    """Whether a request targets the SCIM protocol surface."""
    return request.url.path.startswith(SCIM_PREFIX)


def scim_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """Rewrite an HTTP error on a SCIM path into the SCIM envelope."""
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return scim_error_response(status_code=exc.status_code, detail=detail)


def scim_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    """Rewrite FastAPI's 422 body into the SCIM envelope.

    The provider's SPEC suite reads error bodies, and FastAPI's default shape
    is not a SCIM error. SCIM calls a malformed payload 400 invalidValue, not
    422.
    """
    detail = orjson.dumps(exc.errors(), default=str).decode()
    return scim_error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=detail,
        scim_type="invalidValue",
    )


# =============================================================================
# Resource rendering
# =============================================================================


def _user_resource(
    external_user: ExternalUser, user: User, *, active: bool | None = None
) -> ScimUserResource:
    """Render a provisioned user. The resource id is the external_user row."""
    return ScimUserResource(
        id=str(external_user.id),
        userName=user.email,
        externalId=external_user.external_id,
        active=external_user.active if active is None else active,
        emails=[ScimEmail(value=user.email, primary=True, type="work")],
        meta=ScimMeta(
            resourceType="User",
            location=f"{SCIM_PREFIX}/Users/{external_user.id}",
        ),
    )


def _group_resource(
    group: ExternalGroup, *, members: list[ScimGroupMemberRef]
) -> ScimGroupResource:
    return ScimGroupResource(
        id=str(group.id),
        displayName=group.display_name,
        externalId=group.external_id,
        members=members,
        meta=ScimMeta(
            resourceType="Group",
            created=group.created_at,
            lastModified=group.updated_at,
            location=f"{SCIM_PREFIX}/Groups/{group.id}",
        ),
    )


def _list_response(
    resources: list[Any], *, total: int, start_index: int
) -> ScimListResponse:
    return ScimListResponse(
        totalResults=total,
        startIndex=start_index,
        itemsPerPage=len(resources),
        Resources=[r.model_dump(by_alias=True, exclude_none=True) for r in resources],
    )


def _parse_username_filter(filter_expr: str | None) -> str | None:
    """Extract the value from ``userName eq "..."``.

    Okta queries before creating, and this is the only filter it needs. Any
    other expression is unsupported rather than silently ignored.
    """
    if not filter_expr:
        return None
    parts = filter_expr.strip().split(None, 2)
    if len(parts) != 3 or parts[0].lower() != "username" or parts[1].lower() != "eq":
        raise TracecatValidationError(f"Unsupported filter: {filter_expr}")
    return parts[2].strip().strip('"').strip("'").lower()


# =============================================================================
# Users
# =============================================================================


async def _linked_user(
    session: AsyncSession, *, organization_id: UUID, resource_id: UUID
) -> tuple[ExternalUser, User]:
    """Fetch a provisioned user by its SCIM resource id.

    Raises:
        TracecatNotFoundError: The resource is not in this organization.
    """
    stmt = (
        select(ExternalUser, User)
        .join(User, User.id == ExternalUser.user_id)  # pyright: ignore[reportArgumentType]
        .where(
            ExternalUser.id == resource_id,
            ExternalUser.organization_id == organization_id,
        )
    )
    row = (await session.execute(stmt)).tuples().one_or_none()
    if row is None:
        raise TracecatNotFoundError("User not found")
    return row


@router.get("/Users")
async def list_users(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    filter: str | None = Query(default=None),
    startIndex: ScimStartIndex = Query(default=1),
    count: ScimCount = Query(default=100),
) -> ScimListResponse:
    """List provisioned users, optionally filtered by ``userName``.

    The filter matches case-insensitively: Okta queries with whatever casing
    the directory holds, and a case-sensitive match would make it create a
    duplicate.
    """
    organization_id = _organization_id(role)
    username = _parse_username_filter(filter)

    conditions = [ExternalUser.organization_id == organization_id]
    if username is not None:
        conditions.append(func.lower(User.email) == username)

    base = select(ExternalUser, User).join(User, User.id == ExternalUser.user_id)  # pyright: ignore[reportArgumentType]
    total = await session.scalar(
        select(func.count())
        .select_from(ExternalUser)
        .join(User, User.id == ExternalUser.user_id)  # pyright: ignore[reportArgumentType]
        .where(*conditions)
    )
    rows = (
        (
            await session.execute(
                base.where(*conditions)
                .order_by(User.email)
                .offset(startIndex - 1)
                .limit(count)
            )
        )
        .tuples()
        .all()
    )
    resources = [_user_resource(external_user, user) for external_user, user in rows]
    return _list_response(resources, total=total or 0, start_index=startIndex)


@router.post("/Users", status_code=status.HTTP_201_CREATED)
async def create_user(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    params: ScimUserRequest,
    response: Response,
) -> ScimUserResource:
    """Provision a user, linking an existing account rather than conflicting."""
    service = ScimProvisioningService(session, role=role)
    provisioned = await service.provision_user(
        external_id=params.external_id or params.user_name,
        email=params.user_name,
        active=params.active,
    )
    await session.commit()
    if not provisioned.created:
        # The account already existed and is now linked. A 409 here is what
        # makes Entra give up, so the link is reported as a normal creation.
        response.status_code = status.HTTP_201_CREATED
    return _user_resource(provisioned.external_user, provisioned.user)


@router.get("/Users/{resource_id}")
async def get_user(
    *, role: ScimConnectionRole, session: AsyncDBSession, resource_id: UUID
) -> ScimUserResource:
    """Read one provisioned user."""
    external_user, user = await _linked_user(
        session, organization_id=_organization_id(role), resource_id=resource_id
    )
    return _user_resource(external_user, user)


@router.put("/Users/{resource_id}")
async def replace_user(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    resource_id: UUID,
    params: ScimUserRequest,
) -> ScimUserResource:
    """Replace a user resource. Only ``active`` changes anything in Tracecat."""
    organization_id = _organization_id(role)
    external_user, user = await _linked_user(
        session, organization_id=organization_id, resource_id=resource_id
    )
    await _apply_active(
        session, role=role, external_user=external_user, active=params.active
    )
    return _user_resource(external_user, user, active=params.active)


@router.patch("/Users/{resource_id}")
async def patch_user(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    resource_id: UUID,
    params: ScimPatchOp,
) -> ScimUserResource:
    """Apply a PatchOp. ``active`` is the operation that matters."""
    organization_id = _organization_id(role)
    external_user, user = await _linked_user(
        session, organization_id=organization_id, resource_id=resource_id
    )

    active = external_user.active
    for operation in params.operations:
        if (value := _patch_active_value(operation.path, operation.value)) is not None:
            active = value

    await _apply_active(session, role=role, external_user=external_user, active=active)
    return _user_resource(external_user, user, active=active)


def _patch_active_value(path: str | None, value: Any) -> bool | None:
    """Read ``active`` from a patch operation in either shape it arrives in.

    Okta sends ``path="active"`` with a scalar; Azure sends no path and a
    dictionary body.
    """
    if path is not None and path.strip().lower() == "active":
        return _coerce_bool(value)
    if path is None and isinstance(value, dict):
        for key, item in value.items():
            if key.lower() == "active":
                return _coerce_bool(item)
    return None


def _coerce_bool(value: Any) -> bool | None:
    """Providers send ``active`` as a boolean or as a string."""
    match value:
        case bool():
            return value
        case "true" | "True":
            return True
        case "false" | "False":
            return False
        case _:
            return None


async def _apply_active(
    session: AsyncSession, *, role: Role, external_user: ExternalUser, active: bool
) -> None:
    """Admit or deprovision the user, tolerating a repeat of either."""
    if active == external_user.active:
        return
    service = SCIMService(session, role)
    if active:
        await service.reactivate_external_user(external_user)
        await session.commit()
        return
    try:
        await service.deprovision_user(external_user.user_id)
    except NoResultFound:
        # Already gone. Deprovisioning is idempotent to the provider.
        await session.commit()


@router.delete("/Users/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    *, role: ScimConnectionRole, session: AsyncDBSession, resource_id: UUID
) -> Response:
    """Deprovision a user. Already-removed is success, not an error.

    The service keeps raising ``NoResultFound``; idempotency is a property of
    this transport, not of deprovisioning.
    """
    external_user, _ = await _linked_user(
        session, organization_id=_organization_id(role), resource_id=resource_id
    )
    await _apply_active(session, role=role, external_user=external_user, active=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Groups
# =============================================================================


async def _group_members(
    session: AsyncSession, external_group_id: UUID
) -> list[ScimGroupMemberRef]:
    # The member ref is the /Users resource id, which is external_user.id.
    stmt = (
        select(ExternalGroupMember.external_user_id, User.email)  # pyright: ignore[reportArgumentType, reportCallIssue]
        .join(ExternalUser, ExternalUser.id == ExternalGroupMember.external_user_id)
        .join(User, User.id == ExternalUser.user_id)  # pyright: ignore[reportArgumentType]
        .where(ExternalGroupMember.external_group_id == external_group_id)
        .order_by(User.email)
    )
    rows = (await session.execute(stmt)).tuples().all()
    return [
        ScimGroupMemberRef(value=str(external_user_id), display=email)
        for external_user_id, email in rows
    ]


async def _get_group(
    session: AsyncSession, *, organization_id: UUID, group_id: UUID
) -> ExternalGroup:
    stmt = select(ExternalGroup).where(
        ExternalGroup.id == group_id,
        ExternalGroup.organization_id == organization_id,
    )
    group = (await session.execute(stmt)).scalar_one_or_none()
    if group is None:
        raise TracecatNotFoundError("Group not found")
    return group


async def _resolve_member_ids(
    session: AsyncSession, *, organization_id: UUID, members: list[ScimGroupMemberRef]
) -> list[UUID]:
    """Keep only members this organization's provider actually owns.

    A reference to a user outside the tenant is dropped rather than failing the
    whole push: the provider often sends members before provisioning them.
    """
    candidates: list[UUID] = []
    for member in members:
        try:
            candidates.append(UUID(member.value))
        except ValueError:
            continue
    if not candidates:
        return []
    stmt = select(ExternalUser.user_id).where(
        ExternalUser.organization_id == organization_id,
        ExternalUser.user_id.in_(candidates),
    )
    return list((await session.execute(stmt)).scalars())


@router.get("/Groups")
async def list_groups(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    filter: str | None = Query(default=None),
    startIndex: ScimStartIndex = Query(default=1),
    count: ScimCount = Query(default=100),
) -> ScimListResponse:
    """List synced external groups."""
    organization_id = _organization_id(role)
    conditions = [ExternalGroup.organization_id == organization_id]
    if filter:
        parts = filter.strip().split(None, 2)
        if len(parts) == 3 and parts[0].lower() == "displayname":
            wanted = parts[2].strip().strip('"').strip("'").lower()
            conditions.append(func.lower(ExternalGroup.display_name) == wanted)

    total = await session.scalar(
        select(func.count()).select_from(ExternalGroup).where(*conditions)
    )
    groups = list(
        (
            await session.execute(
                select(ExternalGroup)
                .where(*conditions)
                .order_by(ExternalGroup.display_name)
                .offset(startIndex - 1)
                .limit(count)
            )
        ).scalars()
    )
    resources = [
        _group_resource(g, members=await _group_members(session, g.id)) for g in groups
    ]
    return _list_response(resources, total=total or 0, start_index=startIndex)


@router.post("/Groups", status_code=status.HTTP_201_CREATED)
async def create_group(
    *, role: ScimConnectionRole, session: AsyncDBSession, params: ScimGroupRequest
) -> ScimGroupResource:
    """Create or rename a synced external group and set its members."""
    organization_id = _organization_id(role)
    service = SCIMService(session, role)
    group = await service.upsert_external_group(
        external_id=params.external_id or params.display_name,
        display_name=params.display_name,
    )
    await session.flush()
    if params.members is not None:
        user_ids = await _resolve_member_ids(
            session, organization_id=organization_id, members=params.members
        )
        await service.replace_external_group_members(group.id, user_ids)
    await session.commit()
    return _group_resource(group, members=await _group_members(session, group.id))


@router.get("/Groups/{group_id}")
async def get_group(
    *, role: ScimConnectionRole, session: AsyncDBSession, group_id: UUID
) -> ScimGroupResource:
    """Read one synced external group."""
    group = await _get_group(
        session, organization_id=_organization_id(role), group_id=group_id
    )
    return _group_resource(group, members=await _group_members(session, group.id))


@router.put("/Groups/{group_id}")
async def replace_group(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    group_id: UUID,
    params: ScimGroupRequest,
) -> ScimGroupResource:
    """Replace a group's name and its complete member list."""
    organization_id = _organization_id(role)
    group = await _get_group(
        session, organization_id=organization_id, group_id=group_id
    )
    service = SCIMService(session, role)
    await service.upsert_external_group(
        external_id=group.external_id, display_name=params.display_name
    )
    user_ids = await _resolve_member_ids(
        session, organization_id=organization_id, members=params.members or []
    )
    await service.replace_external_group_members(group.id, user_ids)
    await session.commit()
    return _group_resource(group, members=await _group_members(session, group.id))


@router.patch("/Groups/{group_id}")
async def patch_group(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    group_id: UUID,
    params: ScimPatchOp,
) -> ScimGroupResource:
    """Add or remove members, or rename the group.

    The projection reconciles against a complete member list, so each operation
    is folded into the current set and the result replaces it wholesale.
    """
    organization_id = _organization_id(role)
    group = await _get_group(
        session, organization_id=organization_id, group_id=group_id
    )
    service = SCIMService(session, role)

    current = {UUID(m.value) for m in await _group_members(session, group.id)}
    display_name = group.display_name
    members_changed = False

    for operation in params.operations:
        path = (operation.path or "").strip().lower()
        if path.startswith("members"):
            members_changed = True
            refs = _member_refs(operation.value)
            resolved = set(
                await _resolve_member_ids(
                    session, organization_id=organization_id, members=refs
                )
            )
            match operation.op:
                case "add":
                    current |= resolved
                case "remove":
                    # A bare "members" remove with no value clears the group.
                    current = current - resolved if refs else set()
                case "replace":
                    current = resolved
        elif isinstance(operation.value, dict):
            for key, item in operation.value.items():
                if key.lower() == "displayname" and isinstance(item, str):
                    display_name = item
        elif path == "displayname" and isinstance(operation.value, str):
            display_name = operation.value

    if display_name != group.display_name:
        await service.upsert_external_group(
            external_id=group.external_id, display_name=display_name
        )
    if members_changed:
        await service.replace_external_group_members(group.id, sorted(current, key=str))
    await session.commit()
    await session.refresh(group)
    return _group_resource(group, members=await _group_members(session, group.id))


def _member_refs(value: Any) -> list[ScimGroupMemberRef]:
    """Read member references from either shape a provider sends."""
    match value:
        case list():
            return [
                ScimGroupMemberRef(value=str(v["value"]), display=v.get("display"))
                for v in value
                if isinstance(v, dict) and v.get("value")
            ]
        case {"value": member_value}:
            return [ScimGroupMemberRef(value=str(member_value))]
        case _:
            return []


@router.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    *, role: ScimConnectionRole, session: AsyncDBSession, group_id: UUID
) -> Response:
    """Delete a synced group and drop the membership it supplied."""
    organization_id = _organization_id(role)
    await _get_group(session, organization_id=organization_id, group_id=group_id)
    await SCIMService(session, role).delete_external_group(group_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Discovery
# =============================================================================


@router.get("/ServiceProviderConfig")
async def service_provider_config() -> dict[str, Any]:
    """Advertise the subset of the specification this surface implements."""
    return {
        "schemas": [SERVICE_PROVIDER_CONFIG_SCHEMA],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_FILTER_RESULTS},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "Authentication using the Tracecat SCIM connection token.",
                "primary": True,
            }
        ],
        "meta": {"resourceType": "ServiceProviderConfig"},
    }


@router.get("/ResourceTypes")
async def resource_types() -> ScimListResponse:
    """List the resource types this surface serves."""
    resources: list[dict[str, Any]] = [
        {
            "schemas": [RESOURCE_TYPE_SCHEMA],
            "id": "User",
            "name": "User",
            "endpoint": "/Users",
            "description": "SCIM User",
            "schema": USER_SCHEMA,
            "meta": {"resourceType": "ResourceType"},
        },
        {
            "schemas": [RESOURCE_TYPE_SCHEMA],
            "id": "Group",
            "name": "Group",
            "endpoint": "/Groups",
            "description": "SCIM Group",
            "schema": GROUP_SCHEMA,
            "meta": {"resourceType": "ResourceType"},
        },
    ]
    return ScimListResponse(
        totalResults=len(resources),
        startIndex=1,
        itemsPerPage=len(resources),
        Resources=resources,
    )


@router.get("/Schemas")
async def schemas_document() -> ScimListResponse:
    """List the resource schemas this surface understands."""
    resources: list[dict[str, Any]] = [
        {
            "id": USER_SCHEMA,
            "name": "User",
            "description": "SCIM core User",
            "attributes": [
                {
                    "name": "userName",
                    "type": "string",
                    "required": True,
                    "uniqueness": "server",
                    "caseExact": False,
                    "multiValued": False,
                },
                {
                    "name": "active",
                    "type": "boolean",
                    "required": False,
                    "multiValued": False,
                },
            ],
            "meta": {"resourceType": "Schema"},
        },
        {
            "id": GROUP_SCHEMA,
            "name": "Group",
            "description": "SCIM core Group",
            "attributes": [
                {
                    "name": "displayName",
                    "type": "string",
                    "required": True,
                    "multiValued": False,
                },
                {
                    "name": "members",
                    "type": "complex",
                    "required": False,
                    "multiValued": True,
                },
            ],
            "meta": {"resourceType": "Schema"},
        },
    ]
    return ScimListResponse(
        totalResults=len(resources),
        startIndex=1,
        itemsPerPage=len(resources),
        Resources=resources,
    )


def _organization_id(role: Role) -> UUID:
    """The organization the connection token is bound to."""
    if role.organization_id is None:
        raise TracecatAuthorizationError("SCIM role requires organization context")
    return role.organization_id


__all__ = [
    "ERROR_SCHEMA",
    "ScimJSONResponse",
    "is_scim_path",
    "router",
    "scim_error_response",
    "scim_http_exception_handler",
    "scim_validation_exception_handler",
]
