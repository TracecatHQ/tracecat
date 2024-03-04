import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select

from tracecat.db import Action, Workflow, create_db_engine, initialize_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_db()
    yield


app = FastAPI(lifespan=lifespan)

origins = [
    "http://localhost",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the API."}


class ActionResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    inputs: dict[str, Any] | None


class WorkflowResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    actions: dict[str, list[ActionResponse]]
    graph: dict[str, list[str]] | None  # Adjacency list of Action IDs


class ActionMetadataResponse(BaseModel):
    id: str
    workflow_id: str
    type: str
    title: str
    description: str
    status: str


class WorkflowMetadataResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str


### Workflows


@app.get("/workflows")
def list_workflows() -> list[WorkflowMetadataResponse]:
    """List all Workflows in database."""
    with Session(create_db_engine()) as session:
        statement = select(Workflow)
        results = session.exec(statement)
        workflows = results.all()
    workflow_metadata = [
        WorkflowMetadataResponse(
            id=workflow.id,
            title=workflow.title,
            description=workflow.description,
            status=workflow.status,
        )
        for workflow in workflows
    ]
    return workflow_metadata


class CreateWorkflowParams(BaseModel):
    title: str
    description: str


@app.post("/workflows", status_code=201)
def create_workflow(params: CreateWorkflowParams) -> WorkflowMetadataResponse:
    """Create new Workflow with title and description."""

    workflow = Workflow(title=params.title, description=params.description)
    with Session(create_db_engine()) as session:
        session.add(workflow)
        session.commit()
        session.refresh(workflow)

    return WorkflowMetadataResponse(
        id=workflow.id,
        title=params.title,
        description=params.description,
        status=params.status,
    )


@app.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> WorkflowResponse:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""

    with Session(create_db_engine()) as session:
        # Get Workflow given workflow_id
        statement = select(Workflow).where(Workflow.id == workflow_id)
        result = session.exec(statement)
        workflow = result.one()

        # List all Actions related to `workflow_id`
        statement = select(Action).where(Action.workflow_id == workflow_id)
        results = session.exec(statement)
        actions = results.all()

        graph = None
        if len(actions) > 0 and workflow.object is not None:
            # Process react flow object into adjacency list
            graph = workflow.object

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
        status=workflow.status,
        actions=actions_responses,
        graph=graph,
    )
    return workflow_response


class UpdateWorkflowParams(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    object: str | None = None


@app.post("/workflows/{workflow_id}", status_code=204)
def update_workflow(
    workflow_id: str,
    params: UpdateWorkflowParams,
) -> None:
    """Update Workflow."""

    with Session(create_db_engine()) as session:
        statement = select(Workflow).where(Workflow.id == workflow_id)
        result = session.exec(statement)
        workflow = result.one()

        if params.title is not None:
            workflow.title = params.title
        if params.description is not None:
            workflow.description = params.description
        if params.status is not None:
            workflow.status = params.status
        if params.object is not None:
            workflow.object = params.object

        session.add(workflow)
        session.commit()


### Actions


@app.get("/actions")
def list_actions(workflow_id: str) -> list[ActionMetadataResponse]:
    """List all Actions related to `workflow_id`."""
    with Session(create_db_engine()) as session:
        statement = select(Action).where(Action.workflow_id == workflow_id)
        results = session.exec(statement)
        actions = results.all()
    action_metadata = [
        ActionMetadataResponse(
            id=action.id,
            workflow_id=workflow_id,
            type=action.type,
            title=action.title,
            description=action.description,
            status=action.status,
        )
        for action in actions
    ]
    return action_metadata


class CreateActionParams(BaseModel):
    workflow_id: str
    type: str
    title: str


@app.post("/actions")
def create_action(params: CreateActionParams) -> ActionMetadataResponse:
    with Session(create_db_engine()) as session:
        action = Action(
            workflow_id=params.workflow_id,
            type=params.type,
            title=params.title,
            description="",  # Default to empty string
        )
        session.add(action)
        session.commit()
        session.refresh(action)
    action_metadata = ActionMetadataResponse(
        id=action.id,
        workflow_id=params.workflow_id,
        type=params.type,
        title=action.title,
        description=action.description,
        status=action.status,
    )
    return action_metadata


@app.get("/actions/{action_id}")
def get_action(action_id: str, workflow_id: str) -> ActionResponse:
    with Session(create_db_engine()) as session:
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
        status=action.status,
        inputs=json.loads(action.inputs) if action.inputs else None,
    )


class UpdateWorkflowParams(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    object: str | None = None


@app.get("/actions/{action_id}", status_code=204)
def update_action(params: UpdateWorkflowParams) -> None:
    with Session(create_db_engine()) as session:
        # Fetch the action by id
        statement = select(Action).where(Action.id == params.action_id)
        result = session.exec(statement)
        action = result.one()

        if params.title is not None:
            action.title = params.title
        if params.description is not None:
            action.description = params.description
        if params.status is not None:
            action.status = params.status
        if params.inputs is not None:
            action.inputs = params.inputs

        session.add(action)
        session.commit()


@app.delete("/actions/{action_id}", status_code=204)
def delete_action(action_id: str) -> None:
    with Session(create_db_engine()) as session:
        statement = select(Action).where(Action.id == action_id)
        result = session.exec(statement)
        action = result.one()
        session.delete(action)
        session.commit()
