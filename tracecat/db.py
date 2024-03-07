import os
from pathlib import Path
from uuid import uuid4

from pydantic import computed_field
from slugify import slugify
from sqlmodel import Field, Relationship, SQLModel, create_engine


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


class Webhook(SQLModel, table=True):
    """Webhook is a URL that can be called to trigger a workflow.

    Notes
    -----
    - We need this because we need a way to trigger a workflow from an external source.
    - External sources only have access to the path
    """

    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    secret: str
    action_id: str | None = Field(foreign_key="action.id")
    workflow_id: str | None = Field(foreign_key="workflow.id")
    workflow: Workflow | None = Relationship(back_populates="webhooks")


def create_db_engine():
    storage_path = os.path.expanduser("~/.tracecat/storage")
    Path(storage_path).mkdir(parents=True, exist_ok=True)
    sqlite_uri = f"sqlite:////{storage_path}/database.db"
    engine = create_engine(
        sqlite_uri, echo=True, connect_args={"check_same_thread": False}
    )
    return engine


def initialize_db() -> None:
    engine = create_db_engine()
    SQLModel.metadata.create_all(engine)
