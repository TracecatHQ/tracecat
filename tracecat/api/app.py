import json
from contextlib import asynccontextmanager
from typing import Annotated, Any

import polars as pl
import tantivy
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import Engine
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from tracecat.api.completions import CategoryConstraint, stream_case_completions
from tracecat.auth import (
    Role,
    authenticate_user_or_service,
    authenticate_user_session,
)
from tracecat.db import (
    Action,
    CaseAction,
    CaseContext,
    Secret,
    User,
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
    CreateSecretParams,
    CreateWebhookParams,
    CreateWorkflowParams,
    Event,
    EventSearchParams,
    SearchSecretsParams,
    UpdateActionParams,
    UpdateSecretParams,
    UpdateUserParams,
    UpdateWorkflowParams,
    UpdateWorkflowRunParams,
    WebhookMetadataResponse,
    WebhookResponse,
    WorkflowMetadataResponse,
    WorkflowResponse,
    WorkflowRunMetadataResponse,
    WorkflowRunResponse,
)
from tracecat.types.cases import Case, CaseMetrics

logger = standard_logger("api")


engine: Engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = initialize_db()
    yield


app = FastAPI(lifespan=lifespan)

# TODO: Check TRACECAT__APP_ENV to set origins
origins = [
    "http://localhost:3000",
    "http://localhost:8000",
]

# TODO: Check TRACECAT__APP_ENV to set methods and headers
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


@app.get("/health")
def check_health() -> dict[str, str]:
    return {"message": "Hello world. I am the API. This is the health endpoint."}


### Workflows


@app.get("/workflows")
def list_workflows(
    role: Annotated[Role, Depends(authenticate_user_session)],
) -> list[WorkflowMetadataResponse]:
    """List all Workflows in database."""
    with Session(create_db_engine()) as session:
        statement = select(Workflow).where(Workflow.owner_id == role.id)
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


@app.post("/workflows", status_code=status.HTTP_201_CREATED)
def create_workflow(
    params: CreateWorkflowParams,
    role: Annotated[Role, Depends(authenticate_user_session)],
) -> WorkflowMetadataResponse:
    """Create new Workflow with title and description."""
    workflow = Workflow(
        title=params.title,
        description=params.description,
        owner_id=role.id,
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
            key=action.key,
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
        owner_id=workflow.owner_id,
    )
    return workflow_response


@app.post("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_workflow(
    workflow_id: str,
    params: UpdateWorkflowParams,
) -> None:
    """Update Workflow."""

    with Session(create_db_engine()) as session:
        statement = select(Workflow).where(Workflow.id == workflow_id)
        result = session.exec(statement)
        try:
            workflow = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

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


@app.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow(workflow_id: str) -> None:
    """Delete Workflow."""

    with Session(create_db_engine()) as session:
        statement = select(Workflow).where(Workflow.id == workflow_id)
        result = session.exec(statement)
        try:
            workflow = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
        session.delete(workflow)
        session.commit()


### Workflow Runs


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
        try:
            workflow_run = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

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
        try:
            workflow_run = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

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
            key=action.key,
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
        key=action.key,
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
        try:
            action = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

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
        key=action.key,
    )


@app.post("/actions/{action_id}")
def update_action(action_id: str, params: UpdateActionParams) -> ActionResponse:
    with Session(create_db_engine()) as session:
        # Fetch the action by id
        statement = select(Action).where(Action.id == action_id)
        result = session.exec(statement)
        try:
            action = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

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
        key=action.key,
    )


@app.delete("/actions/{action_id}", status_code=204)
def delete_action(action_id: str) -> None:
    with Session(create_db_engine()) as session:
        statement = select(Action).where(Action.id == action_id)
        result = session.exec(statement)
        try:
            action = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
        session.delete(action)
        session.commit()


### Webhooks


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


@app.post("/webhooks", status_code=status.HTTP_201_CREATED)
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
        try:
            webhook = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
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


@app.get("/webhooks/search")
def search_webhooks(action_id: str | None = None) -> WebhookResponse:
    with Session(create_db_engine()) as session:
        statement = select(Webhook)

        if action_id is not None:
            statement = statement.where(Webhook.action_id == action_id)
        result = session.exec(statement)
        try:
            webhook = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
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
        try:
            webhook = result.one()
        except NoResultFound as e:
            logger.error("Webhook does not exist: %s", e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
        if webhook.secret != secret:
            return AuthenticateWebhookResponse(status="Unauthorized")
        # Get slug
        statement = select(Action).where(Action.id == webhook.action_id)
        result = session.exec(statement)
        try:
            action = result.one()
        except Exception as e:
            logger.error("Action does not exist: %s", e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
    return AuthenticateWebhookResponse(
        status="Authorized",
        action_id=action.id,
        action_key=action.key,
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


@app.post("/workflows/{workflow_id}/cases", status_code=status.HTTP_201_CREATED)
def create_case(workflow_id: str, cases: list[Case]):
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    # Should probably also add a check for existing case IDs
    # NOTE: Duplicate workflow_id - ignore for now, use the one given in the case
    tbl.add([case.flatten() for case in cases])


@app.get("/workflows/{workflow_id}/cases")
def list_cases(workflow_id: str, limit: int = 100) -> list[Case]:
    """List all cases under a workflow.

    Note: currently only supports listing the first 100 cases.
    """
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    result = (
        tbl.search()
        .where(f"workflow_id = {workflow_id!r}")
        .select(list(Case.model_fields.keys()))
        .limit(limit)
        .to_polars()
        .to_dicts()
    )
    return [Case.from_flattened(c) for c in result]


@app.get("/workflows/{workflow_id}/cases/{case_id}")
def get_case(workflow_id: str, case_id: str) -> Case:
    """Get a specific case by ID under a workflow."""
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    result = (
        tbl.search()
        .where(f"(workflow_id = {workflow_id!r}) AND (id = {case_id!r})")
        .select(list(Case.model_fields.keys()))
        .limit(1)
        .to_polars()
        .to_dicts()
    )
    return [Case.from_flattened(c) for c in result]


@app.post("/workflows/{workflow_id}/cases/{case_id}")
def update_case(workflow_id: str, case_id: str, case: Case):
    """Update a specific case by ID under a workflow."""
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    tbl.update(
        where=f"(workflow_id = {workflow_id!r}) AND (id = {case_id!r})",
        values=case.flatten(),
    )


@app.get("/workflows/{workflow_id}/cases/{case_id}/metrics")
def get_case_metrics(workflow_id: str, case_id: str) -> CaseMetrics:
    """Get a specific case by ID under a workflow."""
    db = create_vdb_conn()
    tbl = db.open_table("cases")
    df = pl.DataFrame(
        tbl.search()
        .where(f"(workflow_id = {workflow_id!r}) AND (id = {case_id!r})")
        .select(list(Case.model_fields.keys()))
        .to_arrow()
    ).to_dicts()
    return df


### Available Case Actions


@app.get("/case-actions")
def list_case_actions() -> list[CaseAction]:
    with Session(create_db_engine()) as session:
        statement = select(CaseAction)
        actions = session.exec(statement).all()
    return actions


@app.post("/case-actions")
def add_case_action(case_action: CaseAction) -> CaseAction:
    with Session(create_db_engine()) as session:
        session.add(case_action)
        session.commit()
        session.refresh(case_action)
    return case_action


@app.delete("/case-actions")
def delete_case_action(case_action: CaseAction):
    with Session(create_db_engine()) as session:
        statement = select(CaseAction).where(CaseAction.id == case_action.id)
        result = session.exec(statement)
        try:
            action = result.one()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
        session.delete(action)
        session.commit()


### Available Context Labels


@app.get("/case-contexts")
def list_case_contexts() -> list[CaseContext]:
    with Session(create_db_engine()) as session:
        statement = select(CaseContext)
        actions = session.exec(statement).all()
    return actions


@app.post("/case-contexts")
def add_case_context(case_context: CaseContext) -> CaseContext:
    with Session(create_db_engine()) as session:
        session.add(case_context)
        session.commit()
        session.refresh(case_context)
    return case_context


@app.delete("/case-contexts")
def delete_case_context(case_context: CaseContext):
    with Session(create_db_engine()) as session:
        statement = select(CaseContext).where(CaseContext.id == case_context.id)
        result = session.exec(statement)
        try:
            action = result.one()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
        session.delete(action)
        session.delete(action)
        session.commit()
    pass


@app.post("/completions/cases/stream")
async def streaming_autofill_case_fields(
    cases: list[Case],  # TODO: Replace this with case IDs
) -> dict[str, str]:
    """List of case IDs.
    Steps
    -----
    1. Using Case IDs, fetch case data
    2. Figure out  which fields need to be populated - these fields are None
    3. Complete the fields

    """
    logger.info(f"Received cases: {cases = }")
    actions_mapping = (
        pl.DataFrame(list_case_actions())
        .lazy()
        .select(
            pl.col.tag,
            pl.col.value.str.split(".").list.first(),
        )
        .unique()
        .group_by(pl.col.tag)
        .agg(pl.col.value)
        .collect(streaming=True)
        .to_dicts()
    )

    contexts_mapping = (
        pl.DataFrame(list_case_contexts())
        .lazy()
        .select(
            pl.col.tag,
            pl.col.value.str.split(".").list.first(),
        )
        .unique()
        .group_by(pl.col.tag)
        .agg(pl.col.value)
        .collect(streaming=True)
        .to_dicts()
    )
    context_cons = [
        CategoryConstraint.model_validate(d, strict=True) for d in contexts_mapping
    ]
    action_cons = [
        CategoryConstraint.model_validate(d, strict=True) for d in actions_mapping
    ]

    return StreamingResponse(
        stream_case_completions(
            cases, context_cons=context_cons, action_cons=action_cons
        ),
        media_type="text/event-stream",
    )


### Users


@app.put("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    role: Annotated[Role, Depends(authenticate_user_session)],
) -> User:
    """Create new user.

    Note that this is just for user config, auth is done separately."""

    # Check if user exists

    with Session(create_db_engine()) as session:
        # Check if user exists
        statement = select(User).where(User.id == role.id).limit(1)
        result = session.exec(statement)
        user = result.one_or_none()
        if user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )
        user = User(id=role.id)

        session.add(user)
        session.commit()
        session.refresh(user)
        return user


@app.get("/users")
def get_user(
    role: Annotated[Role, Depends(authenticate_user_session)],
) -> User:
    """Return user as title, description, list of Action JSONs, adjacency list of Action IDs."""

    with Session(create_db_engine()) as session:
        # Get user given user_id
        statement = select(User).where(User.id == role.id)
        result = session.exec(statement)
        try:
            user = result.one()
            return user
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            ) from e


@app.post("/users", status_code=status.HTTP_204_NO_CONTENT)
def update_user(
    role: Annotated[Role, Depends(authenticate_user_session)],
    params: UpdateUserParams,
) -> None:
    """Update user."""

    with Session(create_db_engine()) as session:
        statement = select(User).where(User.id == role.id)
        result = session.exec(statement)
        try:
            user = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            ) from e

        if params.tier is not None:
            user.tier = params.tier
        if params.settings is not None:
            user.settings = params.settings

        session.add(user)
        session.commit()


@app.delete("/users", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    role: Annotated[Role, Depends(authenticate_user_session)],
) -> None:
    """Delete user."""

    with Session(create_db_engine()) as session:
        statement = select(User).where(User.id == role.id)
        result = session.exec(statement)
        try:
            user = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            ) from e
        session.delete(user)
        session.commit()


### Secrets


@app.get("/secrets")
def list_secrets(
    role: Annotated[Role, Depends(authenticate_user_session)],
) -> list[Secret]:
    """Get a secret by ID."""
    with Session(engine) as session:
        statement = select(Secret).where(Secret.user_id == role.id)
        result = session.exec(statement)
        secrets = result.all()
        return secrets


@app.get("/secrets/{secret_name}")
def get_secret(
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    secret_name: str,
    user_id: str | None = None,
) -> Secret:
    """Get a secret by ID.

    Support access for both user and service roles."""

    query_id: str
    logger.info(f"Role: {role}")
    match role:
        case Role(variant="user"):
            query_id = role.id
        case Role(variant="service", id="tracecat-runner"):
            if user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Missing 'user_id' query parameter",
                )
            query_id = user_id
        case _:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Role {role!r} unauthorized",
            )
    with Session(engine) as session:
        # Check if secret exists
        statement = (
            select(Secret)
            .where(Secret.user_id == query_id, Secret.name == secret_name)
            .limit(1)
        )
        result = session.exec(statement)
        secret = result.one_or_none()
        if secret is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
            )
        return secret


@app.put("/secrets", status_code=status.HTTP_201_CREATED)
def create_secret(
    role: Annotated[Role, Depends(authenticate_user_session)],
    params: CreateSecretParams,
) -> Secret:
    """Get a secret by ID."""
    with Session(engine) as session:
        # Check if secret exists
        statement = (
            select(Secret)
            .where(Secret.user_id == role.id, Secret.name == params.name)
            .limit(1)
        )
        result = session.exec(statement)
        secret = result.one_or_none()
        if secret is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
            )
        new_secret = Secret(name=params.name, user_id=role.id)
        new_secret.key = params.value  # Set and encrypt the key

        session.add(new_secret)
        session.commit()
        session.refresh(new_secret)


@app.post("/secrets", status_code=status.HTTP_201_CREATED)
def update_secret(
    role: Annotated[Role, Depends(authenticate_user_session)],
    params: UpdateSecretParams,
) -> Secret:
    """Get a secret by ID."""
    with Session(engine) as session:
        # Check if secret exists
        statement = (
            select(Secret)
            .where(Secret.user_id == role.id, Secret.name == params.name)
            .limit(1)
        )
        result = session.exec(statement)
        secret = result.one_or_none()
        if secret is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
            )
        secret.key = params.value  # Set and encrypt the key
        session.add(secret)
        session.commit()
        session.refresh(secret)


@app.post("/secrets/search")
def search_secrets(
    role: Annotated[Role, Depends(authenticate_user_session)],
    params: SearchSecretsParams,
) -> list[Secret]:
    """Get a secret by ID."""
    with Session(engine) as session:
        statement = (
            select(Secret)
            .where(Secret.user_id == role.id)
            .filter(*[Secret.name == name for name in params.names])
        )
        result = session.exec(statement)
        secrets = result.all()
        return secrets
