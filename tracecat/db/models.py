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
from tracecat.authz.enums import WorkspaceRole
from tracecat.cases.durations.schemas import CaseDurationAnchorSelection
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseTaskStatus,
)
from tracecat.identifiers import OwnerID, action, id_factory
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.enums import IntegrationStatus, OAuthGrantType
from tracecat.interactions.enums import InteractionStatus, InteractionType
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.workspaces.schemas import WorkspaceSettings

_UNSET = object()

CASE_PRIORITY_ENUM = Enum(CasePriority, name="casepriority")
CASE_SEVERITY_ENUM = Enum(CaseSeverity, name="caseseverity")
CASE_STATUS_ENUM = Enum(CaseStatus, name="casestatus")
CASE_TASK_STATUS_ENUM = Enum(CaseTaskStatus, name="casetaskstatus")
INTERACTION_STATUS_ENUM = Enum(InteractionStatus, name="interactionstatus")
APPROVAL_STATUS_ENUM = Enum(ApprovalStatus, name="approvalstatus")


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy models."""

    __abstract__ = True


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


class RecordModel(TimestampMixin, Base):
    """Declarative base for workspace-owned resources."""

    __abstract__ = True
    __pydantic_ignore_fields__ = {"surrogate_id"}

    surrogate_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    owner_id: Mapped[OwnerID] = mapped_column(UUID, nullable=False)

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
        """Convert the model instance to a dictionary."""
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


def _to_dict(instance: RecordModel) -> dict[str, Any]:
    return {
        column.key: value
        for column in instance.__mapper__.columns
        if column.key not in instance.__class__.__pydantic_ignore_fields__
        and (value := getattr(instance, column.key, _UNSET)) is not _UNSET
    }


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    __tablename__ = "oauthaccount"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("user.id"), nullable=False
    )
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


class Workspace(RecordModel):
    __tablename__ = "workspace"

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
        back_populates="owner",
        cascade="all, delete",
    )
    secrets: Mapped[list[Secret]] = relationship(
        "Secret",
        back_populates="owner",
        cascade="all, delete",
    )
    variables: Mapped[list[WorkspaceVariable]] = relationship(
        "WorkspaceVariable",
        back_populates="owner",
        cascade="all, delete",
    )
    workflow_tags: Mapped[list[Tag]] = relationship(
        "Tag",
        back_populates="owner",
        cascade="all, delete",
    )
    case_tags: Mapped[list[CaseTag]] = relationship(
        "CaseTag",
        back_populates="owner",
        cascade="all, delete",
    )
    agent_presets: Mapped[list[AgentPreset]] = relationship(
        "AgentPreset",
        back_populates="owner",
        cascade="all, delete",
    )
    case_duration_definitions: Mapped[list[CaseDurationDefinition]] = relationship(
        "CaseDurationDefinition",
        back_populates="owner",
        cascade="all, delete-orphan",
    )
    folders: Mapped[list[WorkflowFolder]] = relationship(
        "WorkflowFolder",
        back_populates="owner",
        cascade="all, delete",
    )
    integrations: Mapped[list[OAuthIntegration]] = relationship(
        "OAuthIntegration",
        back_populates="owner",
        cascade="all, delete",
    )
    oauth_providers: Mapped[list[WorkspaceOAuthProvider]] = relationship(
        "WorkspaceOAuthProvider",
        back_populates="owner",
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


class AccessToken(SQLAlchemyBaseAccessTokenTableUUID, Base):
    __tablename__ = "accesstoken"

    id: Mapped[uuid.UUID] = mapped_column(UUID, unique=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("user.id"), nullable=False
    )
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


class BaseSecret(RecordModel):
    """Base attributes shared across organization and workspace secrets."""

    __abstract__ = True

    id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        default=id_factory("secret"),
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


class OrganizationSecret(BaseSecret):
    __tablename__ = "organizationsecret"
    __table_args__ = (UniqueConstraint("name", "environment"),)


class Secret(BaseSecret):
    """Workspace secrets."""

    __tablename__ = "secret"
    __table_args__ = (UniqueConstraint("name", "environment", "owner_id"),)

    owner_id: Mapped[OwnerID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
    )
    owner: Mapped[Workspace | None] = relationship(back_populates="secrets")


class WorkspaceVariable(RecordModel):
    __tablename__ = "workspace_variable"
    __table_args__ = (UniqueConstraint("name", "environment", "owner_id"),)

    owner_id: Mapped[OwnerID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
    )
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

    owner: Mapped[Workspace] = relationship(back_populates="variables")


class WorkflowDefinition(RecordModel):
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

    __tablename__ = "workflowdefinition"

    id: Mapped[str] = mapped_column(
        String(64),
        default=id_factory("wf-defn"),
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

    workflow: Mapped[Workflow] = relationship(back_populates="definitions")


class WorkflowFolder(RecordModel):
    """Folder for organizing workflows.

    Uses materialized path pattern for hierarchical structure.
    Path format: "/parent/child/" where each segment is the folder name.
    Root folders have path "/foldername/".
    """

    __tablename__ = "workflow_folder"
    __table_args__ = (
        UniqueConstraint("path", "owner_id", name="uq_workflow_folder_path_owner"),
    )

    owner_id: Mapped[OwnerID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
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

    owner: Mapped[Workspace] = relationship(back_populates="folders")
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

    __tablename__ = "workflowtag"
    __table_args__ = (PrimaryKeyConstraint("tag_id", "workflow_id"),)

    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("tag.id"),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("workflow.id"),
        nullable=False,
    )


class Workflow(RecordModel):
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
        UniqueConstraint("alias", "owner_id", name="uq_workflow_alias_owner_id"),
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
    object: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, doc="React flow graph object"
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
    owner_id: Mapped[OwnerID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("workflow_folder.id", ondelete="CASCADE"),
        nullable=True,
    )

    owner: Mapped[Workspace] = relationship(back_populates="workflows")
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


class WebhookApiKey(RecordModel):
    __tablename__ = "webhook_api_key"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    webhook_id: Mapped[str | None] = mapped_column(
        String,
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


class Webhook(RecordModel):
    __tablename__ = "webhook"

    id: Mapped[str] = mapped_column(
        String(64),
        default=id_factory("wh"),
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
        secret = os.getenv("TRACECAT__SIGNING_SECRET")
        if not secret:
            raise ValueError("TRACECAT__SIGNING_SECRET is not set")
        return hashlib.sha256(f"{self.id}{secret}".encode()).hexdigest()

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


class Schedule(RecordModel):
    __tablename__ = "schedule"

    id: Mapped[str] = mapped_column(
        String(64),
        default=id_factory("sch"),
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
    offset: Mapped[timedelta | None] = mapped_column(Interval(), nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(), nullable=True)
    timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("workflow.id", ondelete="CASCADE"),
        nullable=True,
    )

    workflow: Mapped[Workflow] = relationship(back_populates="schedules")


class Action(RecordModel):
    """The workspace action state."""

    __tablename__ = "action"

    id: Mapped[str] = mapped_column(
        String(64),
        default=id_factory("act"),
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
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("workflow.id", ondelete="CASCADE"),
        nullable=True,
    )

    workflow: Mapped[Workflow] = relationship(back_populates="actions")

    @property
    def ref(self) -> str:
        """Slugified title of the action. Used for references."""
        return action.ref(self.title)


class RegistryRepository(RecordModel):
    """A repository of templates and actions."""

    __tablename__ = "registryrepository"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID, default=uuid.uuid4, nullable=False, unique=True
    )
    origin: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
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

    actions: Mapped[list[RegistryAction]] = relationship(
        "RegistryAction",
        back_populates="repository",
        cascade="all, delete",
        lazy="selectin",
    )


class RegistryAction(RecordModel):
    """A registry action.


    A registry action can be a template action or a udf.
    A udf is a python user-defined function that can be used to create new actions.
    A template action is a reusable action that can be used to create new actions.
    Template actions loaded from tracecat base can be cloned but not edited.
    This is to ensure portability of templates across different users/systems.
    Custom template actions can be edited and cloned

    """

    __tablename__ = "registryaction"
    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_registry_action_namespace_name"),
    )

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
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("registryrepository.id", ondelete="CASCADE"),
        nullable=True,
    )

    repository: Mapped[RegistryRepository] = relationship(back_populates="actions")

    @property
    def action(self):
        return f"{self.namespace}.{self.name}"


class OrganizationSetting(RecordModel):
    """An organization setting."""

    __tablename__ = "organization_settings"

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
        unique=True,
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


class Table(RecordModel):
    """Metadata for lookup tables."""

    __tablename__ = "tables"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

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

    __tablename__ = "table_columns"
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

    __tablename__ = "case_fields"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        primary_key=True,
        nullable=False,
        unique=True,
        index=True,
    )
    # Add required foreign key to Case
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("cases.id", ondelete="CASCADE"),
        unique=True,  # Ensures one-to-one
        nullable=False,  # Ensures CaseFields must have a Case
    )
    case: Mapped[Case] = relationship(back_populates="fields")


class CaseTagLink(Base):
    """Link table for cases and case tags."""

    __tablename__ = "case_tag_link"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("case_tag.id", ondelete="CASCADE"),
        primary_key=True,
    )


class CaseTag(RecordModel):
    """A tag for organizing and filtering cases."""

    __tablename__ = "case_tag"
    __table_args__ = (
        UniqueConstraint("name", "owner_id", name="uq_case_tag_name_owner"),
        UniqueConstraint("ref", "owner_id", name="uq_case_tag_ref_owner"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    owner_id: Mapped[OwnerID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    ref: Mapped[str] = mapped_column(String, nullable=False, index=True)
    color: Mapped[str | None] = mapped_column(String, nullable=True)

    owner: Mapped[Workspace] = relationship(back_populates="case_tags")
    cases: Mapped[list[Case]] = relationship(
        "Case",
        back_populates="tags",
        secondary=CaseTagLink.__table__,
    )


class CaseDurationDefinition(RecordModel):
    """Workspace-defined case duration metric anchored on case events."""

    __tablename__ = "case_duration_definition"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "name",
            name="uq_case_duration_definition_owner_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    owner_id: Mapped[OwnerID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
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

    owner: Mapped[Workspace] = relationship(back_populates="case_duration_definitions")
    case_durations: Mapped[list[CaseDuration]] = relationship(
        "CaseDuration",
        back_populates="definition",
        cascade="all, delete",
        lazy="selectin",
    )


class CaseDuration(RecordModel):
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
    owner_id: Mapped[OwnerID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("cases.id", ondelete="CASCADE"),
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


class Case(RecordModel):
    """A case represents an incident or issue that needs to be tracked and resolved."""

    __tablename__ = "cases"
    __table_args__ = (
        Index("idx_case_cursor_pagination", "owner_id", "created_at", "id"),
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

    fields: Mapped[CaseFields | None] = relationship(
        "CaseFields",
        back_populates="case",
        cascade="all, delete",
        uselist=False,
        lazy="selectin",
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


class CaseComment(RecordModel):
    """A comment on a case."""

    __tablename__ = "case_comments"

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
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )

    case: Mapped[Case] = relationship("Case", back_populates="comments")


class CaseEvent(RecordModel):
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
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )

    case: Mapped[Case] = relationship("Case", back_populates="events")


class CaseTask(RecordModel):
    __tablename__ = "case_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("cases.id", ondelete="CASCADE"),
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

    case: Mapped[Case] = relationship("Case", back_populates="tasks")
    assignee: Mapped[User | None] = relationship("User", lazy="selectin")
    workflow: Mapped[Workflow | None] = relationship("Workflow", lazy="selectin")


class Interaction(RecordModel):
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


class Approval(RecordModel):
    """Database model for storing agent tool approval state."""

    __tablename__ = "approval"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "session_id",
            "tool_call_id",
            name="uq_approval_owner_session_tool",
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
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        nullable=False,
        index=True,
        doc="Agent session identifier",
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


class AgentPreset(RecordModel):
    """Database model for storing reusable agent preset configurations."""

    __tablename__ = "agent_preset"
    __table_args__ = (
        UniqueConstraint("owner_id", "slug", name="uq_agent_preset_owner_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
        doc="Unique agent preset identifier",
    )
    owner_id: Mapped[OwnerID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
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
    mcp_server_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True, doc="Optional MCP server URL"
    )
    mcp_server_headers: Mapped[dict[str, str] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Headers to include when connecting to the MCP server",
    )
    model_settings: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Provider specific model settings",
    )
    retries: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False, doc="Maximum retry attempts per run"
    )

    owner: Mapped[Workspace | None] = relationship(back_populates="agent_presets")
    chats: Mapped[list[Chat]] = relationship(
        "Chat",
        back_populates="agent_preset",
        cascade="save-update",
    )


class File(RecordModel):
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
        ForeignKey("cases.id", ondelete="CASCADE"),
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
            "owner_id",
            "provider_id",
            "user_id",
            "grant_type",
            name="uq_oauth_integration_owner_provider_user_flow",
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
    # Owner (workspace)
    owner_id: Mapped[OwnerID] = mapped_column(
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
    owner: Mapped[Workspace] = relationship("Workspace", back_populates="integrations")

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
            "owner_id",
            "provider_id",
            "grant_type",
            name="uq_oauth_provider_owner_provider_grant_type",
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
    owner_id: Mapped[uuid.UUID] = mapped_column(
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

    owner: Mapped[Workspace] = relationship(
        "Workspace",
        back_populates="oauth_providers",
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

    # Relationships
    workspace: Mapped[Workspace] = relationship()
    user: Mapped[User] = relationship()


class Chat(RecordModel):
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


class ChatMessage(RecordModel):
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


class Tag(RecordModel):
    """A workflow tag for organizing and filtering workflows."""

    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("name", "owner_id", name="uq_tag_name_owner"),
        UniqueConstraint("ref", "owner_id", name="uq_tag_ref_owner"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    owner_id: Mapped[OwnerID] = mapped_column(
        UUID,
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    ref: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        doc="Slug-like identifier derived from the name, used for API lookups alongside uuid.UUID",
    )
    color: Mapped[str | None] = mapped_column(String, nullable=True)

    owner: Mapped[Workspace] = relationship(back_populates="workflow_tags")
    workflows: Mapped[list[Workflow]] = relationship(
        "Workflow",
        secondary=WorkflowTag.__table__,
        back_populates="tags",
    )
