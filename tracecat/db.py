from sqlmodel import Field, Relationship, SQLModel, create_engine
from uuid import uuid4


class Workflow(SQLModel, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    title: str
    description: str
    actions: list["Action"] | None = Relationship(back_populates="workflow")


class Action(SQLModel, table=True):
    id: str | None = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    title: str
    description: str
    inputs: str | None = None  # JSON-serialized String of inputs
    connects_to: list[str] | None = None # List of Action IDs
    workflow_id: str = Field(foreign_key="workflow.id")
    workflow: Workflow = Field(back_populates="actions")


def create_db_engine():
    sqlite_uri = f"sqlite://.tracecat/storage/database.db"
    engine = create_engine(
        sqlite_uri,
        echo=True,
        connect_args={"check_same_thread": False}
    )
    return engine


def initialize_db() -> None:
    engine = create_db_engine()
    SQLModel.metadata.create_all(engine)
