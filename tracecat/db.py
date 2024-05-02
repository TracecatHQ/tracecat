import json
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self
from uuid import uuid4

import lancedb
import pyarrow as pa
from croniter import croniter
from pydantic import computed_field, field_validator
from slugify import slugify
from sqlalchemy import JSON, TIMESTAMP, Column, Engine, ForeignKey, String, text
from sqlmodel import (
    Field,
    Relationship,
    Session,
    SQLModel,
    create_engine,
    delete,
    select,
)

from tracecat import auth, integrations
from tracecat.config import (
    TRACECAT__APP_ENV,
    TRACECAT__RUNNER_URL,
    TRACECAT_DIR,
)
from tracecat.labels.mitre import get_mitre_tactics_techniques
from tracecat.logger import standard_logger
from tracecat.types.secrets import SECRET_FACTORY, SecretBase, SecretKeyValue

if TYPE_CHECKING:
    from tracecat.integrations import IntegrationSpec

logger = standard_logger("db")


STORAGE_PATH = TRACECAT_DIR / "storage"
DEFAULT_CASE_ACTIONS = [
    "Active compromise",
    "Ignore",
    "Informational",
    "Investigate",
    "Quarantined",
    "Sinkholed",
]
STORAGE_PATH.mkdir(parents=True, exist_ok=True)
TRACECAT__DB_URI = os.environ["TRACECAT__DB_URI"]


class User(SQLModel, table=True):
    # The id is also the JWT 'sub' claim
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
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


class Resource(SQLModel):
    """Base class for all resources in the system."""

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


class Secret(Resource, table=True):
    """Secret model.

    A secret can contain an arbitrary number of keys.
    e.g.
    """

    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    type: str  # "custom", "token", "oauth2"
    name: str = Field(..., max_length=255, index=True, nullable=False)
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
        if not self.encrypted_keys:
            return None
        obj = auth.decrypt_object(self.encrypted_keys)
        kv = self._validate_obj(obj)
        return [SecretKeyValue(key=k, value=v) for k, v in kv.model_dump().items()]

    @keys.setter
    def keys(self, value: list[SecretKeyValue]) -> None:
        # Convert to dict
        kv = {item.key: item.value for item in value}
        self._validate_obj(kv)
        self.encrypted_keys = auth.encrypt_object(kv)


class Editor(SQLModel, table=True):
    user_id: str | None = Field(default=None, foreign_key="user.id", primary_key=True)
    workflow_id: str | None = Field(
        default=None, foreign_key="workflow.id", primary_key=True
    )


class CaseAction(Resource, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    tag: str
    value: str
    user_id: str | None = Field(foreign_key="user.id")
    user: User | None = Relationship(back_populates="case_actions")


class CaseContext(Resource, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    tag: str
    value: str
    user_id: str | None = Field(foreign_key="user.id")
    user: User | None = Relationship(back_populates="case_contexts")


class CaseEvent(Resource, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    type: str  # The CaseEvent type
    workflow_id: str | None = Field(foreign_key="workflow.id")
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


class Integration(Resource, table=True):
    """Integration spec.

    Used in:
    1. Frontend integrations library
    2. Frontend integration action form
    """

    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    name: str  # Human readable name
    description: str  # Possibly redundant
    docstring: str
    parameters: str  # JSON-serialized String of form schema
    platform: str  # e.g. AWS, Google Workspace, Crowdstrike
    icon_url: str | None = None

    @classmethod
    def from_spec(cls, spec: "IntegrationSpec") -> Self:
        return cls(
            **spec.model_dump(exclude={"parameters"}),
            parameters=json.dumps([p.model_dump() for p in spec.parameters]),
            owner_id="tracecat",
        )

    @computed_field
    @property
    def key(self) -> str:
        return f"integrations.{self.platform}.{self.name}"


class Workflow(Resource, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    title: str
    description: str
    status: str = "offline"  # "online" or "offline"
    object: str | None = None  # JSON-serialized String of react flow object
    icon_url: str | None = None
    # Owner
    owner_id: str = Field(
        sa_column=Column(String, ForeignKey("user.id", ondelete="CASCADE"))
    )
    owner: User | None = Relationship(back_populates="owned_workflows")
    runs: list["WorkflowRun"] | None = Relationship(back_populates="workflow")
    actions: list["Action"] | None = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    webhooks: list["Webhook"] | None = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    schedules: list["WorkflowSchedule"] | None = Relationship(
        back_populates="workflow",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )

    @computed_field
    @property
    def key(self) -> str:
        slug = slugify(self.title, separator="_")
        return f"{self.id}.{slug}"


class WorkflowRun(Resource, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    status: str = "pending"  # "online" or "offline"
    workflow_id: str | None = Field(foreign_key="workflow.id")
    workflow: Workflow | None = Relationship(back_populates="runs")
    action_runs: list["ActionRun"] | None = Relationship(back_populates="workflow_run")


class WorkflowSchedule(Resource, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    cron: str
    entrypoint_key: str
    entrypoint_payload: str  # JSON-serialized String of payload
    workflow_id: str | None = Field(foreign_key="workflow.id")
    workflow: Workflow | None = Relationship(back_populates="schedules")

    # Custom validator for the cron field
    @field_validator("cron")
    def validate_cron(cls, v):
        if not croniter.is_valid(v):
            raise ValueError("Invalid cron string")
        return v


class Action(Resource, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    type: str
    title: str
    description: str
    status: str = "offline"  # "online" or "offline"
    inputs: str | None = None  # JSON-serialized String of inputs
    workflow_id: str | None = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )
    workflow: Workflow | None = Relationship(back_populates="actions")

    runs: list["ActionRun"] | None = Relationship(back_populates="action")

    @computed_field
    @property
    def key(self) -> str:
        slug = slugify(self.title, separator="_")
        return f"{self.id}.{slug}"


class ActionRun(Resource, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    status: str = "pending"  # "online" or "offline"
    # TODO: This allows action/action_id to be None, which may be undesirable.
    # Need to figure out how to handle this better.
    action_id: str | None = Field(foreign_key="action.id")
    action: Action | None = Relationship(back_populates="runs")
    workflow_run_id: str = Field(foreign_key="workflowrun.id")
    workflow_run: WorkflowRun | None = Relationship(back_populates="action_runs")
    error_msg: str | None = None
    result: str | None = None  # JSON-serialized String of result


class Webhook(Resource, table=True):
    """Webhook is a URL that can be called to trigger a workflow.

    Notes
    -----
    - We need this because we need a way to trigger a workflow from an external source.
    - External sources only have access to the path
    """

    id: str | None = Field(
        default_factory=lambda: uuid4().hex,
        primary_key=True,
        description="Webhook path",
        alias="path",
    )
    action_id: str | None = Field(
        sa_column=Column(String, ForeignKey("action.id", ondelete="CASCADE"))
    )
    workflow_id: str | None = Field(
        sa_column=Column(String, ForeignKey("workflow.id", ondelete="CASCADE"))
    )
    workflow: Workflow | None = Relationship(back_populates="webhooks")

    @computed_field
    @property
    def secret(self) -> str:
        return auth.compute_hash(self.id)

    @computed_field
    @property
    def url(self) -> str:
        return f"{TRACECAT__RUNNER_URL}/webhook/{self.id}/{self.secret}"


def create_db_engine() -> Engine:
    if TRACECAT__APP_ENV == "production":
        # Postgres
        engine_kwargs = {
            "pool_timeout": 30,
            "pool_recycle": 3600,
            "connect_args": {"sslmode": "require"},
        }
    elif TRACECAT__APP_ENV == "local":
        # SQLite disk-based database
        engine_kwargs = {"connect_args": {"check_same_thread": False}}
    else:
        # Postgres as default
        engine_kwargs = {
            "pool_timeout": 30,
            "pool_recycle": 3600,
            "connect_args": {"sslmode": "disable"},
        }
    engine = create_engine(TRACECAT__DB_URI, **engine_kwargs)
    return engine


def create_vdb_conn() -> lancedb.DBConnection:
    db = lancedb.connect(STORAGE_PATH / "vector.db")
    return db


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


def initialize_db() -> Engine:
    # Relational table
    engine = create_db_engine()
    SQLModel.metadata.create_all(engine)

    # VectorDB
    db = create_vdb_conn()
    db.create_table("cases", schema=CaseSchema, exist_ok=True)

    with Session(engine) as session:
        # Add TTPs to context table only if context table is empty
        case_contexts_count = session.exec(select(CaseContext)).all()
        if len(case_contexts_count) == 0:
            mitre_labels = get_mitre_tactics_techniques()
            mitre_contexts = [
                CaseContext(owner_id="tracecat", tag="mitre", value=label)
                for label in mitre_labels
            ]
            session.add_all(mitre_contexts)
            session.commit()

        case_actions_count = session.exec(select(CaseAction)).all()
        if len(case_actions_count) == 0:
            default_actions = [
                CaseAction(owner_id="tracecat", tag="case_action", value=case_action)
                for case_action in DEFAULT_CASE_ACTIONS
            ]
            session.add_all(default_actions)
            session.commit()
        # We might be ok just overwriting the integrations table?
        # Add integrations to integrations table regardless of whether it's empty
        session.exec(delete(Integration))
        session.add_all(
            Integration.from_spec(spec)
            for spec in integrations.registry.get_registered_integration_specs()
        )
        session.commit()
    return engine


def clone_workflow(
    workflow: Workflow,
    session: Session,
    new_owner_id: str,
) -> Workflow:
    """
    Clones a Resource, including its relationships (up to a certain depth).

    :param model_instance: The SQLModel instance to clone.
    :param session: The SQLModel session.
    :param _depth: Current depth level, used to limit recursive depth.
    :return: The cloned instance.
    """

    # Create a new instance of the same model without the primary key
    cloned_workflow = Workflow(
        **workflow.model_dump(
            exclude={"id", "created_at", "updated_at", "owner_id", "object", "status"}
        ),
        owner_id=new_owner_id,
        status="offline",
    )

    # Iterate over relationships and clone them
    action_replacements = {}
    for action in workflow.actions:
        # Special treatment for webhook actions:
        # Need to update the action.path

        cloned_action = Action(
            owner_id=new_owner_id,
            workflow_id=cloned_workflow.id,
            **action.model_dump(
                exclude={"id", "created_at", "updated_at", "owner_id", "workflow_id"}
            ),
        )

        action_inputs: dict[str, str] = json.loads(cloned_action.inputs)
        if action.type == "webhook":
            cloned_webhook = Webhook(
                owner_id=new_owner_id,
                action_id=cloned_action.id,
                workflow_id=cloned_workflow.id,
            )
            # Update the action inputs to point to the new webhook path
            action_inputs.update(path=cloned_webhook.id, secret=cloned_webhook.secret)

            # Assert that there's a new computed secret
            session.add(cloned_webhook)
        cloned_action.inputs = json.dumps(action_inputs)
        action_replacements[action.id] = (cloned_action.id, action_inputs)
        session.add(cloned_action)

    # For each action in the workflow, update the workflow object
    graph = json.loads(workflow.object)
    for edge in graph["edges"]:
        edge["source"] = action_replacements[edge["source"]][0]
        edge["target"] = action_replacements[edge["target"]][0]
        edge["id"] = f"{edge['source']}-{edge['target']}"
    for node in graph["nodes"]:
        new_id, new_inputs = action_replacements[node["id"]]
        node["id"] = new_id
        node["data"].update(id=new_id, inputs=new_inputs, selected=False)
    cloned_workflow.object = json.dumps(graph)

    session.add(cloned_workflow)
    return cloned_workflow
