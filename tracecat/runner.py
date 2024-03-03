"""Workflow runner.

Data flow
---------
1. User creates a workflow graph in the client.
2. On submission, the client sends the graph (nodes and edges with data) to a pool of runners
3. A runner receives the graph and stores it in memory.


Invariants
----------
- Runners can accept and execute multiple arbitrary workflows until completion.


"""

from __future__ import annotations

import asyncio
from enum import StrEnum, auto
from typing import Annotated, Any
from uuid import uuid4

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, status
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field

from tracecat.actions import ActionRun, ActionRunStatus, ActionTrail, start_action_run
from tracecat.graph import find_entrypoint
from tracecat.logger import standard_logger
from tracecat.workflows import Workflow

app = FastAPI(debug=True, default_response_class=ORJSONResponse)


logger = standard_logger(__name__, "DEBUG")


class RunnerStatus(StrEnum):
    STARTING = auto()
    RUNNING = auto()
    SHUTTING_DOWN = auto()


action_results: dict[str, Any] = {}
runner_status: RunnerStatus = RunnerStatus.RUNNING

# Static data
workflow_registry: dict[str, Workflow] = {}

# Node ID -> workflow ID mapping
entrypoint_to_workflow: dict[str, str] = {}


class AddWorkflowResponse(BaseModel):
    """Response from the runner."""

    status: str = Field(..., description="Status of the runner.")
    message: str = Field(..., description="Message from the runner.")
    id: str = Field(..., description="ID of the workflow.")


class StartWorkflowResponse(BaseModel):
    """Response from the runner."""

    status: str = Field(..., description="Status of the runner.")
    message: str = Field(..., description="Message from the runner.")
    id: str = Field(..., description="ID of the workflow.")


# Dependencies
def valid_workflow(workflow_id: str) -> str:
    """Check if a workflow exists."""
    if workflow_id not in workflow_registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id} not found.",
        )
    return workflow_id


def valid_entrypoint(entrypoint_id: str) -> str:
    """Check if a workflow exists."""
    if entrypoint_id not in entrypoint_to_workflow:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{entrypoint_id} is not a valid entrypoint.",
        )
    return entrypoint_id


# Endpoints
@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello. I am a runner."}


@app.post("/workflow", response_model=AddWorkflowResponse)
def add_workflow(workflow: Annotated[Workflow, Body]) -> AddWorkflowResponse:
    """Load the runner with a workflow.

    This is a temporary solution to store the workflow in memory.

    Better alternatives include:
    - Using a database to store the graph.
    - Using a message broker/task queue to send the graph to the runner.
    """
    global workflow_registry, entrypoint_to_workflow
    logger.debug(f"Received workflow graph: {workflow}")

    workflow_registry[workflow.id] = workflow
    entrypoint = find_entrypoint(workflow.adj_list)
    entrypoint_to_workflow[entrypoint] = workflow.id

    logger.debug(f"Workflow registry: {workflow_registry}")
    logger.debug(f"Entrypoint mapping: {entrypoint_to_workflow}")

    return {
        "status": "ok",
        "message": "Successfully added workflow to runner.",
        "id": workflow.id,
    }  # type: ignore


@app.post("/webhook/{entrypoint_id}", response_model=StartWorkflowResponse)
async def webhook(
    data: Annotated[dict[str, Any], Body(default_factory=dict)],
    background_tasks: BackgroundTasks,
    entrypoint_id: Annotated[str, Depends(valid_entrypoint)],
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
    logger.info(f"Received webhook for {entrypoint_id}")

    # Reverse lookup the workflow id
    workflow_id = entrypoint_to_workflow[entrypoint_id]

    # This data refers to the webhook specific data
    response = await start_workflow(
        id=workflow_id,
        data=data,
        background_tasks=background_tasks,
        entrypoint=entrypoint_id,
    )
    return response


@app.post("/workflow/{id}/run/{entrypoint}", response_model=StartWorkflowResponse)
async def start_workflow(
    data: Annotated[dict[str, Any], Body],
    background_tasks: BackgroundTasks,
    id: Annotated[str, Depends(valid_workflow)],
    entrypoint: str,
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
        workflow_id=id,
        run_id=run_id,
        entrypoint=entrypoint,
        data=data,
    )
    return {"status": "ok", "message": "Workflow started.", "id": id}  # type: ignore


async def run_workflow(
    workflow_id: str,
    run_id: str,
    entrypoint: str,
    data: dict[str, Any] | None = None,
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
    """
    run_logger = standard_logger(run_id)
    # This is the adjacency list of the graph
    workflow = workflow_registry[workflow_id]
    dependencies = workflow.action_dependencies

    # Execution state
    action_result_store: dict[str, ActionTrail] = {}
    task_status_store: dict[str, ActionRunStatus] = {}
    ready_jobs_queue: asyncio.Queue[ActionRun] = asyncio.Queue()
    running_jobs_store: dict[str, asyncio.Task[None]] = {}

    # Initial state
    ready_jobs_queue.put_nowait(ActionRun(run_id=run_id, action_id=entrypoint))

    try:
        while (
            not ready_jobs_queue.empty() or running_jobs_store
        ) and runner_status == RunnerStatus.RUNNING:
            try:
                task = await asyncio.wait_for(ready_jobs_queue.get(), timeout=3)
            except TimeoutError:
                continue
            # Defensive: Deduplicate tasks
            action_id = task.action_id
            if action_id in running_jobs_store or action_id in action_result_store:
                run_logger.debug(
                    f"Action {action_id!r} already running or completed. Skipping."
                )
                continue

            run_logger.info(
                f"{workflow.action_map[action_id].__class__.__name__} {action_id!r} ready. Running."
            )
            task_status_store[action_id] = ActionRunStatus.PENDING
            running_jobs_store[action_id] = asyncio.create_task(
                start_action_run(
                    run_id=run_id,
                    action=workflow.action_map[action_id],
                    adj_list=workflow.adj_list,
                    ready_jobs_queue=ready_jobs_queue,
                    running_jobs_store=running_jobs_store,
                    action_result_store=action_result_store,
                    action_run_status_store=task_status_store,
                    dependencies=dependencies[action_id],
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
