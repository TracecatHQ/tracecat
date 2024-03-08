import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

import polars as pl
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from tracecat.db import (
    Action,
    Webhook,
    Workflow,
    WorkflowRun,
    create_db_engine,
    create_events_index,
    initialize_db,
)


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
    type: str
    title: str
    description: str
    status: str
    inputs: dict[str, Any] | None
    key: str  # Computed field
    type_slug: str | None = None  # Computed field


class WorkflowResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    actions: dict[str, ActionResponse]
    object: dict[str, Any] | None  # React Flow object


class ActionMetadataResponse(BaseModel):
    id: str
    workflow_id: str
    type: str
    title: str
    description: str
    status: str
    key: str


class WorkflowMetadataResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str


class WorkflowRunResponse(BaseModel):
    id: str
    workflow_id: str
    status: str


class WorkflowRunMetadataResponse(BaseModel):
    id: str
    workflow_id: str
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
        title=workflow.title,
        description=workflow.description,
        status=workflow.status,
    )


@app.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> WorkflowResponse:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""

    with Session(create_db_engine()) as session:
        # Get Workflow given workflow_id
        statement = select(Workflow).where(Workflow.id == workflow_id)
        result = session.exec(statement)
        try:
            workflow = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

        # List all Actions related to `workflow_id`
        statement = select(Action).where(Action.workflow_id == workflow_id)
        results = session.exec(statement)
        actions = results.all()

        object = None
        if workflow.object is not None:
            # Process react flow object into adjacency list
            object = json.loads(workflow.object)

    actions_responses = {
        action.id: ActionResponse(
            id=action.id,
            type=action.type,
            title=action.title,
            description=action.description,
            status=action.status,
            inputs=json.loads(action.inputs) if action.inputs else None,
            key=action.action_key,
            type_slug=action.type_slug,
        )
        for action in actions
    }
    workflow_response = WorkflowResponse(
        id=workflow.id,
        title=workflow.title,
        description=workflow.description,
        status=workflow.status,
        actions=actions_responses,
        object=object,
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


### Workflow Runs


@app.post("/workflows/{workflow_id}/runs", status_code=status.HTTP_201_CREATED)
def create_workflow_run(workflow_id: str) -> WorkflowRunMetadataResponse:
    """Create a Workflow Run."""

    workflow_run = WorkflowRun(workflow_id=workflow_id)
    with Session(create_db_engine()) as session:
        session.add(workflow_run)
        session.commit()
        session.refresh(workflow_run)

    return WorkflowRunMetadataResponse(
        id=workflow_run.id,
        workflow_id=workflow_id,
        status=workflow_run.status,
    )


@app.get("/workflows/{workflow_id}/runs")
def list_workflow_runs(workflow_id: str) -> list[WorkflowRunMetadataResponse]:
    """List all Workflow Runs for a Workflow."""
    with Session(create_db_engine()) as session:
        statement = select(WorkflowRun).where(WorkflowRun.id == workflow_id)
        results = session.exec(statement)
        workflow_runs = results.all()

    workflow_runs_metadata = [
        WorkflowRunMetadataResponse(
            id=workflow_run.id,
            workflow_id=workflow_run.workflow_id,
            status=workflow_run.status,
        )
        for workflow_run in workflow_runs
    ]
    return workflow_runs_metadata


@app.get("/workflows/{workflow_id}/runs/{workflow_run_id}")
def get_workflow_run(workflow_id: str, workflow_run_id: str) -> WorkflowRunResponse:
    """Return WorkflowRun as title, description, list of Action JSONs, adjacency list of Action IDs."""

    with Session(create_db_engine()) as session:
        # Get Workflow given workflow_id
        statement = select(WorkflowRun).where(
            WorkflowRun.id == workflow_run_id,
            WorkflowRun.workflow_id == workflow_id,  # Redundant, but for clarity
        )
        result = session.exec(statement)
        workflow_run = result.one()

    return WorkflowRunResponse(
        id=workflow_run.id,
        workflow_id=workflow_run.workflow_id,
        status=workflow_run.status,
    )


class UpdateWorkflowRunParams(BaseModel):
    status: str | None = None


@app.post(
    "/workflows/{workflow_id}/runs/{workflow_run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def update_workflow_run(
    workflow_id: str,
    workflow_run_id: str,
    params: UpdateWorkflowRunParams,
) -> None:
    """Update Workflow."""

    with Session(create_db_engine()) as session:
        statement = select(WorkflowRun).where(
            WorkflowRun.id == workflow_run_id,
            WorkflowRun.workflow_id == workflow_id,
        )
        result = session.exec(statement)
        workflow_run = result.one()

        if params.status is not None:
            workflow_run.status = params.status

        session.add(workflow_run)
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
            key=action.action_key,
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

        if params.type.lower() == "webhook":
            create_webhook(
                CreateWebhookParams(action_id=action.id, workflow_id=params.workflow_id)
            )
    action_metadata = ActionMetadataResponse(
        id=action.id,
        workflow_id=params.workflow_id,
        type=params.type,
        title=action.title,
        description=action.description,
        status=action.status,
        key=action.action_key,
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

        inputs: dict[str, Any] = json.loads(action.inputs) if action.inputs else {}
        # Precompute webhook response
        # Alias webhook.id as path
        if action.type.lower() == "webhook":
            webhook_response = search_webhooks(action_id=action.id)
            inputs |= {"path": webhook_response.id, "secret": webhook_response.secret}
    return ActionResponse(
        id=action.id,
        type=action.type,
        title=action.title,
        description=action.description,
        status=action.status,
        inputs=None if len(inputs) == 0 else inputs,
        key=action.action_key,
        type_slug=action.type_slug,
    )


class UpdateActionParams(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    inputs: str | None = None


@app.post("/actions/{action_id}")
def update_action(action_id: str, params: UpdateActionParams) -> ActionResponse:
    with Session(create_db_engine()) as session:
        # Fetch the action by id
        statement = select(Action).where(Action.id == action_id)
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
        session.refresh(action)

    return ActionResponse(
        id=action.id,
        type=action.type,
        title=action.title,
        description=action.description,
        status=action.status,
        inputs=json.loads(action.inputs) if action.inputs else None,
        key=action.action_key,
        type_slug=action.type_slug,
    )


@app.delete("/actions/{action_id}", status_code=204)
def delete_action(action_id: str) -> None:
    with Session(create_db_engine()) as session:
        statement = select(Action).where(Action.id == action_id)
        result = session.exec(statement)
        action = result.one()
        session.delete(action)
        session.commit()


### Webhooks


class CreateWebhookParams(BaseModel):
    action_id: str
    workflow_id: str


class WebhookMetadataResponse(BaseModel):
    id: str
    action_id: str
    workflow_id: str
    secret: str


@app.put("/webhooks", status_code=status.HTTP_201_CREATED)
def create_webhook(params: CreateWebhookParams) -> WebhookMetadataResponse:
    """Create a new Webhook."""
    webhook = Webhook(
        action_id=params.action_id,
        workflow_id=params.workflow_id,
    )
    with Session(create_db_engine()) as session:
        session.add(webhook)
        session.commit()
        session.refresh(webhook)

    return WebhookMetadataResponse(
        id=webhook.id,
        action_id=webhook.action_id,
        workflow_id=webhook.workflow_id,
        secret=webhook.secret,
    )


class WebhookResponse(BaseModel):
    id: str
    secret: str
    action_id: str
    workflow_id: str


class GetWebhookParams(BaseModel):
    webhook_id: str | None = None
    path: str | None = None


@app.get("/webhooks/{webhook_id}")
def get_webhook(webhook_id: str) -> WebhookResponse:
    with Session(create_db_engine()) as session:
        statement = select(Webhook).where(Webhook.id == webhook_id)
        result = session.exec(statement)
        webhook = result.one()
    webhook_response = WebhookResponse(
        id=webhook.id,
        secret=webhook.secret,
        action_id=webhook.action_id,
        workflow_id=webhook.workflow_id,
    )
    return webhook_response


@app.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(webhook_id: str) -> None:
    """Delete a Webhook by ID."""
    with Session(create_db_engine()) as session:
        statement = select(Webhook).where(Webhook.id == webhook_id)
        result = session.exec(statement)
        webhook = result.one()
        session.delete(webhook)
        session.commit()


@app.get("/webhooks")
def list_webhooks(workflow_id: str) -> list[WebhookResponse]:
    """List all Webhooks for a workflow."""
    with Session(create_db_engine()) as session:
        statement = select(Webhook).where(Webhook.workflow_id == workflow_id)
        result = session.exec(statement)
        webhooks = result.all()
    webhook_responses = [
        WebhookResponse(
            id=webhook.id,
            path=webhook.path,
            action_id=webhook.action_id,
            workflow_id=webhook.workflow_id,
        )
        for webhook in webhooks
    ]
    return webhook_responses


@app.get("/webhooks/search")
def search_webhooks(action_id: str | None = None) -> WebhookResponse:
    with Session(create_db_engine()) as session:
        statement = select(Webhook)

        if action_id is not None:
            statement = statement.where(Webhook.action_id == action_id)
        result = session.exec(statement)
        webhook = result.one()
    webhook_response = WebhookResponse(
        id=webhook.id,
        secret=webhook.secret,
        action_id=webhook.action_id,
        workflow_id=webhook.workflow_id,
    )
    return webhook_response


class AuthenticateWebhookResponse(BaseModel):
    status: Literal["Authorized", "Unauthorized"]
    action_key: str | None = None
    action_id: str | None = None
    webhook_id: str | None = None
    workflow_id: str | None = None


@app.post("/authenticate/webhooks/{webhook_id}/{secret}")
def authenticate_webhook(webhook_id: str, secret: str) -> AuthenticateWebhookResponse:
    with Session(create_db_engine()) as session:
        statement = select(Webhook).where(Webhook.id == webhook_id)
        result = session.exec(statement)
        webhook = result.one()
        if webhook.secret != secret:
            return AuthenticateWebhookResponse(status="Unauthorized")
        # Get slug
        statement = select(Action).where(Action.id == webhook.action_id)
        result = session.exec(statement)
        action = result.one()

    return AuthenticateWebhookResponse(
        status="Authorized",
        action_id=action.id,
        action_key=action.action_key,
        workflow_id=webhook.workflow_id,
        webhook_id=webhook_id,
    )


### Events Management


class EventParams(BaseModel):
    id: str  # The action run "key"
    workflow_id: str
    workflow_run_id: str
    action_id: str
    action_type: str
    published_at: datetime
    event: dict[str, Any]


@app.post("/events")
def index_event(event: EventParams):
    with create_events_index().writer() as writer:
        writer.add_document(event.model_dump())
        writer.commit()


SUPPORTED_EVENT_AGGS = {
    "count": pl.count,
    "max": pl.max,
    "avg": pl.mean,
    "median": pl.median,
    "min": pl.min,
    "std": pl.std,
    "sum": pl.sum,
    "var": pl.var,
}


class EventSearchParams(BaseModel):
    workflow_id: str
    workflow_run_id: str | None = None
    query: str | None = None
    group_by: list[str] | None = None
    agg: str | None = None


class EventSearchResponse(BaseModel):
    id: str
    workflow_id: str
    workflow_run_id: str
    action_id: str
    action_type: str
    published_at: datetime
    event: dict[str, Any]


@app.get("/events/search")
def search_events(params: EventSearchParams) -> list[EventSearchResponse]:
    # Filter by workflow_id
    # Filter by workflow_run_id (if non-null)
    # Run query
    # Return results
    pass


### Case Management


class CaseResponse(BaseModel):
    id: str
    title: str


@app.get("/cases")
def list_cases() -> list[CaseResponse]:
    pass


@app.post("/cases")
def create_case() -> CaseResponse:
    pass


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> CaseResponse:
    pass


@app.post("/cases/{case_id}")
def update_case(case_id: str) -> CaseResponse:
    pass
