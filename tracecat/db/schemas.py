"""Database schemas for Tracecat."""

import hashlib
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import UUID4, ConfigDict, computed_field, field_validator
from sqlalchemy import TIMESTAMP, Column, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import UUID, Field, Relationship, SQLModel, UniqueConstraint

from tracecat import config
from tracecat.auth.models import UserRole
from tracecat.db.adapter import (
    SQLModelBaseAccessToken,
    SQLModelBaseOAuthAccount,
    SQLModelBaseUserDB,
)
from tracecat.identifiers import OwnerID, action, id_factory
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT

DEFAULT_SA_RELATIONSHIP_KWARGS = {
    "lazy": "selectin",
}


class Resource(SQLModel):
    """Base class for all resources in the system."""

    surrogate_id: int | None = Field(default=None, primary_key=True, exclude=True)
    owner_id: OwnerID
    created_at: datetime = Field(
        default_factory=datetime.now,  # Appease type checker
        sa_type=TIMESTAMP(timezone=True),  # type: ignore
        sa_column_kwargs={
            "server_default": func.now(),
            "nullable": False,
        },
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,  # Appease type checker
        sa_type=TIMESTAMP(timezone=True),  # type: ignore
        sa_column_kwargs={
            "server_default": func.now(),
            "onupdate": func.now(),
            "nullable": False,
        },
    )


class OAuthAccount(SQLModelBaseOAuthAccount, table=True):
    user_id: UUID4 = Field(foreign_key="user.id")
    user: "User" = Relationship(back_populates="oauth_accounts")


class Membership(SQLModel, table=True):
    """Link table for users and workspaces (many to many)."""

    user_id: UUID4 = Field(foreign_key="user.id", primary_key=True)
    workspace_id: UUID4 = Field(foreign_key="workspace.id", primary_key=True)


class Ownership(SQLModel, table=True):
    """Table to map resources to owners.

    - Organization owns all workspaces
    - One specific user owns the organization
    - Workspaces own all resources (e.g. workflows, secrets) except itself

    Three types of owners:
    - User
    - Workspace
    - Organization (given by a  UUID4 sentinel value created on database creation)
    """

    resource_id: str = Field(nullable=False, primary_key=True)
    resource_type: str
    owner_id: OwnerID
    owner_type: str


class Workspace(Resource, table=True):
    id: UUID4 = Field(default_factory=uuid.uuid4, nullable=False, unique=True)
    name: str = Field(..., unique=True, index=True, nullable=False)
    settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    members: list["User"] = Relationship(
        back_populates="workspaces",
        link_model=Membership,
        sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS,
    )
    workflows: list["Workflow"] = Relationship(
        back_populates="owner",
        sa_relationship_kwargs={
            "cascade": "all, delete",
            **DEFAULT_SA_RELATIONSHIP_KWARGS,
        },
    )
    secrets: list["Secret"] = Relationship(
        back_populates="owner",
        sa_relationship_kwargs={
            "cascade": "all, delete",
            **DEFAULT_SA_RELATIONSHIP_KWARGS,
        },
    )
    tags: list["Tag"] = Relationship(
        back_populates="owner",
        sa_relationship_kwargs={
            "cascade": "all, delete",
            **DEFAULT_SA_RELATIONSHIP_KWARGS,
        },
    )

    @computed_field
    @property
    def n_members(self) -> int:
        return len(self.members)


class User(SQLModelBaseUserDB, table=True):
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    role: UserRole = Field(nullable=False, default=UserRole.BASIC)
    settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    last_login_at: datetime | None = Field(
        sa_column=Column(TIMESTAMP(timezone=True)),
    )
    # Relationships
    oauth_accounts: list["OAuthAccount"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={
            "cascade": "all, delete",
            **DEFAULT_SA_RELATIONSHIP_KWARGS,
        },
    )
    workspaces: list["Workspace"] = Relationship(
        back_populates="members",
        link_model=Membership,
        sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS,
    )
    access_tokens: list["AccessToken"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS,
    )


class AccessToken(SQLModelBaseAccessToken, table=True):
    id: UUID4 = Field(default_factory=uuid.uuid4, nullable=False, unique=True)
    user: "User" = Relationship(
        back_populates="access_tokens",
        sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS,
    )


class BaseSecret(Resource):
    model_config: ConfigDict = ConfigDict(from_attributes=True)
    id: str = Field(
        default_factory=id_factory("secret"), nullable=False, unique=True, index=True
    )
    type: str = "custom"  # "custom", "token", "oauth2"
    name: str = Field(
        ...,
        max_length=255,
        index=True,
        nullable=False,
        description="Secret names should be unique within a user's scope.",
    )
    description: str | None = Field(default=None, max_length=255)
    # We store this object as encrypted bytes, but first validate that it's the correct type
    encrypted_keys: bytes
    environment: str = Field(default=DEFAULT_SECRETS_ENVIRONMENT, nullable=False)
    # Use sa_type over sa_column for inheritance
    tags: dict[str, str] | None = Field(sa_type=JSONB)


class OrganizationSecret(BaseSecret, table=True):
    __table_args__ = (UniqueConstraint("name", "environment"),)


class Secret(BaseSecret, table=True):
    """Workspace secrets."""

    __table_args__ = (UniqueConstraint("name", "environment", "owner_id"),)

    owner_id: OwnerID = Field(
        sa_column=Column(UUID, ForeignKey("workspace.id", ondelete="CASCADE"))
    )
    owner: Workspace | None = Relationship(back_populates="secrets")


class WorkflowDefinition(Resource, table=True):
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

    # Metadata
    id: str = Field(
        default_factory=id_factory("wf-defn"), nullable=False, unique=True, index=True
    )
    version: int = Field(..., index=True, description="DSL spec version")
    workflow_id: str = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )

    # DSL content
    content: dict[str, Any] = Field(sa_column=Column(JSONB))
    workflow: "Workflow" = Relationship(
        back_populates="definitions",
        sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS,
    )


class WorkflowTag(SQLModel, table=True):
    """Link table for workflows and tags with optional metadata."""

    tag_id: UUID4 = Field(foreign_key="tag.id", primary_key=True)
    workflow_id: str = Field(foreign_key="workflow.id", primary_key=True)


class Tag(Resource, table=True):
    """A tag for organizing and filtering entities."""

    __table_args__ = (UniqueConstraint("name", "owner_id"),)

    id: UUID4 = Field(
        default_factory=uuid.uuid4, nullable=False, unique=True, index=True
    )
    owner_id: OwnerID = Field(
        sa_column=Column(UUID, ForeignKey("workspace.id", ondelete="CASCADE"))
    )
    name: str = Field(index=True, nullable=False)
    color: str | None = Field(default=None)
    # Relationships
    owner: "Workspace" = Relationship(back_populates="tags")
    workflows: list["Workflow"] = Relationship(
        back_populates="tags",
        link_model=WorkflowTag,
        sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS,
    )


class Workflow(Resource, table=True):
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

    __table_args__ = (
        UniqueConstraint("alias", "owner_id", name="uq_workflow_alias_owner_id"),
    )

    id: str = Field(
        default_factory=id_factory("wf"), nullable=False, unique=True, index=True
    )
    title: str
    description: str
    status: str = "offline"  # "online" or "offline"
    version: int | None = None
    entrypoint: str | None = Field(
        default=None,
        description="ID of the node directly connected to the trigger.",
    )
    static_inputs: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
        description="Static inputs for the workflow",
    )
    expects: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
        description="Input schema for the workflow",
    )
    returns: Any | None = Field(
        default=None,
        sa_column=Column(JSONB),
        description="Workflow return values",
    )
    object: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB), description="React flow graph object"
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
        description="Workflow configuration",
    )
    alias: str | None = Field(
        default=None, description="Alias for the workflow", index=True
    )
    icon_url: str | None = None
    # Owner
    owner_id: OwnerID = Field(
        sa_column=Column(UUID, ForeignKey("workspace.id", ondelete="CASCADE"))
    )
    owner: Workspace | None = Relationship(back_populates="workflows")
    # Relationships
    actions: list["Action"] | None = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={
            "cascade": "all, delete",
            **DEFAULT_SA_RELATIONSHIP_KWARGS,
        },
    )
    definitions: list["WorkflowDefinition"] | None = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={
            "cascade": "all, delete",
            **DEFAULT_SA_RELATIONSHIP_KWARGS,
        },
    )
    # Triggers
    webhook: "Webhook" = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={
            "cascade": "all, delete",
            **DEFAULT_SA_RELATIONSHIP_KWARGS,
        },
    )
    schedules: list["Schedule"] | None = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={
            "cascade": "all, delete",
            **DEFAULT_SA_RELATIONSHIP_KWARGS,
        },
    )
    tags: list["Tag"] = Relationship(
        back_populates="workflows",
        link_model=WorkflowTag,
        sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS,
    )


class Webhook(Resource, table=True):
    id: str = Field(
        default_factory=id_factory("wh"), nullable=False, unique=True, index=True
    )
    status: str = "offline"  # "online" or "offline"
    method: str = "POST"
    filters: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))

    # Relationships
    workflow_id: str | None = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )
    workflow: Workflow | None = Relationship(
        back_populates="webhook", sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS
    )

    @computed_field
    @property
    def secret(self) -> str:
        secret = os.getenv("TRACECAT__SIGNING_SECRET")
        if not secret:
            raise ValueError("TRACECAT__SIGNING_SECRET is not set")
        return hashlib.sha256(f"{self.id}{secret}".encode()).hexdigest()

    @computed_field
    @property
    def url(self) -> str:
        return f"{config.TRACECAT__PUBLIC_API_URL}/webhooks/{self.workflow_id}/{self.secret}"


class Schedule(Resource, table=True):
    id: str = Field(
        default_factory=id_factory("sch"), nullable=False, unique=True, index=True
    )
    status: str = "online"  # "online" or "offline"
    cron: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    every: timedelta = Field(..., description="ISO 8601 duration string")
    offset: timedelta | None = Field(None, description="ISO 8601 duration string")
    start_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    end_at: datetime | None = Field(None, description="ISO 8601 datetime string")
    timeout: float | None = Field(
        None,
        description="The maximum number of seconds to wait for the workflow to complete",
    )
    # Relationships
    workflow_id: str | None = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )
    workflow: Workflow | None = Relationship(
        back_populates="schedules",
        sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS,
    )

    # Custom validator for the cron field
    @field_validator("cron")
    def validate_cron(cls, v):
        import croniter

        if not croniter.is_valid(v):
            raise ValueError("Invalid cron string")
        return v


class Action(Resource, table=True):
    """The workspace action state."""

    id: str = Field(
        default_factory=id_factory("act"), nullable=False, unique=True, index=True
    )
    type: str = Field(..., description="The action type, i.e. UDF key")
    title: str
    description: str
    status: str = "offline"  # "online" or "offline"
    inputs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    control_flow: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))

    workflow_id: str | None = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )
    workflow: Workflow | None = Relationship(
        back_populates="actions", sa_relationship_kwargs=DEFAULT_SA_RELATIONSHIP_KWARGS
    )

    @computed_field
    @property
    def key(self) -> str:
        """Workflow-relative key for an Action."""
        return action.key(self.workflow_id, self.id)

    @property
    def ref(self) -> str:
        """Slugified title of the action. Used for references."""
        return action.ref(self.title)


class RegistryRepository(Resource, table=True):
    """A repository of templates and actions."""

    id: UUID4 = Field(default_factory=uuid.uuid4, nullable=False, unique=True)
    origin: str = Field(
        ...,
        description=(
            "Tells you where the template action was created from."
            "Can use this to track the hierarchy of templates."
            "Depending on where the TA was created, this could be a few things:\n"
            "- Git url if created via a git sync\n"
            "- file://<path> if it's a custom action that was created from a file"
            "- None if it's a custom action that was created from scratch"
        ),
        unique=True,
        nullable=False,
    )
    last_synced_at: datetime | None = Field(
        default=None,
        sa_column=Column(TIMESTAMP(timezone=True)),
    )
    commit_sha: str | None = Field(
        default=None,
        description="The SHA of the last commit that was synced from the repository",
    )
    # Relationships
    actions: list["RegistryAction"] = Relationship(
        back_populates="repository",
        sa_relationship_kwargs={
            "cascade": "all, delete",
            **DEFAULT_SA_RELATIONSHIP_KWARGS,
        },
    )


class RegistryAction(Resource, table=True):
    """A registry action.


    A registry action can be a template action or a udf.
    A udf is a python user-defined function that can be used to create new actions.
    A template action is a reusable action that can be used to create new actions.
    Template actions loaded from tracecat base can be cloned but not edited.
    This is to ensure portability of templates across different users/systems.
    Custom template actions can be edited and cloned

    """

    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_registry_action_namespace_name"),
    )

    id: UUID4 = Field(default_factory=uuid.uuid4, nullable=False, unique=True)
    name: str = Field(..., description="The name of the action")
    description: str = Field(..., description="The description of the action")
    namespace: str = Field(..., description="The namespace of the action")
    origin: str = Field(..., description="The origin of the action as a url")
    type: str = Field(..., description="The type of the action")
    default_title: str | None = Field(
        None, description="The default title of the action", nullable=True
    )
    display_group: str | None = Field(
        None, description="The presentation group of the action", nullable=True
    )
    secrets: list[dict[str, Any]] | None = Field(
        None, sa_column=Column(JSONB), description="The secrets required by the action"
    )
    interface: dict[str, Any] = Field(
        ..., sa_column=Column(JSONB), description="The interface of the action"
    )
    implementation: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
        description="The action's implementation",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
        description="The action's options",
    )
    # Relationships
    repository_id: UUID4 = Field(
        sa_column=Column(UUID, ForeignKey("registryrepository.id", ondelete="CASCADE")),
    )
    repository: RegistryRepository = Relationship(back_populates="actions")

    @property
    def action(self):
        return f"{self.namespace}.{self.name}"
