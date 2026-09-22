"""API schemas for SCIM connection credentials and the SCIM 2.0 protocol (EE)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracecat.authz.enums import ScimConnectionStatus
from tracecat.core.schemas import Schema

# =============================================================================
# Connection credentials
# =============================================================================


class ScimConnectionRead(Schema):
    """Status of an organization's SCIM connection. Never carries the token."""

    id: UUID
    organization_id: UUID
    preview: str
    status: ScimConnectionStatus
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScimConnectionTokenRead(BaseModel):
    """A freshly issued token. The raw value is returned exactly once."""

    connection: ScimConnectionRead
    token: str


# =============================================================================
# Group mapping administration
# =============================================================================


class ExternalGroupRead(Schema):
    """A synced IdP group offered to an admin as a mapping source."""

    id: UUID
    external_id: str
    display_name: str
    member_count: int


class ExternalGroupMappingRead(Schema):
    """A mapping joined with both sides, so a list renders without refetching."""

    id: UUID
    external_group_id: UUID
    external_group_external_id: str
    external_group_display_name: str
    group_id: UUID
    group_name: str


class ExternalGroupMappingCreate(BaseModel):
    """Request to project an external group into a Tracecat group."""

    external_group_id: UUID
    group_id: UUID


class ScimDirectoryUserRead(Schema):
    """A user the provider has pushed into this organization."""

    id: UUID
    email: str
    external_id: str
    active: bool


class ScimMappingPlanRead(Schema):
    """What activating one proposed mapping would do to a Tracecat group."""

    external_group_id: UUID
    external_group_display_name: str
    group_id: UUID
    group_name: str
    manual_members_purged: list[UUID]
    manual_member_emails: dict[UUID, str]
    users_gaining_access: list[UUID]
    users_losing_access: list[UUID]


class ScimActivationReviewRead(Schema):
    """What arrived while the connection was pending, and the effect of each mapping."""

    users: list[ScimDirectoryUserRead]
    plans: list[ScimMappingPlanRead]


class ScimActivationRequest(BaseModel):
    """Mappings to install as the connection is activated."""

    mappings: list[ExternalGroupMappingCreate] = Field(default_factory=list)


# =============================================================================
# SCIM 2.0 protocol
# =============================================================================

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_OP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
SERVICE_PROVIDER_CONFIG_SCHEMA = (
    "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
)
RESOURCE_TYPE_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"

SCIM_CONTENT_TYPE = "application/scim+json"

# Mirror the stored columns: external_user.external_id, external_group.external_id
# and external_group.display_name are all String(255).
EXTERNAL_ID_MAX_LENGTH = 255
DISPLAY_NAME_MAX_LENGTH = 255
# userName is stored as the account email, whose column is RFC 5321 sized.
USER_NAME_MAX_LENGTH = 320

# Providers send unknown attributes freely; rejecting them would fail otherwise
# valid provisioning requests, so every inbound resource ignores extras.
_ScimModelConfig = ConfigDict(populate_by_name=True, extra="ignore")


class ScimModel(BaseModel):
    """Base for SCIM wire models, which are camelCase by specification."""

    model_config = _ScimModelConfig


class ScimName(ScimModel):
    """A user's name components as pushed by the provider."""

    given_name: str | None = Field(default=None, alias="givenName")
    family_name: str | None = Field(default=None, alias="familyName")
    formatted: str | None = Field(default=None)


class ScimEmail(ScimModel):
    """One entry of a user's multi-valued email attribute."""

    value: str | None = Field(default=None)
    primary: bool | None = Field(default=None)
    type: str | None = Field(default=None)


class ScimMeta(ScimModel):
    """Resource metadata. Only the fields Okta reads are emitted."""

    resource_type: str = Field(alias="resourceType")
    created: datetime | None = Field(default=None)
    last_modified: datetime | None = Field(default=None, alias="lastModified")
    location: str | None = Field(default=None)


class ScimUserRequest(ScimModel):
    """An inbound User resource on POST or PUT.

    ``userName`` is the only attribute Tracecat stores as identity; the display
    and name attributes are accepted so providers do not see a validation
    failure, but nothing here maps onto a Tracecat column.
    """

    schemas: list[str] = Field(default_factory=lambda: [USER_SCHEMA])
    user_name: str = Field(alias="userName", max_length=USER_NAME_MAX_LENGTH)
    # Bounded by the stored column, so an oversized id is 400 invalidValue
    # rather than a database error the provider reads as 500.
    external_id: str | None = Field(
        default=None, alias="externalId", max_length=EXTERNAL_ID_MAX_LENGTH
    )
    active: bool = Field(default=True)
    name: ScimName | None = Field(default=None)
    display_name: str | None = Field(default=None, alias="displayName")
    emails: list[ScimEmail] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_fallback_identifier(self) -> Self:
        """Reject a userName too long to stand in for a missing externalId.

        ``create_user`` falls back to ``userName`` as the provider identifier,
        which is stored in a narrower column than the address itself.
        """
        if self.external_id is None and len(self.user_name) > EXTERNAL_ID_MAX_LENGTH:
            raise ValueError(
                "userName exceeds "
                f"{EXTERNAL_ID_MAX_LENGTH} characters and no externalId was supplied"
            )
        return self


class ScimUserResource(ScimModel):
    """A User resource as returned to the provider."""

    schemas: list[str] = Field(default_factory=lambda: [USER_SCHEMA])
    id: str
    user_name: str = Field(alias="userName", serialization_alias="userName")
    external_id: str | None = Field(
        default=None, alias="externalId", serialization_alias="externalId"
    )
    active: bool = Field(default=True)
    name: ScimName | None = Field(default=None)
    display_name: str | None = Field(
        default=None, alias="displayName", serialization_alias="displayName"
    )
    emails: list[ScimEmail] = Field(default_factory=list)
    meta: ScimMeta | None = Field(default=None)


class ScimGroupMemberRef(ScimModel):
    """A member reference inside a Group resource."""

    value: str
    display: str | None = Field(default=None)


class ScimGroupRequest(ScimModel):
    """An inbound Group resource on POST or PUT."""

    schemas: list[str] = Field(default_factory=lambda: [GROUP_SCHEMA])
    display_name: str = Field(alias="displayName", max_length=DISPLAY_NAME_MAX_LENGTH)
    external_id: str | None = Field(
        default=None, alias="externalId", max_length=EXTERNAL_ID_MAX_LENGTH
    )
    members: list[ScimGroupMemberRef] | None = Field(default=None)


class ScimGroupResource(ScimModel):
    """A Group resource as returned to the provider."""

    schemas: list[str] = Field(default_factory=lambda: [GROUP_SCHEMA])
    id: str
    display_name: str = Field(alias="displayName", serialization_alias="displayName")
    external_id: str | None = Field(
        default=None, alias="externalId", serialization_alias="externalId"
    )
    members: list[ScimGroupMemberRef] = Field(default_factory=list)
    meta: ScimMeta | None = Field(default=None)


class ScimListResponse(ScimModel):
    """The envelope every SCIM query returns, paginated 1-based."""

    schemas: list[str] = Field(default_factory=lambda: [LIST_RESPONSE_SCHEMA])
    total_results: int = Field(alias="totalResults", serialization_alias="totalResults")
    start_index: int = Field(alias="startIndex", serialization_alias="startIndex")
    items_per_page: int = Field(
        alias="itemsPerPage", serialization_alias="itemsPerPage"
    )
    resources: list[dict[str, Any]] = Field(
        default_factory=list, alias="Resources", serialization_alias="Resources"
    )


class ScimPatchOperation(ScimModel):
    """One entry of a PatchOp ``Operations`` array.

    ``op`` is case-insensitive per RFC 7644; Azure sends ``Add`` where Okta
    sends ``add``.
    """

    op: Literal["add", "remove", "replace"]
    path: str | None = Field(default=None)
    value: Any = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _lowercase_op(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(op := data.get("op"), str):
            return {**data, "op": op.lower()}
        return data


class ScimPatchOp(ScimModel):
    """A PATCH request body."""

    schemas: list[str] = Field(default_factory=lambda: [PATCH_OP_SCHEMA])
    operations: list[ScimPatchOperation] = Field(
        alias="Operations", serialization_alias="Operations"
    )


class ScimError(ScimModel):
    """The error envelope. ``status`` is a string by specification."""

    schemas: list[str] = Field(default_factory=lambda: [ERROR_SCHEMA])
    detail: str
    status: str
    scim_type: str | None = Field(
        default=None, alias="scimType", serialization_alias="scimType"
    )

    @classmethod
    def build(
        cls, *, status_code: int, detail: str, scim_type: str | None = None
    ) -> Self:
        return cls(detail=detail, status=str(status_code), scimType=scim_type)


ScimStartIndex = Annotated[int, Field(ge=1)]
ScimCount = Annotated[int, Field(ge=0, le=200)]
