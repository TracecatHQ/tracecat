import os
from pathlib import Path
from uuid import uuid4

import lancedb
import pyarrow as pa
import tantivy
from pydantic import computed_field
from slugify import slugify
from sqlmodel import Field, Relationship, SQLModel, create_engine

from tracecat import auth
from tracecat.llm import TYPE_SLUGS

STORAGE_PATH = Path(os.path.expanduser("~/.tracecat/storage"))
EMBEDDINGS_SIZE = os.environ.get("TRACECAT__EMBEDDINGS_SIZE", 512)


class Workflow(SQLModel, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    title: str
    description: str
    status: str = "offline"  # "online" or "offline"
    object: str | None = None  # JSON-serialized String of react flow object
    actions: list["Action"] | None = Relationship(back_populates="workflow")
    runs: list["WorkflowRun"] | None = Relationship(back_populates="workflow")
    webhooks: list["Webhook"] | None = Relationship(back_populates="workflow")


class WorkflowRun(SQLModel, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    status: str = "pending"  # "online" or "offline"
    workflow_id: str = Field(foreign_key="workflow.id")
    workflow: Workflow | None = Relationship(back_populates="runs")


class Action(SQLModel, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    type: str
    title: str
    description: str
    status: str = "offline"  # "online" or "offline"
    inputs: str | None = None  # JSON-serialized String of inputs
    workflow_id: str | None = Field(foreign_key="workflow.id")
    workflow: Workflow | None = Relationship(back_populates="actions")

    @computed_field
    @property
    def action_key(self) -> str:
        slug = slugify(self.title, separator="_")
        return f"{self.id}.{slug}"

    @computed_field
    @property
    def type_slug(self) -> str:
        """Slug of the action type.

        In the Python runner world we use this as an Action disctiminator.

        Example
        -------
        - "HTTP Request" --> "http_request"
        """
        slug = slugify(self.type, separator="_")
        if slug in TYPE_SLUGS:
            return f"llm.{slug}"
        return slug


class Webhook(SQLModel, table=True):
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
    )
    action_id: str | None = Field(foreign_key="action.id")
    workflow_id: str | None = Field(foreign_key="workflow.id")
    workflow: Workflow | None = Relationship(back_populates="webhooks")

    @computed_field
    @property
    def secret(self) -> str:
        return auth.compute_hash(self.id)


def create_db_engine():
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    sqlite_uri = f"sqlite:////{STORAGE_PATH}/database.db"
    engine = create_engine(
        sqlite_uri, echo=True, connect_args={"check_same_thread": False}
    )
    return engine


def create_events_index():
    index_path = STORAGE_PATH / "events_index"
    index_path.mkdir(parents=True, exist_ok=True)
    event_schema = (
        tantivy.Schema()
        .add_string_field("id", stored=True)
        .add_string_field("workflow_id", stored=True)
        .add_string_field("workflow_run_id", stored=True)
        .add_string_field("action_id", stored=True)
        .add_string_field("action_type", stored=True)
        .add_date_field("published_at", stored=True)
        .add_json_field("event", stored=True)
    )
    index = tantivy.Index(event_schema, path=index_path)
    return index


def create_vdb_conn():
    db = lancedb.connect(STORAGE_PATH / "vector.db")
    return db


CaseSchema = pa.schema(
    [
        pa.field("id", pa.int64(), nullable=False),
        pa.field("workflow_id", pa.int64(), nullable=False),
        pa.field("title", pa.string(), nullable=False),
        pa.field("payload", pa.dictionary(pa.string(), pa.string()), nullable=False),
        pa.field("malice", pa.string(), nullable=False),
        pa.field("context", pa.dictionary(pa.string(), pa.string()), nullable=False),
        pa.field("suppression", pa.dictionary(pa.string(), pa.bool_()), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("priority", pa.string(), nullable=False),
        pa.field("_payload_vector", pa.list_(pa.float32(), list_size=EMBEDDINGS_SIZE)),
        pa.field("_context_vector", pa.list_(pa.float32(), list_size=EMBEDDINGS_SIZE)),
    ]
)

TaskSchema = pa.schema(
    [
        pa.field("id", pa.int64(), nullable=False),
        pa.field("case_id", pa.int64(), nullable=False),
        pa.field("description", pa.string(), nullable=False),
        pa.field("is_done", pa.bool_(), nullable=False),
        pa.field("_vector", pa.list_(pa.float32(), list_size=EMBEDDINGS_SIZE)),
    ]
)


def initialize_db() -> None:
    # Relational table
    engine = create_db_engine()
    SQLModel.metadata.create_all(engine)

    # VectorDB
    db = create_vdb_conn()
    db.create_table("cases", schema=CaseSchema)
    db.create_table("tasks", schema=TaskSchema)

    # Search
    create_events_index()
