"""Polling schedule to run workflows as cron job."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated, Any

import httpx
from croniter import croniter
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlmodel import Session, select
from tenacity import retry, stop_after_attempt, wait_exponential

from tracecat.auth import (
    AuthenticatedRunnerClient,
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
from tracecat.db import WorkflowSchedule, create_db_engine
from tracecat.logger import standard_logger
from tracecat.types.schedules import WorkflowScheduleParams

logger = standard_logger("scheduler")


engine: Engine


@retry(
    wait=wait_exponential(multiplier=1, min=4, max=10),
    stop=stop_after_attempt(3),
)
async def start_workflow(
    user_id: str,
    workflow_id: str,
    entrypoint_key: str,
    entrypoint_payload: dict[str, Any],
) -> httpx.Response:
    # Since we're using the same client instance for all requests,
    # we can set the rate limit directly in the httpx client configuration
    client_limits = httpx.Limits(max_connections=TRACECAT__SCHEDULE_MAX_CONNECTIONS)
    httpx.HTTPTransport(limits=client_limits)
    role = Role(type="service", service_id="tracecat-scheduler", user_id=user_id)
    async with AuthenticatedRunnerClient(role=role) as client:
        url = f"{TRACECAT__RUNNER_URL}/workflows/{workflow_id}"
        response = await client.post(
            url,
            json={
                "entrypoint_key": entrypoint_key,
                "entrypoint_payload": entrypoint_payload,
            },
        )
        response.raise_for_status()
        return response


async def run_scheduled_workflows(interval_seconds: int | None = None):
    """Run scheduled workflows on behalf of users."""

    interval_seconds = interval_seconds or TRACECAT__SCHEDULE_INTERVAL_SECONDS
    while True:
        now = datetime.now().replace(microsecond=0)
        prev = now - timedelta(seconds=interval_seconds)
        with Session(engine) as session:
            statement = select(WorkflowSchedule)
            schedules = session.exec(statement).all()
            responses = []
            for schedule in schedules:
                user_id = schedule.owner_id
                workflow_id = schedule.workflow_id
                cron = schedule.cron

                # Check if the schedule should run
                next_run = croniter(cron, prev).get_next(datetime)
                should_run = next_run <= now

                if should_run:
                    logger.info(
                        "✅ Run scheduled workflow: id=%s cron=%r", workflow_id, cron
                    )
                    response = await start_workflow(
                        user_id=user_id,
                        workflow_id=workflow_id,
                        entrypoint_key=schedule.entrypoint_key,
                        entrypoint_payload=schedule.entrypoint_payload,
                    )
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        logger.error(
                            "Failed to schedule workflo: id=%s cron=%r",
                            workflow_id,
                            cron,
                            exc_info=e,
                        )
                    else:
                        responses.append(response)
                else:
                    logger.info(
                        "⏩ Skip scheduled workflow: id=%s cron=%r | Next run: %s",
                        workflow_id,
                        cron,
                        next_run,
                    )

            for response in responses:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    logger.error(
                        "Failed to schedule workflow: id=%s cron=%r.",
                        workflow_id,
                        cron,
                        exc_info=e,
                    )
        await asyncio.sleep(interval_seconds)


async def start_scheduler(delay_seconds: int = 30, interval_seconds: int | None = None):
    # Wait for API service to start
    await asyncio.sleep(delay_seconds)
    # Run scheduled workflows as a background task
    asyncio.create_task(run_scheduled_workflows(interval_seconds))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = create_db_engine()
    await start_scheduler()
    yield


app = FastAPI(lifespan=lifespan)


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
    engine = create_db_engine()
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
        return schedule


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
        statement = select(WorkflowSchedule).where(
            WorkflowSchedule.owner_id == role.user_id,
            WorkflowSchedule.id == schedule_id,
            WorkflowSchedule.workflow_id == workflow_id,
        )
        schedule = session.exec(statement).one_or_none()
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
        statement = select(WorkflowSchedule).where(
            WorkflowSchedule.owner_id == role.user_id,
            WorkflowSchedule.id == schedule_id,
            WorkflowSchedule.workflow_id == workflow_id,
        )
        schedule = session.exec(statement).one_or_none()
        if schedule is None:
            raise HTTPException(status_code=404, detail="Schedule not found")

        session.delete(schedule)
        session.commit()
        return {"message": "Schedule deleted successfully"}
