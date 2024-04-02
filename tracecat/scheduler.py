"""Polling schedule to run workflows as cron job."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated

import httpx
from croniter import croniter
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlmodel import Session, select
from tenacity import retry, stop_after_attempt, wait_exponential

from tracecat.auth import (
    Role,
    authenticate_user,
)
from tracecat.config import (
    TRACECAT__API_URL,
    TRACECAT__APP_ENV,
    TRACECAT__RUNNER_URL,
    TRACECAT__SCHEDULE_INTERVAL_SECONDS,
    TRACECAT__SCHEDULE_MAX_CONNECTIONS,
)
from tracecat.db import WorkflowSchedule
from tracecat.logger import standard_logger
from tracecat.types.schedules import WorkflowScheduleParams

logger = standard_logger("scheduler")


engine: Engine


async def should_run_schedule(cron: str, now: datetime):
    next_run = croniter(cron, now).get_next(datetime)
    return next_run < now + timedelta(seconds=TRACECAT__SCHEDULE_INTERVAL_SECONDS)


@retry(
    wait=wait_exponential(multiplier=1, min=4, max=10),
    stop=stop_after_attempt(3),
)
async def start_workflow(workflow_id: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        url = f"{TRACECAT__RUNNER_URL}/workflows/{workflow_id}"
        response = await client.post(url)
        response.raise_for_status()
        return response


async def run_scheduled_workflows():
    while True:
        now = datetime.now()
        with Session(engine) as session:
            schedules = session.exec(WorkflowSchedule).all()
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=60.0)
            ) as client:
                tasks = [
                    start_workflow(client, schedule.workflow_id)
                    for schedule in schedules
                    if await should_run_schedule(schedule.cron, now)
                ]
                # Since we're using the same client instance for all requests,
                # we can set the rate limit directly in the httpx client configuration
                client_limits = httpx.Limits(
                    max_connections=TRACECAT__SCHEDULE_MAX_CONNECTIONS
                )
                httpx.HTTPTransport(limits=client_limits)
                responses = await asyncio.gather(*tasks)
                for response in responses:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        logger.error(
                            "Failed to schedule workflow. Will retry next cycle.",
                            exc_info=e,
                        )
        await asyncio.sleep(
            TRACECAT__SCHEDULE_INTERVAL_SECONDS
        )  # Check every 60 seconds


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sleep to wait for API to start
    # and DB to initialize
    global engine
    asyncio.sleep(30)
    yield


app = FastAPI(lifespan=lifespan)


app = FastAPI()


if TRACECAT__APP_ENV == "prod":
    # NOTE: If you are using Tracecat self-hosted
    # please replace with your own domain
    cors_origins_kwargs = {
        "allow_origins": ["https://platform.tracecat.com", TRACECAT__API_URL]
    }
elif TRACECAT__APP_ENV == "staging":
    cors_origins_kwargs = {
        "allow_origins": [TRACECAT__RUNNER_URL],
        "allow_origin_regex": r"https://tracecat-.*-tracecat\.vercel\.app",
    }
else:
    cors_origins_kwargs = {
        "allow_origins": [
            "http://localhost:3000",
            "http://localhost:8000",
        ],
    }


logger.info(f"Setting CORS origins to {cors_origins_kwargs}")
logger.info(f"{TRACECAT__APP_ENV =}")
app.add_middleware(
    CORSMiddleware,
    **cors_origins_kwargs,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the Scheduler."}


@app.get("/health")
def check_health() -> dict[str, str]:
    return {"message": "Hello world. I am the Scheduler. This is the health endpoint."}


@app.get("/workflows/{workflow_id}/schedules", response_model=list[WorkflowSchedule])
def list_schedules(role: Annotated[Role, Depends(authenticate_user)], workflow_id: str):
    """List all schedules for a given workflow ID."""
    with Session(engine) as session:
        schedules = session.exec(
            select(WorkflowSchedule).where(
                WorkflowSchedule.owner_id == role.user_id,
                WorkflowSchedule.workflow_id == workflow_id,
            )
        ).all()
        return schedules


@app.get(
    "/workflows/{workflow_id}/schedules/{schedule_id}", response_model=WorkflowSchedule
)
def get_schedule(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    schedule_id: str,
):
    """Get schedule for given workflow and schedule ID."""
    with Session(engine) as session:
        # Get Workflow given workflow_id
        statement = select(WorkflowSchedule).where(
            WorkflowSchedule.owner_id == role.user_id,
            WorkflowSchedule.id == schedule_id,
            WorkflowSchedule.workflow_id == workflow_id,  # Redundant, but for clarity
        )
        schedule = session.exec(statement).one_or_none()
        if schedule is None:
            raise HTTPException(status_code=404, detail="Schedule not found")


@app.post("/workflows/{workflow_id}/schedules", response_model=WorkflowSchedule)
def create_schedule(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    params: WorkflowScheduleParams,
):
    """Set a schedule for a given workflow ID."""
    with Session(engine) as session:
        schedule = WorkflowSchedule(
            owner_id=role.user_id, **params.model_dump(), workflow_id=workflow_id
        )
        session.add(schedule)
        session.commit()
        session.refresh(schedule)
        return schedule


@app.put(
    "/workflows/{workflow_id}/schedules/{schedule_id}", response_model=WorkflowSchedule
)
def update_schedule(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    schedule_id: str,
    params: WorkflowScheduleParams,
):
    """Update schedule for a given workflow and schedule ID."""
    with Session(engine) as session:
        schedule = (
            select(WorkflowSchedule)
            .where(
                WorkflowSchedule.owner_id == role.user_id,
                WorkflowSchedule.id == schedule_id,
                WorkflowSchedule.workflow_id == workflow_id,
            )
            .one_or_none()
        )

        if schedule is None:
            raise HTTPException(status_code=404, detail="Schedule not found")

        if params.cron is not None:
            schedule.cron = params.cron
        if params.entrypoint_key is not None:
            schedule.entrypoint_key = params.entrypoint_key
        if params.entrypoint_payload is not None:
            schedule.entrypoint_payload = params.entrypoint_payload

        session.add(schedule)
        session.commit()
        session.refresh(schedule)
        return schedule


@app.delete("/workflows/{workflow_id}/schedules/{schedule_id}")
def delete_schedule(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    schedule_id: str,
):
    with Session(engine) as session:
        schedule = session.where(
            WorkflowSchedule.owner_id == role.user_id,
            WorkflowSchedule.id == schedule_id,
            WorkflowSchedule.workflow_id == workflow_id,
        ).one_or_none()
        if schedule is None:
            raise HTTPException(status_code=404, detail="Schedule not found")

        session.delete(schedule)
        session.commit()
        return {"message": "Schedule deleted successfully"}
