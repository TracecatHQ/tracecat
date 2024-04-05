"""Test scheduled workflow cron jobs.

Integration test:
1. Start the scheduler server.
2. Mock the runner server.
3. Create scheduled workflows using the API.
4. Check that the scheduler runs the workflows at the correct time by calling the runner server.
"""

import asyncio
import json
import re
import time
from datetime import datetime

import polars as pl
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from tracecat.auth import (
    Role,
    authenticate_user,
)
from tracecat.config import TRACECAT__RUNNER_URL
from tracecat.db import TRACECAT__DB_URI, Workflow, WorkflowSchedule
from tracecat.scheduler import app, engine, start_scheduler

TEST_SCHEDULER_INTERVAL_SECONDS = 10
TEST_WORKFLOW_RUN_TIMEOUT = 40  # seconds
TEST_USER_ID = "3f1606c4-351e-41df-acb4-fb6e243fd071"
TEST_OTHER_USER_ID = "83de065f-e933-4a69-8a7a-2d796238575d"


client = TestClient(app=app)


# Override authentication dependencies
app.dependency_overrides[authenticate_user] = lambda: Role(
    type="user", user_id=TEST_USER_ID
)


@pytest.fixture(autouse=True)
def initialize_database():
    """Create Workflow and WorkflowSchedule tables before starting tests."""
    Workflow.metadata.create_all(engine)
    WorkflowSchedule.metadata.create_all(engine)
    yield
    Workflow.metadata.drop_all(engine)
    WorkflowSchedule.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def schedules_table():
    # Define mock schedules
    mock_schedules = pl.DataFrame(
        {
            "id": [
                # NOTE: Schedule IDs are UUIDs in production
                # We use human readable IDs for testing
                "schedule-0",
                "schedule-1",
                "schedule-2",
                "schedule-3",
                "schedule-4",
            ],
            "workflow_id": [
                # NOTE: Workflow IDs are UUIDs in production
                # We use human readable IDs for testing
                "workflow-0",
                "workflow-0",
                "workflow-1",
                "workflow-2",
                "workflow-3",
            ],
            "owner_id": [
                TEST_USER_ID,
                TEST_USER_ID,
                TEST_USER_ID,
                TEST_USER_ID,
                TEST_OTHER_USER_ID,
            ],
            "cron": [
                "*/10 * * * * *",
                "*/20 * * * * *",
                "*/30 * * * * *",
                "*/60 * * * * *",
                "*/60 * * * * *",
            ],
            "entrypoint_key": ["mocked", "mocked", "mocked", "mocked", "mocked"],
            "entrypoint_payload": [
                '{"key": "mocked"}',
                '{"key": "mocked"}',
                '{"key": "mocked"}',
                '{"key": "mocked"}',
                '{"key": "mocked"}',
            ],
        }
    )
    mock_schedules.write_database(
        table_name="workflowschedule",
        connection=TRACECAT__DB_URI,
        if_table_exists="append",
    )
    # Wait 1 second to avoid same created_at timestamp
    time.sleep(1)
    yield mock_schedules
    # Delete all rows in workflowschedule table
    with Session(engine) as session:
        session.exec(delete(WorkflowSchedule))


@pytest.mark.parametrize(
    "payload",
    ["{}", '{"key": "value"}'],
    ids=["empty_payload", "nonempty_payload"],
)
def test_create_schedule(payload, schedules_table):
    """Schedule is created successfully in an empty table."""

    workflow_id = "workflow-1"
    response = client.post(
        f"/workflows/{workflow_id}/schedules",
        json={
            "cron": "*/5 * * * * *",
            "entrypoint_key": "start",
            "entrypoint_payload": payload,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["workflow_id"] == workflow_id
    assert data["cron"] == "*/5 * * * * *"

    with Session(engine) as session:
        schedule = session.exec(
            select(WorkflowSchedule)
            .where(WorkflowSchedule.workflow_id == workflow_id)
            .order_by(WorkflowSchedule.created_at.desc())
        ).first()

    assert schedule is not None
    assert schedule.id not in schedules_table["id"].to_list()
    assert schedule.workflow_id == workflow_id
    assert schedule.owner_id == TEST_USER_ID
    assert schedule.cron == "*/5 * * * * *"
    assert schedule.entrypoint_key == "start"
    assert schedule.entrypoint_payload == payload


def test_list_schedules(schedules_table):
    """List all schedules in the table under specific workflow and user."""
    response = client.get("/workflows/workflow-0/schedules")
    assert response.status_code == 200
    schedules = response.json()
    assert len(schedules) == len(
        schedules_table.filter(pl.col("workflow_id") == "workflow-0")
    )


def test_get_schedule(schedules_table):
    """Get a specific schedule."""
    schedule_id = "schedule-0"
    response = client.get(f"/workflows/workflow-0/schedules/{schedule_id}")
    assert response.status_code == 200
    schedule = response.json()
    assert schedule["id"] == schedule_id


def test_update_schedule(schedules_table):
    """Update the cron string for a specific schedule."""
    schedule_id = "schedule-0"
    response = client.put(
        f"/workflows/workflow-0/schedules/{schedule_id}",
        json={"cron": "15 * * * * *"},
    )
    assert response.status_code == 200

    with Session(engine) as session:
        schedule = session.get(WorkflowSchedule, schedule_id)

    assert schedule.cron == "15 * * * * *"


def test_delete_schedule(schedules_table):
    """Delete a specific schedule."""
    # Use a known schedule_id from your setup
    schedule_id = "schedule-3"
    response = client.delete(f"/workflows/workflow-2/schedules/{schedule_id}")
    assert response.status_code == 200

    with Session(engine) as session:
        schedule = session.get(WorkflowSchedule, schedule_id)

    assert schedule is None


def test_delete_schedule_wrong_authenticated_user(schedules_table):
    """Raise 404 error when trying to delete another users schedule."""
    # Use a known schedule_id from your setup
    schedule_id = "schedule-4"
    response = client.delete(f"/workflows/workflow-3/schedules/{schedule_id}")
    assert response.status_code == 404


@pytest.mark.slow
@pytest.mark.asyncio
@respx.mock(base_url=TRACECAT__RUNNER_URL)
async def test_workflow_scheduler_runs(respx_mock):
    """Workflow scheduler successfully calls the runner to start
    three scheduled workflows at the correct order.

    Timeout after 40 seconds.
    """

    # Wait until the start of the next minute
    current_time = datetime.now()
    sleep_seconds = 60 - current_time.second
    await asyncio.sleep(sleep_seconds)

    # Create schedules
    schedules = [
        {
            "id": "schedule-0",
            "workflow_id": "workflow-0",
            "owner_id": TEST_USER_ID,
            "cron": "* * * * * 15",
            "entrypoint_key": "start_15",
            "entrypoint_payload": '{"key": "value_0"}',
        },
        {
            "id": "schedule-other-user",
            "workflow_id": "workflow-other-user",
            "owner_id": TEST_OTHER_USER_ID,
            "cron": "* * * * * 15",
            "entrypoint_key": "start_other_user",
            "entrypoint_payload": "{}",
        },
        {
            "id": "schedule-1",
            "workflow_id": "workflow-0",
            "owner_id": TEST_USER_ID,
            "cron": "* * * * * 30",
            "entrypoint_key": "start_30a",
            "entrypoint_payload": '{"key": "value_1"}',
        },
        {
            "id": "schedule-2",
            "workflow_id": "workflow-1",
            "owner_id": TEST_USER_ID,
            "cron": "* * * * * 30",
            "entrypoint_key": "start_30b",
            "entrypoint_payload": '{"key": "value_2"}',
        },
        {
            "id": "schedule-3",
            "workflow_id": "workflow-2",
            "owner_id": TEST_USER_ID,
            "cron": "* * * * * 50",
            "entrypoint_key": "start_z",
            "entrypoint_payload": '{"key": "value_3"}',
        },
    ]

    # Add schedules to database using Polars
    mocked_schedules = pl.from_dicts(schedules)
    mocked_schedules.write_database(
        table_name="workflowschedule",
        connection=TRACECAT__DB_URI,
        if_table_exists="append",
    )

    route = respx_mock.post(re.compile(r"/workflows/.+"))

    # Start scheduler in the background
    await start_scheduler(
        delay_seconds=3, interval_seconds=TEST_SCHEDULER_INTERVAL_SECONDS
    )
    await asyncio.sleep(TEST_WORKFLOW_RUN_TIMEOUT)

    assert route.called
    assert route.call_count == 4

    # Assert that the captured data is in the expected order
    # NOTE: last schedule is not expected to run
    calls = route.calls
    start_keys = [json.loads(call.request.content) for call in calls]
    assert start_keys == [
        {
            "entrypoint_key": obj["entrypoint_key"],
            "entrypoint_payload": obj["entrypoint_payload"],
        }
        for obj in schedules[:-1]
    ]
