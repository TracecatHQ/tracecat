"""Database models for Tracecat."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi_users.db import (
    SQLAlchemyBaseOAuthAccountTableUUID,
    SQLAlchemyBaseUserTableUUID,
)
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyBaseAccessTokenTableUUID
from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema, to_json
from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Interval,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from tracecat import config
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.auth.schemas import UserRole
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.cases.durations.schemas import CaseDurationAnchorSelection
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseTaskStatus,
)
from tracecat.identifiers import (
    OrganizationID,
    OwnerID,
    WorkspaceID,
    action,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.enums import IntegrationStatus, MCPAuthType, OAuthGrantType
from tracecat.interactions.enums import InteractionStatus, InteractionType
from tracecat.invitations.enums import InvitationStatus
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.tiers.types import EntitlementsDict
from tracecat.workspaces.schemas import WorkspaceSettings

_UNSET = object()

CASE_PRIORITY_ENUM = Enum(CasePriority, name="casepriority")
CASE_SEVERITY_ENUM = Enum(CaseSeverity, name="caseseverity")
CASE_STATUS_ENUM = Enum(CaseStatus, name="casestatus")
CASE_TASK_STATUS_ENUM = Enum(CaseTaskStatus, name="casetaskstatus")
INTERACTION_STATUS_ENUM = Enum(InteractionStatus, name="interactionstatus")
APPROVAL_STATUS_ENUM = Enum(ApprovalStatus, name="approvalstatus")
INVITATION_STATUS_ENUM = Enum(InvitationStatus, name="invitationstatus")


# Naming convention for constraints so Alembic can generate deterministic names
# See: https://alembic.sqlalchemy.org/en/latest/naming.html
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy models."""

    __abstract__ = True
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Mixin for timestamp columns using SQLAlchemy mapped_column."""

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class InvitationMixin:
    """Mixin for invitation columns shared between workspace and organization invitations."""

    email: Mapped[str] = mapped_column(String(255), doc="Email address of the invitee")
    status: Mapped[InvitationStatus] = mapped_column(
        INVITATION_STATUS_ENUM, default=InvitationStatus.PENDING, index=True
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("user.id", ondelete="SET NULL"),
        doc="User who created the invitation",
    )
    token: Mapped[str] = mapped_column(
        String(64), unique=True, doc="Unique token for magic link acceptance"
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), doc="When the invitation expires"
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), doc="When the invitation was accepted"
    )


class RecordModel(TimestampMixin, Base):
    """Base class for all record models - provides surrogate key and timestamps."""

    __abstract__ = True
    __serialization_exclude__ = {"surrogate_id"}

    surrogate_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)

    def __repr__(self) -> str:
        """Return a string representation showing all mapped attributes."""
        attrs = {
            column.key: value
            for column in self.__mapper__.columns
            if (value := getattr(self, column.key, _UNSET)) is not _UNSET
        }
        return f"{self.__class__.__name__}({to_json(attrs, indent=2).decode()})"

    def __eq__(self, __value: object) -> bool:
        return (
            isinstance(__value, self.__class__)
            and self.surrogate_id == __value.surrogate_id
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert the model instance to a dictionary.

        This serializes the model's column attributes only. Subclasses can override
        this method to include related objects in the output if needed.
        """
        return _to_dict(self)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        """Make this SQLAlchemy model usable as a Pydantic field."""

        def validate(value: Any) -> RecordModel:
            if not isinstance(value, cls):
                raise TypeError(
                    f"Expected {cls.__name__} instance, got {type(value)!r}"
                )
            return value

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                _to_dict,
                when_used="always",  # use "always" if you also want model_dump(mode="python") to convert
            ),
        )


class Organization(Base, TimestampMixin):
    """An organization in the platform."""

    __tablename__ = "organization"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, index=True)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    members: Mapped[list[User]] = relationship(
        "User",
        secondary="organization_membership",
        back_populates="organizations",
        lazy="select",
    )
    organization_tier: Mapped[OrganizationTier | None] = relationship(
        "OrganizationTier",
        back_populates="organization",
        uselist=False,
    )


class OrganizationModel(RecordModel):
    """Base class for organization-scoped resources.

    Used for resources that belong to an organization (e.g., Workspace, OrganizationSecret).
    The organization_id is a foreign key to the organization table.

    Default ondelete is RESTRICT to prevent accidental data loss when deleting
    organizations. Override in subclasses to use CASCADE where appropriate.
    """

    __abstract__ = True

    organization_id: Mapped[OrganizationID] = mapped_column(
        UUID,
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )


class PlatformModel(RecordModel):
    """Base class for platform-owned resources (not scoped to any org)."""

    __abstract__ = True


class WorkspaceModel(RecordModel):
    """Base class for workspace-owned resources.

    Used for resources that belong to a specific workspace (e.g., Workflow, Case, Secret).
    """

    __abstract__ = True

    workspace_id: Mapped[WorkspaceID] = mapped_column(
        UUID, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )


def _to_dict(instance: RecordModel) -> dict[str, Any]:
    return {
        column.key: value
        for column in instance.__mapper__.columns
        if column.key not in instance.__class__.__serialization_exclude__
        and (value := getattr(instance, column.key, _UNSET)) is not _UNSET
    }


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    __tablename__ = "oauth_account"

    user: Mapped[User] = relationship(back_populates="oauth_accounts")


class Membership(Base):
    """Link table for users and workspaces (many to many)."""

    __tablename__ = "membership"
    __table_args__ = (
        Index("ix_membership_workspace_id", "workspace_id"),
        Index("ix_membership_workspace_user", "workspace_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("user.id"),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("workspace.id"),
        primary_key=True,
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole, name="workspacerole"),
        nullable=False,
        default=WorkspaceRole.EDITOR,
    )


class OrganizationMembership(Base, TimestampMixin):
    """Link table for users and organizations (many to many)."""

    __tablename__ = "organization_membership"
    __table_args__ = (
        # Index for "get all members of org" queries
        # (PK index covers user_id lookups, but not org_id alone)
        Index("ix_org_membership_org_id", "organization_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("organization.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[OrgRole] = mapped_column(
        Enum(OrgRole, name="orgrole"),
        nullable=False,
        default=OrgRole.MEMBER,
    )


class Ownership(Base):
    """Table to map resources to owners.

    - Organization owns all workspaces
    - One specific user owns the organization
    - Workspaces own all resources (e.g. workflows, secrets) except itself

    Three types of owners:
    - User
    - Workspace
    - Organization (given by a  uuid.uuid4 sentinel value created on database creation)
    """

    __tablename__ = "ownership"

    resource_id: Mapped[str] = mapped_column(String, nullable=False, primary_key=True)
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    owner_id: Mapped[OwnerID] = mapped_column(UUID, nullable=False)
    owner_type: Mapped[str] = mapped_column(String, nullable=False)


class Workspace(OrganizationModel):
    """A workspace belonging to an organization."""

    __tablename__ = "workspace"
    organization_id: Mapped[OrganizationID] = mapped_column(
        UUID,
        ForeignKey("organization.id", ondelete="RESTRICT"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(String, index=True, nullable=False)
    settings: Mapped[WorkspaceSettings] = mapped_column(
        JSONB,
        default=WorkspaceSettings(workflow_unlimited_timeout_enabled=True),
        nullable=True,
    )
    members: Mapped[list[User]] = relationship(
        "User",
        secondary=Membership.__table__,
        back_populates="workspaces",
    )
    workflows: Mapped[list[Workflow]] = relationship(
        "Workflow",
        back_populates="workspace",
        cascade="all, delete",
    )
    cases: Mapped[list[Case]] = relationship(
        "Case",
        back_populates="workspace",
        cascade="all, delete",
    )
    secrets: Mapped[list[Secret]] = relationship(
        "Secret",
        back_populates="workspace",
        cascade="all, delete",
    )
    variables: Mapped[list[WorkspaceVariable]] = relationship(
        "WorkspaceVariable",
        back_populates="workspace",
        cascade="all, delete",
    )
    workflow_tags: Mapped[list[Tag]] = relationship(
        "Tag",
        back_populates="workspace",
        cascade="all, delete",
    )
    case_tags: Mapped[list[CaseTag]] = relationship(
        "CaseTag",
        back_populates="workspace",
        cascade="all, delete",
    )
    agent_presets: Mapped[list[AgentPreset]] = relationship(
        "AgentPreset",
        back_populates="workspace",
        cascade="all, delete",
    )
    case_duration_definitions: Mapped[list[CaseDurationDefinition]] = relationship(
        "CaseDurationDefinition",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    folders: Mapped[list[WorkflowFolder]] = relationship(
        "WorkflowFolder",
        back_populates="workspace",
        cascade="all, delete",
    )
    integrations: Mapped[list[OAuthIntegration]] = relationship(
        "OAuthIntegration",
        back_populates="workspace",
        cascade="all, delete",
    )
    oauth_providers: Mapped[list[WorkspaceOAuthProvider]] = relationship(
        "WorkspaceOAuthProvider",
        back_populates="workspace",
        cascade="all, delete",
    )


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "user"

    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="userrole"), nullable=False, default=UserRole.BASIC
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        "OAuthAccount",
        back_populates="user",
        cascade="all, delete",
        lazy="selectin",
    )
    workspaces: Mapped[list[Workspace]] = relationship(
        "Workspace",
        back_populates="members",
        lazy="select",
        secondary=Membership.__table__,
    )
    assigned_cases: Mapped[list[Case]] = relationship(
        "Case",
        back_populates="assignee",
        lazy="select",
    )
    access_tokens: Mapped[list[AccessToken]] = relationship(
        "AccessToken",
        back_populates="user",
        lazy="select",
    )
    chats: Mapped[list[Chat]] = relationship(
        "Chat",
        back_populates="user",
        lazy="select",
    )
    organizations: Mapped[list[Organization]] = relationship(
        "Organization",
        secondary=OrganizationMembership.__table__,
        back_populates="members",
        lazy="select",
    )


class AccessToken(SQLAlchemyBaseAccessTokenTableUUID, Base):
    __tablename__ = "access_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID, unique=True, default=uuid.uuid4)
    user: Mapped[User] = relationship(back_populates="access_tokens")


class SAMLRequestData(Base):
    """Stores SAML request data for validating responses.

    This replaces the disk cache to support distributed environments like Fargate.
    """

    __tablename__ = "saml_request_data"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    relay_state: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class BaseSecret(Base):
    """Base attributes shared across organization and workspace secrets."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(255), nullable=False, default="custom")
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_keys: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    environment: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default=DEFAULT_SECRETS_ENVIRONMENT,
    )
    tags: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.id}, name={self.name}, environment={self.environment})"


class OrganizationSecret(OrganizationModel, BaseSecret):
    __tablename__ = "organization_secret"
    __table_args__ = (UniqueConstraint("organization_id", "name", "environment"),)


class PlatformSecret(PlatformModel, BaseSecret):
    """Platform-level secrets."""

    __tablename__ = "platform_secret"
    __table_args__ = (UniqueConstraint("name", "environment"),)


class Secret(WorkspaceModel, BaseSecret):
    """Workspace secrets."""

    __tablename__ = "secret"
    __table_args__ = (UniqueConstraint("name", "environment", "workspace_id"),)

    workspace: Mapped[Workspace] = relationship(back_populates="secrets")


class WorkspaceVariable(WorkspaceModel):
    __tablename__ = "workspace_variable"
    __table_args__ = (UniqueConstraint("name", "environment", "workspace_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=True)
    environment: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default=DEFAULT_SECRETS_ENVIRONMENT,
    )
    tags: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="variables")


class WorkflowDefinition(WorkspaceModel):
    """A workflow definition.

    This is the underlying representation/snapshot of a workflow in the system, which
    can directly execute in the runner.

    Shoulds
    -------
    1. Be convertible into a Workspace Workflow + Acitons
    2. Be convertible into a YAML DSL
    3. Be able to be versioned

    Shouldn'ts
    ----------
    1. Have any stateful information

    Relationships
    -------------
    - 1 Workflow to many WorkflowDefinitions

    """

    __tablename__ = "workflow_definition"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("workflow.id", ondelete="CASCADE"),
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=True, default=dict)
    alias: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True, doc="Workflow alias at commit time"
    )
    registry_lock: Mapped[dict[str, str] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc=(
            "Frozen registry versions at commit time. "
            "Maps repository origin to version string."
        ),
    )

    workflow: Mapped[Workflow] = relationship(back_populates="definitions")


class WorkflowFolder(WorkspaceModel):
    """Folder for organizing workflows.

    Uses materialized path pattern for hierarchical structure.
    Path format: "/parent/child/" where each segment is the folder name.
    Root folders have path "/foldername/".
    """

    __tablename__ = "workflow_folder"
    __table_args__ = (
        UniqueConstraint(
            "path", "workspace_id", name="uq_workflow_folder_path_workspace"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(
        String, index=True, nullable=False, doc="Full materialized path: /parent/child/"
    )

    workspace: Mapped[Workspace] = relationship(back_populates="folders")
    workflows: Mapped[list[Workflow]] = relationship(
        "Workflow", back_populates="folder", cascade="all, delete-orphan"
    )

    @property
    def parent_path(self) -> str:
        """Get the parent path of this folder."""
        if self.path == "/":
            return "/"

        # Remove trailing slash, split by slashes, remove the last segment, join back
        path_parts = self.path.rstrip("/").split("/")
        if len(path_parts) <= 2:  # Root level folder like "/folder/"
            return "/"

        return "/".join(path_parts[:-1]) + "/"

    @property
    def is_root(self) -> bool:
        """Check if this is a root-level folder."""
        return self.path.count("/") <= 2  # "/foldername/" has two slashes


class WorkflowTag(Base):
    """Link table for workflows and tags with optional metadata."""

    __tablename__ = "workflow_tag"
    __table_args__ = (PrimaryKeyConstraint("tag_id", "workflow_id"),)

    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("tag.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("workflow.id", ondelete="CASCADE"),
        nullable=False,
    )


class Workflow(WorkspaceModel):
    """The workflow state.

    Notes
    -----
    - This table serves as the source of truth for the workflow regardless of operating
     mode (headless or not)
    - Workflow controls (status, scheduels, etc.) are stored and modified here
    - Workflow definitions are executable instances (snapshots) of the workflow

    Relationships
    -------------
    - 1 Workflow to many WorkflowDefinitions
    """

    __tablename__ = "workflow"
    __table_args__ = (
        UniqueConstraint(
            "alias", "workspace_id", name="uq_workflow_alias_workspace_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="offline", nullable=False)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entrypoint: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="ID of the node directly connected to the trigger.",
    )
    expects: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=True,
        doc="Input schema for the workflow",
    )
    returns: Mapped[Any | None] = mapped_column(
        JSONB, nullable=True, doc="Workflow return values"
    )
    trigger_position_x: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
        doc="Trigger node X position",
    )
    trigger_position_y: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
        doc="Trigger node Y position",
    )
    graph_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        doc="Graph version for optimistic concurrency control. Incremented on each graph mutation.",
    )
    viewport_x: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
        doc="Viewport X position",
    )
    viewport_y: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
        doc="Viewport Y position",
    )
    viewport_zoom: Mapped[float] = mapped_column(
        Float,
        default=1.0,
        nullable=False,
        doc="Viewport zoom level",
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=True,
        doc="Workflow configuration",
    )
    alias: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True, doc="Alias for the workflow"
    )
    error_handler: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="Workflow alias or ID for the workflow to run when this fails.",
    )
    icon_url: Mapped[str | None] = mapped_column(String, nullable=True)
    registry_lock: Mapped[dict[str, str] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc=(
            "Maps repository origin to pinned version string. "
            "Example: {'tracecat_registry': '1.2.3', 'git+ssh://...': '0.5.0'}"
        ),
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("workflow_folder.id", ondelete="CASCADE"),
        nullable=True,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="workflows")
    folder: Mapped[WorkflowFolder | None] = relationship(back_populates="workflows")
    actions: Mapped[list[Action]] = relationship(
        "Action",
        back_populates="workflow",
        cascade="all, delete",
        lazy="selectin",
    )
    definitions: Mapped[list[WorkflowDefinition]] = relationship(
        "WorkflowDefinition",
        back_populates="workflow",
        cascade="all, delete",
    )
    webhook: Mapped[Webhook] = relationship(
        "Webhook",
        back_populates="workflow",
        cascade="all, delete",
        lazy="selectin",
        uselist=False,
    )
    schedules: Mapped[list[Schedule]] = relationship(
        "Schedule",
        back_populates="workflow",
        cascade="all, delete",
        lazy="selectin",
    )
    tags: Mapped[list[Tag]] = relationship(
        "Tag",
        secondary=WorkflowTag.__table__,
        back_populates="workflows",
    )


class WebhookApiKey(WorkspaceModel):
    __tablename__ = "webhook_api_key"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    webhook_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("webhook.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    hashed: Mapped[str] = mapped_column(String(128), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    preview: Mapped[str] = mapped_column(String(16), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)

    webhook: Mapped[Webhook | None] = relationship(back_populates="api_key")


class Webhook(WorkspaceModel):
    __tablename__ = "webhook"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="offline", nullable=False)
    methods: Mapped[list[str]] = mapped_column(
        JSONB,
        default=lambda: ["POST"],
        nullable=False,
        server_default=text("'[\"POST\"]'::jsonb"),
    )
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=True)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("workflow.id", ondelete="CASCADE"),
        nullable=True,
    )
    allowlisted_cidrs: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )

    workflow: Mapped[Workflow] = relationship(back_populates="webhook")
    api_key: Mapped[WebhookApiKey | None] = relationship(
        "WebhookApiKey",
        back_populates="webhook",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    @property
    def secret(self) -> str:
        secret = (
            os.environ.get("TRACECAT__SIGNING_SECRET")
            or config.TRACECAT__SIGNING_SECRET
        )
        if not secret:
            raise ValueError("TRACECAT__SIGNING_SECRET is not set")
        # Using legacy format to prevent webhook url changes
        id_part = f"wh-{self.id.hex}"
        return hashlib.sha256(f"{id_part}{secret}".encode()).hexdigest()

    @property
    def url(self) -> str:
        short_wf_id = WorkflowUUID.make_short(self.workflow_id)
        return f"{config.TRACECAT__PUBLIC_API_URL}/webhooks/{short_wf_id}/{self.secret}"

    @property
    def normalized_methods(self) -> tuple[str, ...]:
        return tuple(m.lower() for m in self.methods)

    @property
    def has_active_api_key(self) -> bool:
        return self.api_key is not None and self.api_key.revoked_at is None

    @property
    def api_key_preview(self) -> str | None:
        return self.api_key.preview if self.api_key else None

    @property
    def api_key_created_at(self) -> datetime | None:
        return self.api_key.created_at if self.api_key else None

    @property
    def api_key_last_used_at(self) -> datetime | None:
        return self.api_key.last_used_at if self.api_key else None

    @property
    def api_key_revoked_at(self) -> datetime | None:
        return self.api_key.revoked_at if self.api_key else None


class Schedule(WorkspaceModel):
    __tablename__ = "schedule"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[Literal["online", "offline"]] = mapped_column(
        String(16), default="online", nullable=False
    )
    cron: Mapped[str | None] = mapped_column(String, nullable=True)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=True)
    every: Mapped[timedelta | None] = mapped_column(Interval(), nullable=True)
    offset: Mapped[timedelta | None] = mapped_column(
        Interval(),
        nullable=True,
        doc="ISO 8601 duration string",
    )
    start_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        doc="ISO 8601 datetime string",
    )
    end_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        doc="ISO 8601 datetime string",
    )
    timeout: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="The maximum number of seconds to wait for the workflow to complete",
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("workflow.id", ondelete="CASCADE"),
        nullable=True,
    )

    workflow: Mapped[Workflow] = relationship(back_populates="schedules")


class Action(WorkspaceModel):
    """The workspace action state."""

    __tablename__ = "action"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    type: Mapped[str] = mapped_column(
        String, nullable=False, doc="The action type, i.e. UDF key"
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="offline", nullable=False)
    inputs: Mapped[str] = mapped_column(
        String,
        default="",
        nullable=False,
        doc=(
            "YAML string containing input configuration. The default value is an empty "
            "string, which is `null` in YAML flow style."
        ),
    )
    control_flow: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=True,
    )
    is_interactive: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        doc="Whether the action is interactive",
    )
    interaction: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="The interaction configuration for the action",
    )
    environment: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="Override environment for this action's execution",
    )
    position_x: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
        doc="Node X position in workflow canvas",
    )
    position_y: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
        doc="Node Y position in workflow canvas",
    )
    upstream_edges: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
        doc="List of incoming edges: [{'source_id': 'act-xxx', 'source_handle': 'success'}]",
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("workflow.id", ondelete="CASCADE"),
        nullable=False,
    )

    workflow: Mapped[Workflow] = relationship(back_populates="actions")

    @property
    def ref(self) -> str:
        """Slugified title of the action. Used for references."""
        return action.ref(self.title)


class BaseRegistryRepository(Base):
    """Shared fields for registry repositories."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID, default=uuid.uuid4, nullable=False, unique=True
    )
    origin: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc=(
            "Tells you where the template action was created from. Can use this to "
            "track the hierarchy of templates."
        ),
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    commit_sha: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="The SHA of the last commit that was synced from the repository",
    )


class RegistryRepository(OrganizationModel, BaseRegistryRepository):
    """A repository of templates and actions."""

    __tablename__ = "registry_repository"
    __table_args__ = (UniqueConstraint("organization_id", "origin"),)

    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("registry_version.id", ondelete="SET NULL"),
        doc="The active registry version for this repository.",
    )
    actions: Mapped[list[RegistryAction]] = relationship(
        "RegistryAction",
        back_populates="repository",
        cascade="all, delete",
        lazy="selectin",
    )
    versions: Mapped[list[RegistryVersion]] = relationship(
        "RegistryVersion",
        back_populates="repository",
        cascade="all, delete",
        foreign_keys="[RegistryVersion.repository_id]",
    )
    current_version: Mapped[RegistryVersion | None] = relationship(
        "RegistryVersion",
        foreign_keys=[current_version_id],
        uselist=False,
    )


class PlatformRegistryRepository(PlatformModel, BaseRegistryRepository):
    """A platform-owned repository of templates and actions."""

    __tablename__ = "platform_registry_repository"
    __table_args__ = (UniqueConstraint("origin"),)

    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("platform_registry_version.id", ondelete="SET NULL"),
        doc="The active platform registry version for this repository.",
    )
    actions: Mapped[list[PlatformRegistryAction]] = relationship(
        "PlatformRegistryAction",
        back_populates="repository",
        cascade="all, delete",
        lazy="selectin",
    )
    versions: Mapped[list[PlatformRegistryVersion]] = relationship(
        "PlatformRegistryVersion",
        back_populates="repository",
        cascade="all, delete",
        foreign_keys="[PlatformRegistryVersion.repository_id]",
    )
    current_version: Mapped[PlatformRegistryVersion | None] = relationship(
        "PlatformRegistryVersion",
        foreign_keys=[current_version_id],
        uselist=False,
    )


class BaseRegistryAction(Base):
    """Shared fields for registry actions."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID, default=uuid.uuid4, nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    namespace: Mapped[str] = mapped_column(String, nullable=False)
    origin: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    default_title: Mapped[str | None] = mapped_column(
        String, nullable=True, doc="The default title of the action"
    )
    display_group: Mapped[str | None] = mapped_column(
        String, nullable=True, doc="The presentation group of the action"
    )
    doc_url: Mapped[str | None] = mapped_column(
        String, nullable=True, doc="Link to documentation"
    )
    author: Mapped[str | None] = mapped_column(
        String, nullable=True, doc="Author of the action"
    )
    deprecated: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="Marks action as deprecated along with message",
    )
    secrets: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="The secrets required by the action",
    )
    interface: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=True, doc="The interface of the action"
    )
    implementation: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=True,
        doc="The action's implementation",
    )
    options: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=True,
        doc="The action's options",
    )

    @property
    def action(self) -> str:
        return f"{self.namespace}.{self.name}"


class RegistryAction(OrganizationModel, BaseRegistryAction):
    """A registry action."""

    __tablename__ = "registry_action"
    __table_args__ = (UniqueConstraint("organization_id", "namespace", "name"),)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("registry_repository.id", ondelete="CASCADE"),
        nullable=True,
    )

    repository: Mapped[RegistryRepository] = relationship(back_populates="actions")


class PlatformRegistryAction(PlatformModel, BaseRegistryAction):
    """A platform registry action."""

    __tablename__ = "platform_registry_action"
    __table_args__ = (UniqueConstraint("namespace", "name"),)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("platform_registry_repository.id", ondelete="CASCADE"),
        nullable=True,
    )

    repository: Mapped[PlatformRegistryRepository] = relationship(
        back_populates="actions"
    )


class BaseRegistryVersion(Base):
    """Shared fields for registry versions."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID, default=uuid.uuid4, nullable=False, unique=True, index=True
    )
    version: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Version string, e.g., '1.0.0', 'tracecat_core@1.2.3'",
    )
    commit_sha: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="Git commit SHA if applicable",
    )
    manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        doc="Frozen action definitions",
    )
    tarball_uri: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="S3 URI to the compressed tarball venv for action execution",
    )


class RegistryVersion(OrganizationModel, BaseRegistryVersion):
    """An immutable versioned snapshot of a registry repository."""

    __tablename__ = "registry_version"
    __table_args__ = (UniqueConstraint("organization_id", "repository_id", "version"),)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("registry_repository.id", ondelete="CASCADE"),
        nullable=False,
    )

    repository: Mapped[RegistryRepository] = relationship(
        back_populates="versions",
        foreign_keys=[repository_id],
    )
    index_entries: Mapped[list[RegistryIndex]] = relationship(
        "RegistryIndex",
        back_populates="registry_version",
        cascade="all, delete",
        lazy="selectin",
    )


class PlatformRegistryVersion(PlatformModel, BaseRegistryVersion):
    """A platform-owned versioned snapshot of a registry repository."""

    __tablename__ = "platform_registry_version"
    __table_args__ = (
        UniqueConstraint("id"),
        UniqueConstraint("repository_id", "version"),
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("platform_registry_repository.id", ondelete="CASCADE"),
        nullable=False,
    )

    repository: Mapped[PlatformRegistryRepository] = relationship(
        back_populates="versions",
        foreign_keys=[repository_id],
    )
    index_entries: Mapped[list[PlatformRegistryIndex]] = relationship(
        "PlatformRegistryIndex",
        back_populates="registry_version",
        cascade="all, delete",
        lazy="selectin",
    )


class BaseRegistryIndex(Base):
    """Shared fields for registry indexes."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID, default=uuid.uuid4, nullable=False, unique=True, index=True
    )
    namespace: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Action type: 'udf' or 'template'",
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    default_title: Mapped[str | None] = mapped_column(
        String, nullable=True, doc="Default title of the action"
    )
    display_group: Mapped[str | None] = mapped_column(
        String, nullable=True, doc="Presentation group of the action"
    )
    doc_url: Mapped[str | None] = mapped_column(
        String, nullable=True, doc="Link to documentation"
    )
    author: Mapped[str | None] = mapped_column(
        String, nullable=True, doc="Author of the action"
    )
    deprecated: Mapped[str | None] = mapped_column(
        String, nullable=True, doc="Deprecation message if deprecated"
    )
    secrets: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Secrets required by the action",
    )
    interface: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=True,
        doc="Action interface (expects/returns schema)",
    )
    options: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=True,
        doc="Action options",
    )

    @property
    def action(self) -> str:
        return f"{self.namespace}.{self.name}"


class RegistryIndex(OrganizationModel, BaseRegistryIndex):
    """Index of actions from a RegistryVersion manifest for fast lookups."""

    __tablename__ = "registry_index"
    __table_args__ = (UniqueConstraint("registry_version_id", "namespace", "name"),)
    registry_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("registry_version.id", ondelete="CASCADE"),
        nullable=False,
    )

    registry_version: Mapped[RegistryVersion] = relationship(
        back_populates="index_entries"
    )


class PlatformRegistryIndex(PlatformModel, BaseRegistryIndex):
    """Platform index of actions from a registry manifest."""

    __tablename__ = "platform_registry_index"
    __table_args__ = (
        UniqueConstraint("id"),
        UniqueConstraint("registry_version_id", "namespace", "name"),
    )
    registry_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("platform_registry_version.id", ondelete="CASCADE"),
        nullable=False,
    )

    registry_version: Mapped[PlatformRegistryVersion] = relationship(
        back_populates="index_entries"
    )


class OrganizationSetting(OrganizationModel):
    """An organization setting."""

    __tablename__ = "organization_settings"
    __table_args__ = (UniqueConstraint("organization_id", "key"),)
    # Override to CASCADE - settings are meaningless without the org
    organization_id: Mapped[OrganizationID] = mapped_column(
        UUID,
        ForeignKey("organization.id", ondelete="CASCADE"),
        nullable=False,
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    key: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        doc="A unique key that identifies the setting",
    )
    value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    value_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="The data type of the setting value",
    )
    is_encrypted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="Whether the setting is encrypted",
    )


class PlatformSetting(Base, TimestampMixin):
    """A platform-level setting (not scoped to any organization)."""

    __tablename__ = "platform_settings"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String, unique=True, index=True)
    value: Mapped[bytes] = mapped_column(LargeBinary)
    value_type: Mapped[str] = mapped_column(String)
    is_encrypted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )


class Table(WorkspaceModel):
    """Metadata for lookup tables."""

    __tablename__ = "tables"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)

    columns: Mapped[list[TableColumn]] = relationship(
        "TableColumn",
        back_populates="table",
        cascade="all, delete",
        lazy="selectin",
    )


class TableColumn(TimestampMixin, Base):
    """Column definitions for tables."""

    __tablename__ = "table_column"
    __table_args__ = (UniqueConstraint("table_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        primary_key=True,
        nullable=False,
        unique=True,
        index=True,
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("tables.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    type: Mapped[str] = mapped_column(
        String, nullable=False, doc="SQL type like 'TEXT', 'INTEGER', etc."
    )
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    options: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    # Relationship back to the table
    table: Mapped[Table] = relationship(
        "Table",
        back_populates="columns",
        lazy="selectin",
    )


class CaseFields(TimestampMixin, Base):
    """A table of fields for a case."""

    __tablename__ = "case_field"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_case_field_workspace"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        primary_key=True,
        nullable=False,
        unique=True,
        index=True,
    )
    workspace_id: Mapped[WorkspaceID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    schema: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class CaseTagLink(Base):
    """Link table for cases and case tags."""

    __tablename__ = "case_tag_link"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("case.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("case_tag.id", ondelete="CASCADE"),
        primary_key=True,
    )


class CaseTag(WorkspaceModel):
    """A tag for organizing and filtering cases."""

    __tablename__ = "case_tag"
    __table_args__ = (
        UniqueConstraint("name", "workspace_id", name="uq_case_tag_name_workspace"),
        UniqueConstraint("ref", "workspace_id", name="uq_case_tag_ref_workspace"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    ref: Mapped[str] = mapped_column(String, nullable=False, index=True)
    color: Mapped[str | None] = mapped_column(String, nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="case_tags")
    cases: Mapped[list[Case]] = relationship(
        "Case",
        back_populates="tags",
        secondary=CaseTagLink.__table__,
    )


class CaseDurationDefinition(WorkspaceModel):
    """Workspace-defined case duration metric anchored on case events."""

    __tablename__ = "case_duration_definition"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_case_duration_definition_workspace_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    start_event_type: Mapped[CaseEventType] = mapped_column(nullable=False)
    start_timestamp_path: Mapped[str] = mapped_column(
        String(255), default="created_at", nullable=False
    )
    start_field_filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    start_selection: Mapped[CaseDurationAnchorSelection] = mapped_column(
        default=CaseDurationAnchorSelection.FIRST, nullable=False
    )
    end_event_type: Mapped[CaseEventType] = mapped_column(nullable=False)
    end_timestamp_path: Mapped[str] = mapped_column(
        String(255), default="created_at", nullable=False
    )
    end_field_filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    end_selection: Mapped[CaseDurationAnchorSelection] = mapped_column(
        default=CaseDurationAnchorSelection.FIRST, nullable=False
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="case_duration_definitions"
    )
    case_durations: Mapped[list[CaseDuration]] = relationship(
        "CaseDuration",
        back_populates="definition",
        cascade="all, delete",
        lazy="selectin",
    )


class CaseDuration(WorkspaceModel):
    """Computed duration values for a case tied to a duration definition."""

    __tablename__ = "case_duration"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "definition_id",
            name="uq_case_duration_case_definition",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("case.id", ondelete="CASCADE"),
        nullable=False,
    )
    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("case_duration_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("case_event.id", ondelete="SET NULL"),
        nullable=True,
    )
    end_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("case_event.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    duration: Mapped[timedelta | None] = mapped_column(Interval(), nullable=True)

    case: Mapped[Case] = relationship(
        "Case",
        back_populates="durations",
        lazy="selectin",
    )
    definition: Mapped[CaseDurationDefinition] = relationship(
        "CaseDurationDefinition",
        back_populates="case_durations",
        lazy="selectin",
    )


class Case(WorkspaceModel):
    """A case represents an incident or issue that needs to be tracked and resolved."""

    __tablename__ = "case"
    __table_args__ = (
        Index("ix_case_cursor_pagination", "workspace_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    case_number: Mapped[int] = mapped_column(
        Integer,
        Identity(start=1, increment=1),
        unique=True,
        nullable=False,
        index=True,
        doc="Auto-incrementing case number for human readable IDs like CASE-1234",
    )
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(5000), nullable=False)
    priority: Mapped[CasePriority] = mapped_column(
        CASE_PRIORITY_ENUM,
        nullable=False,
    )
    severity: Mapped[CaseSeverity] = mapped_column(
        CASE_SEVERITY_ENUM,
        nullable=False,
    )
    status: Mapped[CaseStatus] = mapped_column(
        CASE_STATUS_ENUM,
        default=CaseStatus.NEW,
        nullable=False,
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Additional data payload for the case",
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        doc="The ID of the user who is assigned to the case.",
    )
    comments: Mapped[list[CaseComment]] = relationship(
        "CaseComment",
        back_populates="case",
        cascade="all, delete",
    )
    events: Mapped[list[CaseEvent]] = relationship(
        "CaseEvent",
        back_populates="case",
        cascade="all, delete",
    )
    durations: Mapped[list[CaseDuration]] = relationship(
        "CaseDuration",
        back_populates="case",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    attachments: Mapped[list[CaseAttachment]] = relationship(
        "CaseAttachment",
        back_populates="case",
        cascade="all, delete",
    )
    assignee: Mapped[User | None] = relationship(
        "User",
        back_populates="assigned_cases",
        lazy="selectin",
    )
    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="cases")
    tags: Mapped[list[CaseTag]] = relationship(
        "CaseTag",
        secondary=CaseTagLink.__table__,
        back_populates="cases",
        lazy="selectin",
    )
    tasks: Mapped[list[CaseTask]] = relationship(
        "CaseTask",
        back_populates="case",
        cascade="all, delete",
    )

    @property
    def short_id(self) -> str:
        return f"CASE-{self.case_number:04d}"


class CaseComment(WorkspaceModel):
    """A comment on a case."""

    __tablename__ = "case_comment"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    content: Mapped[str] = mapped_column(String(5000), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        nullable=True,
        doc="The ID of the user who made the comment. If null, the comment is system generated.",
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        nullable=True,
        doc="The ID of the parent comment. If null, the comment is a top-level comment.",
    )
    last_edited_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("case.id", ondelete="CASCADE"),
        nullable=False,
    )

    case: Mapped[Case] = relationship("Case", back_populates="comments")


class CaseEvent(WorkspaceModel):
    """A activity record for a case.

    Uses a tagged union pattern where the 'type' field indicates the kind of activity,
    and the 'data' field contains variant-specific information for that activity type.
    """

    __tablename__ = "case_event"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    type: Mapped[CaseEventType] = mapped_column(nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=True,
        doc="Variant-specific data for this event type",
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        nullable=True,
        doc="The ID of the user who made the event. If null, the event is system generated.",
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("case.id", ondelete="CASCADE"),
        nullable=False,
    )

    case: Mapped[Case] = relationship("Case", back_populates="events")


class CaseTask(WorkspaceModel):
    __tablename__ = "case_task"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("case.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    priority: Mapped[CasePriority] = mapped_column(
        CASE_PRIORITY_ENUM,
        default=CasePriority.UNKNOWN,
        nullable=False,
        doc="Task priority level",
    )
    status: Mapped[CaseTaskStatus] = mapped_column(
        CASE_TASK_STATUS_ENUM,
        default=CaseTaskStatus.TODO,
        nullable=False,
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("workflow.id", ondelete="SET NULL"),
        nullable=True,
    )
    default_trigger_values: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="The default trigger values for the task.",
    )

    case: Mapped[Case] = relationship("Case", back_populates="tasks")
    assignee: Mapped[User | None] = relationship("User", lazy="selectin")
    workflow: Mapped[Workflow | None] = relationship("Workflow", lazy="selectin")


class Interaction(WorkspaceModel):
    """Database model for storing workflow interaction state.

    This table stores the state of interactions within workflows, allowing us
    to query them without relying on Temporal's event replay.
    """

    __tablename__ = "interaction"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    wf_exec_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action_ref: Mapped[str] = mapped_column(String, nullable=False)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[InteractionType] = mapped_column(nullable=False)
    status: Mapped[InteractionStatus] = mapped_column(
        INTERACTION_STATUS_ENUM,
        default=InteractionStatus.PENDING,
        nullable=False,
        index=True,
    )
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Data sent for the interaction",
    )
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Data received from the interaction",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(),
        nullable=True,
        doc="Timestamp for when the interaction expires",
    )
    actor: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="ID of the user/actor who responded",
    )


class Approval(WorkspaceModel):
    """Database model for storing agent tool approval state."""

    __tablename__ = "approval"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "session_id",
            "tool_call_id",
            name="uq_approval_workspace_session_tool",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
        doc="Unique approval identifier",
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("agent_session.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Agent session identifier (FK to agent_session.id)",
    )
    tool_call_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        doc="Identifier of the deferred tool call",
    )
    tool_name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Name of the tool requiring approval",
    )
    tool_call_args: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Tool call arguments captured at approval request time",
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        APPROVAL_STATUS_ENUM,
        default=ApprovalStatus.PENDING,
        nullable=False,
        index=True,
    )
    reason: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="Optional reason for approval decision",
    )
    decision: Mapped[bool | dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc=(
            "Deferred tool result decision (approved/denied with override args or rejection reason)"
        ),
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("user.id"),
        nullable=True,
        doc="User who approved this request",
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class AgentSession(WorkspaceModel):
    """Generic agent session/thread entity (harness-agnostic).

    Represents an agent execution session that can be backed by different harnesses
    (pydantic_ai, claude_code, etc.). This is the primary entity for all agent
    interactions - both chat UI and workflow-initiated.

    Replaces the legacy Chat model for new conversations.
    """

    __tablename__ = "agent_session"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        unique=True,
        index=True,
        doc="Session identifier (matches RunAgentArgs.session_id)",
    )
    title: Mapped[str] = mapped_column(
        String(200),
        default="New Chat",
        nullable=False,
        doc="Human-readable title for the session",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=True,
        doc="User who created this session (nullable for workflow-initiated sessions)",
    )
    entity_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        doc="The entity type this session is associated with (case, agent_preset, workflow, etc.)",
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        nullable=False,
        doc="The ID of the associated entity",
    )
    tools: Mapped[list[str] | None] = mapped_column(
        JSONB,
        default=None,
        nullable=True,
        doc="The tools available to the agent for this session",
    )
    agent_preset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("agent_preset.id", ondelete="SET NULL"),
        nullable=True,
        doc="Agent preset used for this session (if any)",
    )
    # Agent harness fields
    harness_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="Agent harness type: 'pydantic_ai', 'claude_code', or None for legacy",
    )
    # Claude SDK session tracking (for resume)
    sdk_session_id: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        doc="Claude SDK internal session ID (for JSONL file naming on resume)",
    )
    # Current workflow run tracking (for approval continuation)
    curr_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        nullable=True,
        index=True,
        doc="Current workflow run ID - used to construct workflow handle for approvals",
    )
    # Stream position tracking (for resuming from last event)
    last_stream_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        doc="Last processed Redis stream ID - used to resume streaming from correct position",
    )
    # Parent session for forked sessions (approval continuations)
    parent_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("agent_session.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        index=True,
        doc="Parent session ID for forked sessions (e.g., approval continuations)",
    )

    # Relationships
    creator: Mapped[User | None] = relationship("User")
    parent_session: Mapped[AgentSession | None] = relationship(
        "AgentSession",
        remote_side=[id],
        foreign_keys=[parent_session_id],
    )
    history: Mapped[list[AgentSessionHistory]] = relationship(
        "AgentSessionHistory",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AgentSessionHistory.surrogate_id",
    )


class AgentSessionHistory(WorkspaceModel):
    """Harness-agnostic history storage for agent sessions.

    Each row represents one message in the session. Ordered by created_at.
    """

    __tablename__ = "agent_session_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("agent_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        doc="Harness-specific message content",
    )
    kind: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="internal",
        index=True,
        doc="Message kind for filtering (chat-message, internal). Default to internal - only user/assistant messages explicitly marked visible.",
    )

    session: Mapped[AgentSession] = relationship(
        "AgentSession",
        back_populates="history",
    )


class AgentPreset(WorkspaceModel):
    """Database model for storing reusable agent preset configurations."""

    __tablename__ = "agent_preset"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_agent_preset_workspace_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
        doc="Unique agent preset identifier",
    )
    name: Mapped[str] = mapped_column(
        String(120), nullable=False, doc="Human readable preset name"
    )
    slug: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        index=True,
        doc="Stable slug identifier used for lookups",
    )
    description: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        doc="Optional description for the preset",
    )
    instructions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="System instructions for the agent",
    )
    model_name: Mapped[str] = mapped_column(
        String(120), nullable=False, doc="Model name used for execution"
    )
    model_provider: Mapped[str] = mapped_column(
        String(120), nullable=False, doc="LLM provider identifier"
    )
    base_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc="Optional model base URL override",
    )
    output_type: Mapped[dict[str, Any] | str | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Optional structured output type definition",
    )
    actions: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Tool identifiers available to the agent",
    )
    namespaces: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Tool namespaces available to the agent",
    )
    tool_approvals: Mapped[dict[str, bool] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Tool approval requirements by tool name",
    )
    mcp_integrations: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="MCP integrations to use",
    )
    retries: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False, doc="Maximum retry attempts per run"
    )
    enable_internet_access: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
        doc="Whether to enable direct internet access in the agent sandbox",
    )

    workspace: Mapped[Workspace] = relationship(back_populates="agent_presets")
    chats: Mapped[list[Chat]] = relationship(
        "Chat",
        back_populates="agent_preset",
        cascade="save-update",
    )


class File(WorkspaceModel):
    """A file entity with content-addressable storage using SHA256."""

    __tablename__ = "file"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="SHA256 hash for content-addressable storage and deduplication",
    )
    name: Mapped[str] = mapped_column(
        String(config.TRACECAT__MAX_ATTACHMENT_FILENAME_LENGTH),
        nullable=False,
        doc="Original filename when uploaded",
    )
    content_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="MIME type of the file",
    )
    size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="File size in bytes",
    )
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        nullable=True,
        doc="ID of the user who uploaded the file. If None, assume is system created.",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        doc="Timestamp for soft deletion",
    )
    attachments: Mapped[list[CaseAttachment]] = relationship(
        "CaseAttachment",
        back_populates="file",
        cascade="all, delete",
    )

    @hybrid_property
    def is_deleted(self) -> bool:
        """Check if file is soft deleted."""
        return self.deleted_at is not None


class CaseAttachment(TimestampMixin, Base):
    """Link table between cases and files."""

    __tablename__ = "case_attachment"
    __table_args__ = (
        UniqueConstraint("case_id", "file_id", name="uq_case_attachment_case_file"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        primary_key=True,
        nullable=False,
        unique=True,
        index=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("case.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("file.id", ondelete="CASCADE"),
        nullable=False,
    )

    case: Mapped[Case] = relationship(
        "Case",
        back_populates="attachments",
    )
    file: Mapped[File] = relationship(
        "File",
        back_populates="attachments",
    )

    @hybrid_property
    def storage_path(self) -> str:
        """Generate the storage path for case attachments."""
        return f"attachments/{self.file.sha256}"


class OAuthIntegration(TimestampMixin, Base):
    """Store user integrations with external services."""

    __tablename__ = "oauth_integration"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider_id",
            "user_id",
            "grant_type",
            name="uq_oauth_integration_workspace_provider_user_flow",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        primary_key=True,
        nullable=False,
        unique=True,
        index=True,
    )
    # Workspace
    workspace_id: Mapped[WorkspaceID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=True,
    )
    provider_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
    )
    encrypted_access_token: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )
    encrypted_refresh_token: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    encrypted_client_id: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    encrypted_client_secret: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    use_workspace_credentials: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    token_type: Mapped[str] = mapped_column(
        String,
        default="Bearer",
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    scope: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    requested_scopes: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    grant_type: Mapped[OAuthGrantType] = mapped_column(
        Enum(OAuthGrantType, name="oauthgranttype"),
        default=OAuthGrantType.AUTHORIZATION_CODE,
        nullable=False,
    )
    authorization_endpoint: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    token_endpoint: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    user: Mapped[User | None] = relationship("User")
    workspace: Mapped[Workspace] = relationship(
        "Workspace", back_populates="integrations"
    )

    @property
    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        """Check if the token needs to be refreshed soon (within 5 minutes)."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= (self.expires_at - timedelta(minutes=5))

    @property
    def status(self) -> IntegrationStatus:
        """Get the status of the integration."""
        # Is configured: Has client ID and Secret
        is_configured = (
            self.encrypted_client_id is not None
            and self.encrypted_client_secret is not None
        )

        # Is connected: Successfully authenticated
        is_connected = len(self.encrypted_access_token) > 0

        # Return status based on conditions
        if is_connected:
            return IntegrationStatus.CONNECTED
        elif is_configured:
            return IntegrationStatus.CONFIGURED
        else:
            return IntegrationStatus.NOT_CONFIGURED


class WorkspaceOAuthProvider(TimestampMixin, Base):
    """Custom OAuth providers defined within a workspace."""

    __tablename__ = "oauth_provider"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider_id",
            "grant_type",
            name="uq_oauth_provider_workspace_provider_grant_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        primary_key=True,
        nullable=False,
        unique=True,
        index=True,
    )
    workspace_id: Mapped[WorkspaceID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        doc="Unique identifier for the custom OAuth provider",
    )
    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Display name for the custom provider",
    )
    description: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="Optional description for the custom provider",
    )
    grant_type: Mapped[OAuthGrantType] = mapped_column(
        Enum(OAuthGrantType, name="oauthgranttype"),
        nullable=False,
        doc="OAuth grant type supported by this provider",
    )
    authorization_endpoint: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Default OAuth authorization endpoint for this provider",
    )
    token_endpoint: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Default OAuth token endpoint for this provider",
    )
    scopes: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        doc="Default OAuth scopes requested by this provider",
    )

    workspace: Mapped[Workspace] = relationship(
        "Workspace",
        back_populates="oauth_providers",
    )


MCP_AUTH_TYPE_ENUM = Enum(MCPAuthType, name="mcpauthtype")


class MCPIntegration(TimestampMixin, Base):
    """Store MCP integrations for a workspace."""

    __tablename__ = "mcp_integration"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "slug", name="uq_mcp_integration_workspace_slug"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        primary_key=True,
        nullable=False,
        unique=True,
        index=True,
        doc="Unique MCP integration identifier",
    )
    workspace_id: Mapped[WorkspaceID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        doc="Workspace ID associated with this MCP integration",
    )
    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Human readable name of the MCP integration",
    )
    description: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="Optional description of the MCP integration",
    )
    slug: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Slug of the MCP integration",
    )
    server_uri: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="URL of the MCP server",
    )
    auth_type: Mapped[MCPAuthType] = mapped_column(
        MCP_AUTH_TYPE_ENUM,
        nullable=False,
        doc="Authentication type for the MCP integration",
    )
    oauth_integration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("oauth_integration.id", ondelete="SET NULL"),
        nullable=True,
        doc="OAuth integration associated with this MCP integration",
    )
    encrypted_headers: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        doc="Encrypted custom credentials (API key, bearer token, or JSON headers) for custom auth type",
    )

    oauth_integration: Mapped[OAuthIntegration | None] = relationship(
        "OAuthIntegration",
        uselist=False,
        lazy="selectin",
    )


class OAuthStateDB(TimestampMixin, Base):
    """Store OAuth state parameters for CSRF protection during OAuth flows."""

    __tablename__ = "oauth_state"
    __table_args__ = (Index("ix_oauth_state_expires_at", "expires_at"),)

    state: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        primary_key=True,
        nullable=False,
        doc="Unique state identifier for OAuth flow",
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        doc="Workspace ID associated with this OAuth flow",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        doc="User ID initiating the OAuth flow",
    )
    provider_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Provider ID for this OAuth flow",
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        doc="When this state expires",
    )
    code_verifier: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="PKCE code verifier for OAuth 2.1 flows",
    )

    # Relationships
    workspace: Mapped[Workspace] = relationship()
    user: Mapped[User] = relationship()


class Chat(WorkspaceModel):
    """A chat between a user and an AI agent."""

    __tablename__ = "chat"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String,
        default="New Chat",
        nullable=False,
        doc="Human-readable title for the chat",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="The entity associated with this chat. e.g. a case",
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        nullable=False,
        doc="The polymorphic id of the associated entity.",
    )
    tools: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=True,
        doc="The tools available to the agent for this chat.",
    )
    agent_preset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("agent_preset.id", ondelete="SET NULL"),
        nullable=True,
        doc="Optional agent preset used for this chat session.",
    )
    last_stream_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        doc="Last processed Redis stream ID for this chat.",
    )

    user: Mapped[User] = relationship("User", back_populates="chats")
    messages: Mapped[list[ChatMessage]] = relationship(
        "ChatMessage",
        back_populates="chat",
        cascade="all, delete",
        order_by="ChatMessage.created_at.asc()",
    )
    agent_preset: Mapped[AgentPreset | None] = relationship(
        "AgentPreset",
        back_populates="chats",
        lazy="joined",
    )


class ChatMessage(WorkspaceModel):
    """A message in a chat."""

    __tablename__ = "chat_message"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String, nullable=False, doc="The kind of message")
    harness: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="pydantic-ai",
        server_default="pydantic-ai",
        doc="The harness type that created this message (e.g., pydantic-ai, claude)",
    )
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=True,
        doc="The data of the message.",
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("chat.id", ondelete="CASCADE"),
        nullable=False,
    )

    chat: Mapped[Chat] = relationship("Chat", back_populates="messages")


class Tag(WorkspaceModel):
    """A workflow tag for organizing and filtering workflows."""

    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("name", "workspace_id", name="uq_tag_name_workspace"),
        UniqueConstraint("ref", "workspace_id", name="uq_tag_ref_workspace"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    ref: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        doc="Slug-like identifier derived from the name, used for API lookups alongside uuid.UUID",
    )
    color: Mapped[str | None] = mapped_column(String, nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="workflow_tags")
    workflows: Mapped[list[Workflow]] = relationship(
        "Workflow",
        secondary=WorkflowTag.__table__,
        back_populates="tags",
    )


class OrganizationInvitation(InvitationMixin, TimestampMixin, Base):
    """Invitation to join an organization."""

    __tablename__ = "organization_invitation"
    __table_args__ = (UniqueConstraint("email", "organization_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[OrgRole] = mapped_column(
        Enum(OrgRole, name="orgrole"),
        default=OrgRole.MEMBER,
        doc="Role to grant upon acceptance",
    )

    # Relationships
    organization: Mapped[Organization] = relationship("Organization")
    inviter: Mapped[User | None] = relationship("User")


class Invitation(InvitationMixin, TimestampMixin, Base):
    """Invitation to join a workspace."""

    __tablename__ = "invitation"
    __table_args__ = (UniqueConstraint("workspace_id", "email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("workspace.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole, name="workspacerole"),
        default=WorkspaceRole.EDITOR,
        doc="Role to grant upon acceptance",
    )

    # Relationships
    workspace: Mapped[Workspace] = relationship("Workspace")
    inviter: Mapped[User | None] = relationship("User")


class Tier(Base, TimestampMixin):
    """Platform-configurable tier definition.

    Tiers define resource limits and feature entitlements that apply to organizations.
    The default tier provides unlimited access for self-hosted deployments.
    """

    __tablename__ = "tier"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String)

    # Limits (None = unlimited)
    max_concurrent_workflows: Mapped[int | None] = mapped_column(Integer)
    max_action_executions_per_workflow: Mapped[int | None] = mapped_column(Integer)
    max_concurrent_actions: Mapped[int | None] = mapped_column(Integer)
    api_rate_limit: Mapped[int | None] = mapped_column(Integer)
    api_burst_capacity: Mapped[int | None] = mapped_column(Integer)

    # Entitlements (JSONB for flexibility)
    entitlements: Mapped[EntitlementsDict] = mapped_column(JSONB, default=dict)

    # Metadata
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class OrganizationTier(Base, TimestampMixin):
    """Organization's assigned tier with optional overrides.

    Each organization can have a tier assigned with per-org overrides for limits
    and entitlements. None values mean "use tier default".
    """

    __tablename__ = "organization_tier"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("organization.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    tier_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("tier.id"))

    # Per-org limit overrides (None = use tier default)
    max_concurrent_workflows: Mapped[int | None] = mapped_column(Integer)
    max_action_executions_per_workflow: Mapped[int | None] = mapped_column(Integer)
    max_concurrent_actions: Mapped[int | None] = mapped_column(Integer)
    api_rate_limit: Mapped[int | None] = mapped_column(Integer)
    api_burst_capacity: Mapped[int | None] = mapped_column(Integer)

    # Per-org entitlement overrides
    entitlement_overrides: Mapped[EntitlementsDict | None] = mapped_column(JSONB)

    # Billing (future)
    stripe_customer_id: Mapped[str | None] = mapped_column(String)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    # Relationships
    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="organization_tier"
    )
    tier: Mapped[Tier] = relationship("Tier")
