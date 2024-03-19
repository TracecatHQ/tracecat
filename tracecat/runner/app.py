"""Workflow runner.

Data flow
---------
1. User creates a workflow graph in the client.
2. On submission, the client sends the graph (nodes and edges with data) to a pool of runners
3. A runner receives the graph and stores it in memory.


Invariants
----------
- Runners can accept and execute multiple arbitrary workflows until completion.

Workflows and workflow runs
----------------------------
- A workflow is a graph of actions.
- A run is an instance of a workflow.

Actions and action runs
-----------------------
- An action is a node in the graph.
- A run is an instance of an action.

Stores
------
- We need to store the state of the workflow run.
- The current implementation uses in-memory kv stores to manage execution state.
- We can use distributed kv stores / databases to manage state across multiple runners to scale the backend.
- Note that ActionRuns need to be identified across workflow runs - we use a combination of the workflow id and the action id to do this.

"""

from __future__ import annotations

import asyncio
from enum import StrEnum, auto
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
)
from fastapi.datastructures import FormData
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field

from tracecat.config import TRACECAT__API_URL
from tracecat.contexts import ctx_workflow
from tracecat.logger import standard_logger
from tracecat.runner.actions import (
    ActionRun,
    ActionRunStatus,
    ActionTrail,
    start_action_run,
)
from tracecat.runner.workflows import Workflow
from tracecat.types.api import AuthenticateWebhookResponse, WorkflowResponse

logger = standard_logger(__name__)


app = FastAPI(debug=True, default_response_class=ORJSONResponse)
origins = [
    "http://localhost",
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunnerStatus(StrEnum):
    STARTING = auto()
    RUNNING = auto()
    SHUTTING_DOWN = auto()


runner_status: RunnerStatus = RunnerStatus.RUNNING


# Dynamic data
action_result_store: dict[str, ActionTrail] = {}
action_run_status_store: dict[str, ActionRunStatus] = {}
ready_jobs_queue: asyncio.Queue[ActionRun] = asyncio.Queue()
running_jobs_store: dict[str, asyncio.Task[None]] = {}


async def get_workflow(workflow_id: str) -> WorkflowResponse:
    async with httpx.AsyncClient(http2=True) as client:
        response = await client.get(f"{TRACECAT__API_URL}/workflows/{workflow_id}")
        response.raise_for_status()
    data = response.json()
    return WorkflowResponse.model_validate(data)


# Dependencies
async def valid_workflow(workflow_id: str) -> str:
    """Check if a workflow exists."""
    async with httpx.AsyncClient(http2=True) as client:
        response = await client.get(f"{TRACECAT__API_URL}/workflows/{workflow_id}")
        if response.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow {workflow_id} not found.",
            )
    return workflow_id


# Endpoints
@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello. I am the runner."}


@app.get("/health")
def check_health() -> dict[str, str]:
    return {"message": "Hello world. I am the runner. This is the health endpoint."}


class StartWorkflowResponse(BaseModel):
    status: str = Field(..., description="Status of the runner.")
    message: str = Field(..., description="Message from the runner.")
    id: str = Field(..., description="ID of the workflow.")


async def valid_payload(request: Request) -> dict[str, Any] | FormData:
    """Validate the payload of a request."""
    payload: dict[str, Any] | FormData
    match request.headers.get("content-type"):
        case "application/json":
            payload = await request.json()
        case "application/x-www-form-urlencoded":
            payload = await request.form()
        case _:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Validation error: Invalid payload content type.",
            )
    return payload


async def valid_webhook_request(path: str, secret: str) -> AuthenticateWebhookResponse:
    """Validate a webhook request.

    Steps
    -----
    1. Lookup the secret in the database
    2. If the secret is not found, return a 404.
    """
    # Change this to make a db call
    async with httpx.AsyncClient(http2=True) as client:
        response = await client.post(
            f"{TRACECAT__API_URL}/authenticate/webhooks/{path}/{secret}"
        )
        response.raise_for_status()

    auth_response = AuthenticateWebhookResponse.model_validate(response.json())
    if auth_response.status == "Unauthorized":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid secret.",
        )
    return auth_response


@app.post("/webhook/{path}/{secret}")
async def webhook(
    webhook_metadata: Annotated[
        AuthenticateWebhookResponse, Depends(valid_webhook_request)
    ],
    payload: Annotated[dict[str, Any], Depends(valid_payload)],
    background_tasks: BackgroundTasks,
) -> StartWorkflowResponse:
    """A webhook to handle tracecat events.

    Responsibilities
    ---------------
    - This endpoint can (and ideally will) run in a separate process.
    - Ideally, we should use a message broker to handle the events.

    Authentication
    --------------
    - Needs to check that the incoming request is from the intended user.
    - To do this we can store the webhook secret in the database.
    - This server should also perform authentication / validation of the incoming requests.


    Notes
    -----
    - This endpoint will be called by tracecat to notify the runner of new events.
    - The webhook uuid points to an entrypoint in a Tracecat workflow.

    Logic
    -----
    - When this endpoint receives a request, it will:
        - Spawn a new process to handle the event.
        - Store the process in a queue.
    """
    logger.info(f"Received webhook with entrypoint {webhook_metadata.action_key}")
    logger.debug(f"{payload =}")

    workflow_id = webhook_metadata.workflow_id
    workflow_response = await get_workflow(workflow_id)
    if workflow_response.status == "offline":
        return StartWorkflowResponse(
            status="error", message="Workflow offline", id=workflow_id
        )

    # This data refers to the webhook specific data
    response = await start_workflow(
        workflow_id=workflow_id,
        start_workflow_params=StartWorkflowParams(
            entrypoint_key=webhook_metadata.action_key, entrypoint_payload=payload
        ),
        background_tasks=background_tasks,
    )
    return response


class StartWorkflowParams(BaseModel):
    entrypoint_key: str
    entrypoint_payload: dict[str, Any]


@app.post("/workflows/{workflow_id}")
async def start_workflow(
    workflow_id: Annotated[str, Depends(valid_workflow)],
    start_workflow_params: StartWorkflowParams,
    background_tasks: BackgroundTasks,
) -> StartWorkflowResponse:
    """Start a workflow.

    Use-cases:
    1. Trigger a workflow from a webhook
    2. Trigger a workflow from a scheduled event

    Parameters
    ----------
    entrypoint_id : str
        The action ID to start the workflow from.

    workflow_id : str
        The ID of the workflow to start.

    entrypoint_payload : dict
        The action inputs to pass into the entrypoint action.
    """
    workflow_run_id = uuid4().hex
    background_tasks.add_task(
        run_workflow,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        entrypoint_key=start_workflow_params.entrypoint_key,
        entrypoint_payload=start_workflow_params.entrypoint_payload,
    )
    return StartWorkflowResponse(
        status="ok", message="Workflow started.", id=workflow_id
    )


async def run_workflow(
    workflow_id: str,
    workflow_run_id: str,
    entrypoint_key: str,
    entrypoint_payload: dict[str, Any] | None = None,
) -> None:
    """Run a workflow.

    Design
    ------
    - There are 2 ways to design this:
        1. Use a 'lazy' BFS traversal to traverse the graph, with as much concurrency as possible.
        2. Use something like `graphlib` to schedule all tasks eagerly.
    - We will use the lazy approach for various reasons:
        1. Compute efficiency: We only compute the next action when we need it.
        2. We can prune the graph as we go along, instead of having to cancel scheduled tasks along a certain path.
        3. We can infinitely suspend the workflow run and resume it later.

    Logic
    -----
    - If no entry point is passed, the workflow will start from the default entrypoint.
    - Beginning from the entrypoint, traverse the graph and execute each action.
    - Execute the action based on the action type.
    - Store the results of each action in the KV store.
    - On successful completion, enqueue the next actions.
        - We must pass the results of the previous action to the next action
        - This will allow us to trace the lineage of the data.
        - NOTE(perf): We can parallelize the execution of the next actions (IO bound).

    Thoughts on current design
    --------------------------
    - Maybe break this out into a pure workflow worker. I say this because we then won't have to
     associate the worker with a specific workflow.
    - The `start_workflow` function can then just directly enqueue the first action.
    """
    run_logger = standard_logger(workflow_run_id)
    workflow_response = await get_workflow(workflow_id)
    workflow = Workflow.from_response(workflow_response)
    ctx_workflow.set(workflow)

    # Initial state
    ready_jobs_queue.put_nowait(
        ActionRun(
            workflow_run_id=workflow_run_id,
            run_kwargs=entrypoint_payload,
            action_key=entrypoint_key,
        )
    )

    try:
        while (
            not ready_jobs_queue.empty() or running_jobs_store
        ) and runner_status == RunnerStatus.RUNNING:
            try:
                action_run = await asyncio.wait_for(ready_jobs_queue.get(), timeout=3)
            except TimeoutError:
                continue
            # Defensive: Deduplicate tasks
            if (
                action_run.id in running_jobs_store
                or action_run.id in action_result_store
            ):
                run_logger.debug(
                    f"Action {action_run.id!r} already running or completed. Skipping."
                )
                continue

            run_logger.info(
                f"{workflow.actions[action_run.action_key].__class__.__name__} {action_run.id!r} ready. Running."
            )
            action_run_status_store[action_run.id] = ActionRunStatus.PENDING
            # Schedule a new action run
            running_jobs_store[action_run.id] = asyncio.create_task(
                start_action_run(
                    action_run=action_run,
                    workflow_ref=workflow,
                    ready_jobs_queue=ready_jobs_queue,
                    running_jobs_store=running_jobs_store,
                    action_result_store=action_result_store,
                    action_run_status_store=action_run_status_store,
                    custom_logger=run_logger,
                )
            )

        run_logger.info("Workflow completed.")
    except asyncio.CancelledError:
        run_logger.warning("Workflow was cancelled.")
    finally:
        run_logger.info("Shutting down running tasks")
        for running_task in running_jobs_store.values():
            running_task.cancel()
