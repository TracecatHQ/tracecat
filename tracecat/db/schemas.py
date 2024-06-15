"""Database schemas for Tracecat."""

from datetime import datetime
from typing import Any, Self

import pyarrow as pa
from pydantic import computed_field, field_validator
from sqlalchemy import JSON, TIMESTAMP, Column, ForeignKey, String, text
from sqlmodel import Field, Relationship, SQLModel

from tracecat import config, registry
from tracecat.auth.credentials import compute_hash, decrypt_object, encrypt_object
from tracecat.dsl.common import DSLInput
from tracecat.types.secrets import SECRET_FACTORY, SecretBase, SecretKeyValue
from tracecat.utils import action_key, gen_id, get_ref

DEFAULT_CASE_ACTIONS = [
    "Active compromise",
    "Ignore",
    "Informational",
    "Investigate",
    "Quarantined",
    "Sinkholed",
]


class Resource(SQLModel):
    """Base class for all resources in the system."""

    surrogate_id: int | None = Field(default=None, primary_key=True, exclude=True)
    owner_id: str
    created_at: datetime = Field(
        sa_type=TIMESTAMP(timezone=True),  # UTC Timestamp
        sa_column_kwargs={
            "server_default": text("(now() AT TIME ZONE 'utc'::text)"),
            "nullable": False,
        },
    )
    updated_at: datetime = Field(
        sa_type=TIMESTAMP(timezone=True),  # UTC Timestamp
        sa_column_kwargs={
            "server_default": text("(now() AT TIME ZONE 'utc'::text)"),
            "onupdate": text("(now() AT TIME ZONE 'utc'::text)"),
            "nullable": False,
        },
    )


class User(Resource, table=True):
    # The id is also the JWT 'sub' claim
    id: str = Field(
        default_factory=gen_id("user"), nullable=False, unique=True, index=True
    )
    tier: str = "free"  # "free" or "premium"
    settings: str | None = None  # JSON-serialized String of settings
    owned_workflows: list["Workflow"] = Relationship(
        back_populates="owner",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    case_actions: list["CaseAction"] = Relationship(back_populates="user")
    case_contexts: list["CaseContext"] = Relationship(back_populates="user")
    secrets: list["Secret"] = Relationship(
        back_populates="owner",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )


class Secret(Resource, table=True):
    id: str = Field(
        default_factory=gen_id("secret"), nullable=False, unique=True, index=True
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
    encrypted_keys: bytes | None = Field(default=None, nullable=True)
    tags: dict[str, str] | None = Field(sa_column=Column(JSON))
    owner_id: str = Field(
        sa_column=Column(String, ForeignKey("user.id", ondelete="CASCADE"))
    )
    owner: User | None = Relationship(back_populates="secrets")

    def _validate_obj(self, value: dict[str, Any]) -> SecretBase:
        if self.type not in SECRET_FACTORY:
            raise ValueError(f"Invalid secret type {self.type!r}")
        return SECRET_FACTORY[self.type].model_validate(value)

    @property
    def keys(self) -> list[SecretKeyValue] | None:
        """Getter: Decrypt the keys and return them as a list of SecretKeyValue objects."""
        if not self.encrypted_keys:
            return None
        obj = decrypt_object(self.encrypted_keys)
        kv = self._validate_obj(obj)
        return [SecretKeyValue(key=k, value=v) for k, v in kv.model_dump().items()]

    @keys.setter
    def keys(self, value: list[SecretKeyValue]) -> None:
        """Setter: Encrypt the keys and store them as bytes."""
        # Convert to dict
        kv = {item.key: item.value.get_secret_value() for item in value}
        self._validate_obj(kv)
        self.encrypted_keys = encrypt_object(kv)


class CaseAction(Resource, table=True):
    id: str = Field(
        default_factory=gen_id("case-act"), nullable=False, unique=True, index=True
    )
    tag: str
    value: str
    user_id: str | None = Field(foreign_key="user.id")
    user: User | None = Relationship(back_populates="case_actions")


class CaseContext(Resource, table=True):
    id: str = Field(
        default_factory=gen_id("case-ctx"), nullable=False, unique=True, index=True
    )
    tag: str
    value: str
    user_id: str | None = Field(foreign_key="user.id")
    user: User | None = Relationship(back_populates="case_contexts")


class CaseEvent(Resource, table=True):
    id: str = Field(
        default_factory=gen_id("case-evt"), nullable=False, unique=True, index=True
    )
    type: str  # The CaseEvent type
    workflow_id: str
    case_id: str
    # Tells us what kind of role modified the case
    initiator_role: str  # "user", "service"
    # Changes: We'll use a dict to store the changes
    # The dict takes key-value pairs where the key is a field name in the Case model
    # The value represents the new value.
    # Possible events:
    # - Key not in dict: The field was not modified
    # - Key in dict with value None: The field was deleted
    # - Key in dict with value: The field was modified
    data: dict[str, str | None] | None = Field(sa_column=Column(JSON))


class UDFSpec(Resource, table=True):
    """UDF spec.

    Used in:
    1. Frontend action library
    2. Frontend integration action form
    """

    id: str = Field(
        default_factory=gen_id("udf"), nullable=False, unique=True, index=True
    )
    description: str
    namespace: str
    key: str
    version: str | None = None
    json_schema: dict[str, Any] | None = Field(sa_column=Column(JSON))
    # Can put the icon url in the metadata
    meta: dict[str, Any] | None = Field(sa_column=Column(JSON))

    @staticmethod
    def from_registry_udf(
        key: str, udf: registry.RegisteredUDF, owner_id: str = "tracecat"
    ) -> Self:
        return UDFSpec(
            owner_id=owner_id,
            key=key,
            description=udf.description,
            namespace=udf.namespace,
            version=udf.version,
            json_schema=udf.construct_schema(),
            meta=udf.metadata,
        )


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
        default_factory=gen_id("wf-defn"), nullable=False, unique=True, index=True
    )
    version: int = Field(..., index=True, description="DSL spec version")
    workflow_id: str = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )

    # DSL content
    content: DSLInput = Field(sa_column=Column(JSON))
    workflow: "Workflow" = Relationship(back_populates="definitions")


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

    id: str = Field(
        default_factory=gen_id("wf"), nullable=False, unique=True, index=True
    )
    title: str
    description: str
    status: str = "offline"  # "online" or "offline"
    version: int | None = None
    entrypoint: str | None = Field(
        None,
        description="ID of the node directly connected to the trigger.",
    )
    object: dict[str, Any] | None = Field(
        sa_column=Column(JSON), description="React flow graph object"
    )
    icon_url: str | None = None
    # Owner
    owner_id: str = Field(
        sa_column=Column(String, ForeignKey("user.id", ondelete="CASCADE"))
    )
    # Relationships
    owner: User | None = Relationship(back_populates="owned_workflows")
    runs: list["WorkflowRun"] | None = Relationship(back_populates="workflow")
    actions: list["Action"] | None = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    definitions: list["WorkflowDefinition"] | None = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    # Triggers
    webhook: "Webhook" = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    schedules: list["Schedule"] | None = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )


class WorkflowRun(Resource, table=True):
    id: str = Field(
        default_factory=gen_id("wf-run"), nullable=False, unique=True, index=True
    )
    status: str = "pending"  # "online" or "offline"
    workflow_id: str | None = Field(foreign_key="workflow.id")
    workflow: Workflow | None = Relationship(back_populates="runs")
    action_runs: list["ActionRun"] | None = Relationship(back_populates="workflow_run")


class Webhook(Resource, table=True):
    id: str = Field(
        default_factory=gen_id("wh"), nullable=False, unique=True, index=True
    )
    status: str = "offline"  # "online" or "offline"
    method: str = "POST"
    filters: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Relationships
    workflow_id: str | None = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )
    workflow: Workflow | None = Relationship(back_populates="webhook")

    @computed_field
    @property
    def secret(self) -> str:
        return compute_hash(self.id)

    @computed_field
    @property
    def url(self) -> str:
        return f"{config.TRACECAT__PUBLIC_RUNNER_URL}/webhooks/{self.workflow_id}/{self.secret}"


class Schedule(Resource, table=True):
    id: str = Field(
        default_factory=gen_id("sch"), nullable=False, unique=True, index=True
    )
    status: str = "offline"  # "online" or "offline"
    cron: str
    entrypoint_payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )

    # Relationships
    workflow_id: str | None = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )
    workflow: Workflow | None = Relationship(back_populates="schedules")

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
        default_factory=gen_id("act"), nullable=False, unique=True, index=True
    )
    type: str = Field(..., description="The action type, i.e. UDF key")
    title: str
    description: str
    status: str = "offline"  # "online" or "offline"
    inputs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    workflow_id: str | None = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )
    workflow: Workflow | None = Relationship(back_populates="actions")

    runs: list["ActionRun"] | None = Relationship(back_populates="action")

    @computed_field
    @property
    def key(self) -> str:
        """Workflow-relative key for an Action."""
        return action_key(self.workflow_id, self.id)

    @property
    def ref(self) -> str:
        """Slugified title of the action. Used for references."""
        return get_ref(self.title)


class ActionRun(Resource, table=True):
    id: str = Field(
        default_factory=gen_id("act-run"), nullable=False, unique=True, index=True
    )
    status: str = "pending"  # "online" or "offline"
    # TODO: This allows action/action_id to be None, which may be undesirable.
    # Need to figure out how to handle this better.
    action_id: str | None = Field(foreign_key="action.id")
    action: Action | None = Relationship(back_populates="runs")
    workflow_run_id: str = Field(foreign_key="workflowrun.id")
    workflow_run: WorkflowRun | None = Relationship(back_populates="action_runs")
    error_msg: str | None = None
    result: str | None = None  # JSON-serialized String of result


CaseSchema = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("owner_id", pa.string(), nullable=False),
        pa.field("workflow_id", pa.string(), nullable=False),
        pa.field("case_title", pa.string(), nullable=False),
        pa.field("payload", pa.string(), nullable=False),  # JSON-serialized
        pa.field("context", pa.string(), nullable=True),  # JSON-serialized
        pa.field("malice", pa.string(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("priority", pa.string(), nullable=False),
        pa.field("action", pa.string(), nullable=True),
        pa.field("suppression", pa.string(), nullable=True),  # JSON-serialized
        pa.field("tags", pa.string(), nullable=True),  # JSON-serialized
        pa.field(
            "created_at", pa.timestamp("us", tz="UTC"), nullable=True
        ),  # JSON-serialized
        pa.field(
            "updated_at", pa.timestamp("us", tz="UTC"), nullable=True
        ),  # JSON-serialized
        # pa.field("_action_vector", pa.list_(pa.float32(), list_size=EMBEDDINGS_SIZE)),
        # pa.field("_payload_vector", pa.list_(pa.float32(), list_size=EMBEDDINGS_SIZE)),
        # pa.field("_context_vector", pa.list_(pa.float32(), list_size=EMBEDDINGS_SIZE)),
    ]
)
