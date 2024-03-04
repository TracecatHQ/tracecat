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
import hashlib
import os
from enum import StrEnum, auto
from typing import Annotated, Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field

from tracecat.actions import (
    ActionRun,
    ActionRunStatus,
    ActionTrail,
    action_key_to_workflow_id,
    start_action_run,
)
from tracecat.graph import find_entrypoint
from tracecat.logger import standard_logger
from tracecat.workflows import Workflow

logger = standard_logger(__name__)

load_dotenv()


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

# Static data
workflow_registry: dict[str, Workflow] = {}
entrypoint_secret_to_action_key: dict[str, str] = {}

# Dynamic data
action_result_store: dict[str, ActionTrail] = {}
action_run_status_store: dict[str, ActionRunStatus] = {}
ready_jobs_queue: asyncio.Queue[ActionRun] = asyncio.Queue()
running_jobs_store: dict[str, asyncio.Task[None]] = {}


# Dependencies
def valid_workflow(workflow_id: str) -> str:
    """Check if a workflow exists."""
    if workflow_id not in workflow_registry:
        logger.error(f"Workflow {workflow_id} not found.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id} not found.",
        )
    return workflow_id


def valid_webhook_secret(secret: str) -> str:
    if secret not in entrypoint_secret_to_action_key:
        logger.error(f"{secret} is not a valid entrypoint.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{secret} is not a valid entrypoint.",
        )
    return entrypoint_secret_to_action_key[secret]


# Endpoints
@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello. I am a runner."}


class AddWorkflowResponse(BaseModel):
    status: str = Field(..., description="Status of the runner.")
    message: str = Field(..., description="Message from the runner.")
    id: str = Field(..., description="ID of the workflow.")


@app.post("/workflow", response_model=AddWorkflowResponse)
def add_workflow(workflow: Annotated[Workflow, Body]) -> AddWorkflowResponse:
    """Load the runner with a workflow.

    This is a temporary solution to store the workflow in memory.

    Better alternatives include:
    - Using a database to store the graph.
    - Using a message broker/task queue to send the graph to the runner.
    """
    global workflow_registry, entrypoint_secret_to_action_key
    logger.debug(f"Received workflow: {workflow.id!r}")

    workflow_registry[workflow.id] = workflow
    entrypoint_key = find_entrypoint(workflow.adj_list)
    entrypoint_secret = hashlib.sha256(
        f"{entrypoint_key}{os.environ["RUNNER_SALT"]}".encode()
    ).hexdigest()

    logger.debug(f"Entrypoint: {entrypoint_key}")
    logger.debug(f"Entrypoint secret: {entrypoint_secret}")

    entrypoint_secret_to_action_key[entrypoint_secret] = entrypoint_key

    logger.debug(f"Workflow registry: {workflow_registry.keys()}")
    logger.debug(f"Entrypoint mapping: {entrypoint_secret_to_action_key}")

    return {
        "status": "ok",
        "message": "Successfully added workflow to runner.",
        "id": workflow.id,
        "entrypoint_secret": entrypoint_secret,
    }  # type: ignore


class StartWorkflowResponse(BaseModel):
    status: str = Field(..., description="Status of the runner.")
    message: str = Field(..., description="Message from the runner.")
    id: str = Field(..., description="ID of the workflow.")


async def valid_payload(request: Request) -> dict[str, Any]:
    """Validate the payload of a request."""
    payload: dict[str, Any]
    match request.headers.get("content-type"):
        case "application/json":
            payload = await request.json()
        case "application/x-www-form-urlencoded":
            payload = await request.form()
        case _:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid content type.",
            )
    return payload


@app.post("/webhook/{secret}", response_model=StartWorkflowResponse)
async def webhook(
    entrypoint_key: Annotated[str, Depends(valid_webhook_secret)],
    payload: Annotated[dict[str, Any], Depends(valid_payload)],
    background_tasks: BackgroundTasks,
) -> StartWorkflowResponse:
    """A webhook to handle tracecat events.

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
    logger.info(f"Received webhook with entrypoint {entrypoint_key}")

    logger.info(f"{payload =}")

    workflow_id = action_key_to_workflow_id(entrypoint_key)

    # This data refers to the webhook specific data
    response = await start_workflow(
        entrypoint_key=entrypoint_key,
        workflow_id=workflow_id,
        entrypoint_payload=payload,
        background_tasks=background_tasks,
    )
    return response


@app.post(
    "/workflow/{workflow_id}/run/{entrypoint_key}", response_model=StartWorkflowResponse
)
async def start_workflow(
    entrypoint_key: str,
    workflow_id: Annotated[str, Depends(valid_workflow)],
    entrypoint_payload: Annotated[dict[str, Any], Body],
    background_tasks: BackgroundTasks,
) -> StartWorkflowResponse:
    """Start a workflow.

    Optional entrypoint

    We need this endpoint to:
    1. Trigger a workflow from a webhook
    2. Trigger a workflow from a scheduled event
    """
    run_id = uuid4().hex
    background_tasks.add_task(
        run_workflow,
        workflow_id=workflow_id,
        run_id=run_id,
        entrypoint_key=entrypoint_key,
        entrypoint_payload=entrypoint_payload,
    )
    return {"status": "ok", "message": "Workflow started.", "id": workflow_id}  # type: ignore


async def run_workflow(
    workflow_id: str,
    run_id: str,
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
    run_logger = standard_logger(run_id)
    # This is the adjacency list of the graph
    workflow = workflow_registry[workflow_id]

    # Execution state

    # Initial state
    ready_jobs_queue.put_nowait(
        ActionRun(
            run_id=run_id,
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
                f"{workflow.action_map[action_run.action_key].__class__.__name__} {action_run.id!r} ready. Running."
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


@app.post("/mock/search")
def mock_search(data: Annotated[dict[str, Any], Body]) -> dict[str, Any]:
    """Mock search endpoint."""
    logger.info(f"Received data: {data}")
    return {"query": data, "response": "Mock response"}


class SlackPayload(BaseModel):
    workspace: str
    channel: str
    message: str


@app.post("/mock/slack")
def mock_slack(params: Annotated[SlackPayload, Body]) -> dict[str, Any]:
    """Mock search endpoint."""
    logger.info(
        f"Sending message to {params.workspace}/{params.channel}: {params.message}"
    )
    return params.model_dump()
