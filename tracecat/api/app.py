import json
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

import polars as pl
import psycopg
import tantivy
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from tracecat.db import (
    Action,
    Webhook,
    Workflow,
    WorkflowRun,
    create_db_engine,
    create_events_index,
    create_vdb_conn,
    initialize_db,
)
from tracecat.logger import standard_logger

# TODO: Clean up API params / response "zoo"
# lots of repetition and inconsistency
from tracecat.types.api import (
    ActionMetadataResponse,
    ActionResponse,
    AuthenticateWebhookResponse,
    CreateActionParams,
    CreateWebhookParams,
    CreateWorkflowParams,
    Event,
    EventSearchParams,
    UpdateActionParams,
    UpdateWorkflowParams,
    UpdateWorkflowRunParams,
    WebhookMetadataResponse,
    WebhookResponse,
    WorkflowMetadataResponse,
    WorkflowResponse,
    WorkflowRunMetadataResponse,
    WorkflowRunResponse,
)
from tracecat.types.cases import Case


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
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

logger = standard_logger("api")


async def get_auth_user(user_id: str) -> tuple[str, ...] | None:
    conn_manager = await psycopg.AsyncConnection.connect(
        os.environ["SUPABASE_PSQL_URL"]
    )
    async with conn_manager as aconn:
        async with aconn.cursor() as acur:
            await acur.execute(
                "SELECT id, aud, role FROM auth.users WHERE (id=%s AND aud=%s AND role=%s)",
                (user_id, "authenticated", "authenticated"),
            )

            record = await acur.fetchone()
    logger.critical(f"{record = }")
    return record


async def authenticate_session(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    logger.info(f"Token: {token}")
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            key=os.environ["SUPABASE_JWT_SECRET"],
            algorithms=os.environ["SUPABASE_JWT_ALGORITHM"],
            # NOTE: Workaround, not sure if there are alternatives
            options={"verify_aud": False},
        )
        logger.critical(f"{payload = }")
        user_id: str = payload.get("sub")
        logger.critical(f"{user_id = }")
        if user_id is None:
            raise credentials_exception
    except JWTError as e:
        logger.error(f"{e = }")
        raise credentials_exception from e

    # Validate this against supabase

    if await get_auth_user(user_id) is None:
        raise credentials_exception
    return user_id


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the API."}


### Workflows


@app.get("/workflows")
def list_workflows(
    user_id: Annotated[str, Depends(authenticate_session)],
) -> list[WorkflowMetadataResponse]:
    """List all Workflows in database."""
    logger.critical(f"{user_id = }")
    with Session(create_db_engine()) as session:
        statement = select(Workflow).where(Workflow.owner_id == user_id)
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


@app.post("/workflows", status_code=201)
def create_workflow(
    params: CreateWorkflowParams,
    user_id: Annotated[str, Depends(authenticate_session)],
) -> WorkflowMetadataResponse:
    """Create new Workflow with title and description."""

    workflow = Workflow(
        title=params.title,
        description=params.description,
        owner_id=user_id,
    )
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
    )


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


@app.get("/events/search")
def search_events(params: EventSearchParams) -> list[Event]:
    """Search for events based on query parameters.

    Note: currently on supports filter by `workflow_id` and sort by `published_at`.
    """
    index = create_events_index()
    index.reload()
    query = index.parse_query(params.workflow_id, ["workflow_id"])
    searcher = index.searcher()
    searcher.search(
        query, order_by_field=tantivy.field(params.order_by), limit=params.limit
    )


### Case Management


@app.get("workflows/{workflow_id}/cases")
def list_cases(workflow_id: str, limit: int = 100) -> list[Case]:
    """List all cases under a workflow.

    Note: currently only supports listing the first 100 cases.
    """
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    result = pl.DataFrame(
        tbl.search()
        .where(f"workflow_id == {workflow_id}")
        .select(list(Case.model_fields().keys()))
        .limit(limit)
        .to_arrow()
    ).to_dicts()
    return result


@app.get("workflows/{workflow_id}/cases/{case_id}")
def get_case(workflow_id: str, case_id: str) -> Case:
    """Get a specific case by ID under a workflow."""
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    result = pl.DataFrame(
        tbl.search()
        .where(f"(workflow_id == {workflow_id}) AND (id == {case_id})")
        .select(list(Case.model_fields().keys()))
        .limit(1)
        .to_arrow()
    ).to_dicts()
    return result


@app.post("workflows/{workflow_id}/cases/{case_id}")
def update_case(workflow_id: str, case_id: str, case: Case):
    """Update a specific case by ID under a workflow."""
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    tbl.update(
        where=f"(workflow_id == {workflow_id}) AND (id == {case_id})",
        values=case.flatten(),
    )
