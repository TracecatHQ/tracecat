import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from sqlmodel import Session, select

from tracecat.db import Action, Workflow, create_db_engine, initialize_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_db()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the API."}


def create_session() -> Session:
    with create_db_engine().connect() as connection:
        with Session(connection) as session:
            yield session


class ActionResponse(BaseModel):
    id: str
    title: str
    description: str
    inputs: dict[str, Any] | None


class WorkflowResponse(BaseModel):
    id: str
    title: str
    description: str
    actions: dict[str, list[ActionResponse]]
    graph: dict[str, list[str]] | None  # Adjacency list of Action IDs


class ActionTitleResponse(BaseModel):
    id: str
    title: str
    description: str


class WorkflowTitleResponse(BaseModel):
    id: str
    title: str
    description: str


### Workflows


@app.get("/workflows")
def list_workflows() -> list[WorkflowTitleResponse]:
    """List all Workflows in database."""
    with create_session() as session:
        statement = select(Workflow)
        results = session.exec(statement)
        workflows = results.all()
    workflow_titles = [
        WorkflowTitleResponse(
            id=workflow.id, title=workflow.title, description=workflow.description
        )
        for workflow in workflows
    ]
    return workflow_titles


@app.post("/workflows", status_code=201)
def create_workflow(title: str, description: str) -> WorkflowTitleResponse:
    """Create new Workflow with title and description."""

    workflow = Workflow(title=title, description=description)
    with create_session() as session:
        session.add(workflow)
        session.commit()

    return WorkflowTitleResponse(id=workflow.id, title=title, description=description)


@app.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> WorkflowResponse:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""

    with create_session() as session:
        # Get Workflow given workflow_id
        statement = select(Workflow).where(Workflow.id == workflow_id)
        result = session.exec(statement)
        workflow = result.one()

        # List all Actions related to `workflow_id`
        statement = select(Action).where(Action.workflow_id == workflow_id)
        results = session.exec(statement)
        actions = results.all()

        graph = None
        if len(actions) > 0:
            # For each Action, get all connected Actions from Action IDs in `connects_to`
            graph = {}
            for action in actions:
                if action.connects_to:
                    graph[action.id] = action.connects_to
                else:
                    graph[action.id] = []

    actions_responses = [
        ActionResponse(
            id=action.id,
            title=action.title,
            description=action.description,
            inputs=json.loads(action.inputs) if action.inputs else None,
        )
        for action in actions
    ]
    workflow_response = WorkflowResponse(
        id=workflow.id,
        title=workflow.title,
        description=workflow.description,
        actions=actions_responses,
        graph=graph,
    )
    return workflow_response


@app.post("/workflows/{workflow_id}", status_code=204)
def update_workflow(
    workflow_id: str,
    title: str | None,
    description: str | None,
) -> WorkflowTitleResponse:
    """Update Workflow."""

    with create_session() as session:
        statement = select(Workflow).where(Workflow.id == workflow_id)
        result = session.exec(statement)
        workflow = result.one()

        if title is not None:
            workflow.title = title
        if description is not None:
            workflow.description = description

        session.add(workflow)
        session.commit()

    return WorkflowTitleResponse(
        id=workflow_id, title=workflow.title, description=workflow.description
    )


### Actions


@app.get("/actions")
def list_actions(workflow_id: str) -> list[ActionTitleResponse]:
    """List all Actions related to `workflow_id`."""
    with create_session() as session:
        statement = select(Action).where(Action.workflow_id == workflow_id)
        results = session.exec(statement)
        actions = results.all()
    action_titles = [
        ActionTitleResponse(
            id=action.id, title=action.title, description=action.description
        )
        for action in actions
    ]
    return action_titles


@app.post("/actions")
def create_action(
    workflow_id: str,
    title: str,
    description: str,
) -> ActionTitleResponse:
    with create_session() as session:
        action = Action(
            workflow_id=workflow_id,
            title=title,
            description=description,
        )
        session.add(action)
        session.commit()
        session.refresh(action)
    action_title = ActionTitleResponse(
        id=action.id, title=action.title, description=action.description
    )
    return action_title


@app.get("/actions/{action_id}")
def get_action(action_id: str, workflow_id: int) -> ActionResponse:
    with create_session() as session:
        statement = (
            select(Action)
            .where(Action.id == action_id)
            .where(Action.workflow_id == workflow_id)
        )
        result = session.exec(statement)
        action = result.one()
    return ActionResponse(
        id=action.id,
        title=action.title,
        description=action.description,
        inputs=json.loads(action.inputs) if action.inputs else None,
    )


@app.get("/actions/{action_id}", status_code=204)
def update_action(
    action_id: str | None,
    title: str | None,
    description: str | None,
    connects_to: list[str] | None,
    inputs: str | None,  # JSON-serialized string
) -> ActionResponse:
    with create_session() as session:
        # Fetch the action by id
        statement = select(Action).where(Action.id == action_id)
        result = session.exec(statement)
        action = result.one()

        if title is not None:
            action.title = title
        if description is not None:
            action.description = description
        if connects_to is not None:
            action.connects_to = connects_to
        if inputs is not None:
            action.inputs = inputs

        session.add(action)
        session.commit()

    return ActionResponse(
        id=action.id,
        title=action.title,
        description=action.description,
        inputs=json.loads(action.inputs) if action.inputs else None,
    )


@app.delete("/actions/{action_id}", status_code=204)
def delete_action(action_id: str) -> None:
    with create_session() as session:
        statement = select(Action).where(Action.id == action_id)
        result = session.exec(statement)
        action = result.one()
        session.delete(action)
        session.commit()
    return None
