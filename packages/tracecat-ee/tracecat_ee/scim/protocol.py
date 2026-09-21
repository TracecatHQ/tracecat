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

import json
import re
from typing import Any
from uuid import UUID

import orjson
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.membership import lock_role_changes
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
    ScimEmail,
    ScimError,
    ScimGroupMemberRef,
    ScimGroupRequest,
    ScimGroupResource,
    ScimListResponse,
    ScimMeta,
    ScimPatchOp,
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


class ScimFilterError(HTTPException):
    """An unsupported or malformed SCIM search filter."""


class ScimMutabilityError(HTTPException):
    """An attempt to change a SCIM user's login identity."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "SCIM username and email changes are not supported. "
                "Keep userName and emails unchanged for existing users."
            ),
        )


def scim_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """Rewrite an HTTP error on a SCIM path into the SCIM envelope."""
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    scim_type = None
    if isinstance(exc, ScimFilterError):
        scim_type = "invalidFilter"
    elif isinstance(exc, ScimMutabilityError):
        scim_type = "mutability"
    response = scim_error_response(
        status_code=exc.status_code,
        detail=detail,
        scim_type=scim_type,
    )
    response.headers.update(exc.headers or {})
    return response


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


def _resource_location(kind: str, resource_id: UUID) -> str:
    return f"{config.TRACECAT__PUBLIC_API_URL.rstrip('/')}{SCIM_PREFIX}/{kind}/{resource_id}"


def _user_resource(external_user: ExternalUser, user: User) -> ScimUserResource:
    """Render a provisioned user. The resource id is the external_user row."""
    return ScimUserResource(
        id=str(external_user.id),
        userName=user.email,
        externalId=external_user.external_id,
        active=external_user.active,
        emails=[ScimEmail(value=user.email, primary=True, type="work")],
        meta=ScimMeta(
            resourceType="User",
            location=_resource_location("Users", external_user.id),
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
            location=_resource_location("Groups", group.id),
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


def _parse_equality_filter(filter_expr: str | None, attribute: str) -> str | None:
    """Accept one equality expression with a JSON string value."""
    if filter_expr is None:
        return None
    match = re.fullmatch(
        rf'\s*{attribute}\s+eq\s+("(?:[^"\\]|\\.)*")\s*', filter_expr, re.IGNORECASE
    )
    if match is not None:
        try:
            return json.loads(match[1]).lower()
        except ValueError:
            pass
    raise ScimFilterError(
        status_code=400, detail=f"Expected {attribute} eq with a quoted string"
    )


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
    startIndex: int = Query(default=1),
    count: int = Query(default=100),
) -> ScimListResponse:
    """List provisioned users, optionally filtered by ``userName``.

    The filter matches case-insensitively: Okta queries with whatever casing
    the directory holds, and a case-sensitive match would make it create a
    duplicate.
    """
    startIndex = max(1, startIndex)
    count = min(200, max(0, count))
    organization_id = _organization_id(role)
    username = _parse_equality_filter(filter, "userName")

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
    response.headers["Location"] = _resource_location(
        "Users", provisioned.external_user.id
    )
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
    """Replace the provider identifier and active state of a user resource."""
    await lock_role_changes(session, _organization_id(role))
    organization_id = _organization_id(role)
    external_user, user = await _linked_user(
        session, organization_id=organization_id, resource_id=resource_id
    )
    if params.user_name.strip().lower() != user.email.lower() or any(
        email.value is not None and email.value.strip().lower() != user.email.lower()
        for email in params.emails
    ):
        raise ScimMutabilityError()
    await ScimProvisioningService(session, role).update_external_id(
        external_user, params.external_id
    )
    await _apply_active(
        session, role=role, external_user=external_user, active=params.active
    )
    await session.commit()
    return _user_resource(external_user, user)


@router.patch("/Users/{resource_id}")
async def patch_user(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    resource_id: UUID,
    params: ScimPatchOp,
) -> ScimUserResource:
    """Apply a PatchOp. ``active`` is the operation that matters."""
    await lock_role_changes(session, _organization_id(role))
    organization_id = _organization_id(role)
    external_user, user = await _linked_user(
        session, organization_id=organization_id, resource_id=resource_id
    )

    active = external_user.active
    for operation in params.operations:
        path = operation.path.strip().lower() if operation.path is not None else None
        values = operation.value if path is None else {path: operation.value}
        if not isinstance(values, dict) or not values:
            raise TracecatValidationError(
                "PATCH requires an attribute path or object value"
            )
        for key, value in values.items():
            attribute = key.strip().lower().removeprefix(f"{USER_SCHEMA.lower()}:")
            if (
                attribute == "username"
                and operation.op != "remove"
                and isinstance(value, str)
                and value.strip().lower() == user.email.lower()
            ):
                continue
            if re.match(r"^(username|emails)(?:$|[.\[])", attribute):
                raise ScimMutabilityError()
            if operation.op == "remove":
                raise TracecatValidationError("Removing user attributes is unsupported")
            if attribute != "active":
                raise TracecatValidationError("Unsupported user PATCH path")
            parsed = _coerce_bool(value)
            if parsed is None:
                raise TracecatValidationError("active must be a boolean")
            active = parsed

    await _apply_active(session, role=role, external_user=external_user, active=active)
    return _user_resource(external_user, user)


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
    service = SCIMService(session, role)
    if active:
        if not external_user.active:
            await service.reactivate_external_user(external_user)
            await session.commit()
    else:
        # An inactive flag alone does not prove admission/grants are absent.
        await service.deprovision_user(external_user.user_id)
    await session.refresh(external_user)


@router.delete("/Users/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    *, role: ScimConnectionRole, session: AsyncDBSession, resource_id: UUID
) -> Response:
    """Deprovision a user. Already-removed is success, not an error.

    Known identities remain addressable and inactive. Unknown resource IDs
    return the same idempotent success without changing another tenant.
    """
    await lock_role_changes(session, _organization_id(role))
    try:
        external_user, _ = await _linked_user(
            session, organization_id=_organization_id(role), resource_id=resource_id
        )
    except TracecatNotFoundError:
        # A resource this tenant never had is already in the desired state.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    await _apply_active(session, role=role, external_user=external_user, active=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Groups
# =============================================================================


async def _group_members(
    session: AsyncSession, external_group_id: UUID
) -> list[ScimGroupMemberRef]:
    return (await _group_members_by_group(session, [external_group_id])).get(
        external_group_id, []
    )


async def _group_members_by_group(
    session: AsyncSession, group_ids: list[UUID]
) -> dict[UUID, list[ScimGroupMemberRef]]:
    if not group_ids:
        return {}
    stmt = (
        select(
            ExternalGroupMember.external_group_id,
            ExternalGroupMember.external_user_id,
            User.__table__.c.email,
        )
        .join(ExternalUser, ExternalUser.id == ExternalGroupMember.external_user_id)
        .join(User, User.__table__.c.id == ExternalUser.user_id)
        .where(ExternalGroupMember.external_group_id.in_(group_ids))
        .order_by(User.email)
    )
    members: dict[UUID, list[ScimGroupMemberRef]] = {}
    for group_id, user_id, email in (await session.execute(stmt)).tuples().all():
        members.setdefault(group_id, []).append(
            ScimGroupMemberRef(value=str(user_id), display=email)
        )
    return members


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
    """Resolve every reference, rejecting an incomplete or foreign member set."""
    try:
        candidates = {UUID(member.value) for member in members}
    except ValueError as exc:
        raise TracecatValidationError("Invalid group member reference") from exc
    if not candidates:
        return []
    stmt = select(ExternalUser.id).where(
        ExternalUser.organization_id == organization_id,
        ExternalUser.id.in_(candidates),
    )
    resolved = set((await session.execute(stmt)).scalars())
    if resolved != candidates:
        raise TracecatValidationError("Unknown group member reference")
    return sorted(resolved, key=str)


@router.get("/Groups")
async def list_groups(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    filter: str | None = Query(default=None),
    startIndex: int = Query(default=1),
    count: int = Query(default=100),
) -> ScimListResponse:
    """List synced external groups."""
    startIndex = max(1, startIndex)
    count = min(200, max(0, count))
    organization_id = _organization_id(role)
    conditions = [ExternalGroup.organization_id == organization_id]
    wanted = _parse_equality_filter(filter, "displayName")
    if wanted is not None:
        conditions.append(func.lower(ExternalGroup.display_name) == wanted)

    total = await session.scalar(
        select(func.count()).select_from(ExternalGroup).where(*conditions)
    )
    groups = list(
        (
            await session.execute(
                select(ExternalGroup)
                .where(*conditions)
                .order_by(ExternalGroup.display_name, ExternalGroup.id)
                .offset(startIndex - 1)
                .limit(count)
            )
        ).scalars()
    )
    members = await _group_members_by_group(session, [g.id for g in groups])
    resources = [_group_resource(g, members=members.get(g.id, [])) for g in groups]
    return _list_response(resources, total=total or 0, start_index=startIndex)


@router.post("/Groups", status_code=status.HTTP_201_CREATED)
async def create_group(
    *,
    role: ScimConnectionRole,
    session: AsyncDBSession,
    params: ScimGroupRequest,
    response: Response,
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
        external_user_ids = await _resolve_member_ids(
            session, organization_id=organization_id, members=params.members
        )
        await service.replace_external_group_members(group.id, external_user_ids)
    result = _group_resource(group, members=await _group_members(session, group.id))
    await session.commit()
    response.headers["Location"] = _resource_location("Groups", group.id)
    return result


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
    await lock_role_changes(session, _organization_id(role))
    organization_id = _organization_id(role)
    group = await _get_group(
        session, organization_id=organization_id, group_id=group_id
    )
    service = SCIMService(session, role)
    await service.update_external_group(
        group, external_id=params.external_id, display_name=params.display_name
    )
    external_user_ids = await _resolve_member_ids(
        session, organization_id=organization_id, members=params.members or []
    )
    await service.replace_external_group_members(group.id, external_user_ids)
    await session.flush()
    await session.refresh(group)
    result = _group_resource(group, members=await _group_members(session, group.id))
    await session.commit()
    return result


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
    await lock_role_changes(session, _organization_id(role))
    organization_id = _organization_id(role)
    group = await _get_group(
        session, organization_id=organization_id, group_id=group_id
    )
    service = SCIMService(session, role)

    current = {UUID(m.value) for m in await _group_members(session, group.id)}
    display_name = group.display_name
    members_changed = False

    for operation in params.operations:
        path = operation.path.strip() if operation.path is not None else None
        if path is None:
            if (
                operation.op == "remove"
                or not isinstance(operation.value, dict)
                or not operation.value
            ):
                raise TracecatValidationError(
                    "PATCH requires an attribute path or object value"
                )
            attributes = list(operation.value.items())
        else:
            attributes = [(path, operation.value)]
        for attribute, value in attributes:
            attribute = (
                attribute.strip().lower().removeprefix(f"{GROUP_SCHEMA.lower()}:")
            )
            if attribute == "displayname":
                if (
                    operation.op == "remove"
                    or not isinstance(value, str)
                    or not value.strip()
                ):
                    raise TracecatValidationError(
                        "displayName must be a non-empty string"
                    )
                display_name = value
                continue
            selected = _member_path(attribute)
            if selected is not None:
                if operation.op != "remove":
                    raise TracecatValidationError(
                        "Filtered members only supports removal"
                    )
                current -= selected
            elif operation.op == "remove" and value is None:
                current.clear()
            else:
                refs = _member_refs(value)
                resolved = set(
                    await _resolve_member_ids(
                        session, organization_id=organization_id, members=refs
                    )
                )
                match operation.op:
                    case "add":
                        current |= resolved
                    case "remove":
                        current -= resolved
                    case "replace":
                        current = resolved
            members_changed = True

    if display_name != group.display_name:
        await service.upsert_external_group(
            external_id=group.external_id, display_name=display_name
        )
    if members_changed:
        await service.replace_external_group_members(group.id, sorted(current, key=str))
    await session.flush()
    await session.refresh(group)
    result = _group_resource(group, members=await _group_members(session, group.id))
    await session.commit()
    return result


def _member_path(path: str) -> set[UUID] | None:
    """Parse supported member paths without treating unknown paths as clear-all."""
    if path.lower() == "members":
        return None
    match = re.fullmatch(r"members\[\s*(.*?)\s*\]", path, flags=re.IGNORECASE)
    if match is None:
        raise TracecatValidationError("Unsupported PATCH path")
    selected: set[UUID] = set()
    for clause in re.split(r"\s+or\s+", match[1], flags=re.IGNORECASE):
        term = re.fullmatch(
            r'value\s+eq\s+"([0-9a-fA-F-]+)"', clause.strip(), flags=re.IGNORECASE
        )
        if term is None:
            raise TracecatValidationError("Unsupported member filter")
        try:
            selected.add(UUID(term[1]))
        except ValueError as exc:
            raise TracecatValidationError("Invalid group member reference") from exc
    return selected


def _member_refs(value: Any) -> list[ScimGroupMemberRef]:
    """Reject malformed references rather than silently truncating replacements."""
    values = value if isinstance(value, list) else [value]
    refs = []
    for item in values:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("value"), str)
            or not item["value"]
        ):
            raise TracecatValidationError("Invalid group member reference")
        refs.append(ScimGroupMemberRef(value=item["value"]))
    return refs


@router.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    *, role: ScimConnectionRole, session: AsyncDBSession, group_id: UUID
) -> Response:
    """Delete a synced group and drop the membership it supplied."""
    await lock_role_changes(session, _organization_id(role))
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
                    "mutability": "immutable",
                    "description": (
                        "The login email address. Username changes are not supported."
                    ),
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
