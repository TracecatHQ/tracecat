"""Run end-to-end workflows in a test environment.

We trigger a workflow by calling a webhook.
To test successful completion, we check the status of the workflow run.

Environment variables required:
- TRACECAT__STORAGE_PATH: Path to the directory where the SQLite database will be stored
- TRACECAT__API_URL
- TRACECAT__RUNNER_URL
- TRACECAT__SERVICE_KEY

Note: API and runner servers must be running.
"""

import logging
import os
import threading

import polars as pl
import pytest
import uvicorn
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth import (
    Role,
    authenticate_service,
    authenticate_user,
    authenticate_user_or_service,
)
from tracecat.db import TRACECAT__DB_URI
from tracecat.runner.app import app as runner_app
from tracecat.runner.app import valid_workflow

TEST_WORKFLOW_RUN_TIMEOUT = 60  # seconds
TEST_USER_ID = "3f1606c4-351e-41df-acb4-fb6e243fd071"


client = TestClient(app=app)


# Override authentication dependencies
app.dependency_overrides[authenticate_user] = lambda: Role(
    type="user", user_id=TEST_USER_ID
)
app.dependency_overrides[authenticate_service] = lambda: Role(
    type="service", service_id="tracecat-runner"
)
app.dependency_overrides[authenticate_user_or_service] = lambda: Role(
    type="user", user_id=TEST_USER_ID, service_id="tracecat-runner"
)
runner_app.dependency_overrides[authenticate_service] = lambda: Role(
    type="service", user_id="tracecat-api"
)
runner_app.dependency_overrides[valid_workflow] = lambda workflow_id: workflow_id


# Define a fixture for the Runner server
@pytest.fixture(scope="session", autouse=True)
def start_servers():
    thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="localhost", port=8000, log_level="info")
    )
    thread = threading.Thread(
        target=lambda: uvicorn.run(
            runner_app, host="localhost", port=8001, log_level="info"
        )
    )
    thread.daemon = True
    thread.start()
    yield
    # Cleanup code here if needed


def create_test_db():
    with TestClient(app=app) as client:
        # Load test data
        actions = pl.read_csv("tests/data/actions.csv").fill_null("")
        webhooks = pl.read_csv("tests/data/webhooks.csv").fill_null("")
        workflows = pl.read_csv("tests/data/workflows.csv").fill_null("")

        logging.info("Test Actions:\n%s", actions)
        logging.info("Test Webhooks:\n%s", webhooks)
        logging.info("Test Workflows:\n%s", workflows)

        # Insert data into database
        actions.write_database(
            table_name="action",
            connection=TRACECAT__DB_URI,
            if_table_exists="append",
        )
        webhooks.write_database(
            table_name="webhook",
            connection=TRACECAT__DB_URI,
            if_table_exists="append",
        )
        workflows.write_database(
            table_name="workflow",
            connection=TRACECAT__DB_URI,
            if_table_exists="append",
        )

        # Insert secrets into database
        secrets = [
            {
                "name": "URL_SCAN_KEY",
                "value": os.environ["TRACECAT__TESTING__URL_SCAN_KEY"],
            },
        ]
        for secret in secrets:
            client.put("/secrets", json=secret)


def pytest_generate_tests(metafunc):
    """Dynamically generate workflow triggers fixture,
    which is a list of dicts with workflow_id, action_id, and payload.
    """

    create_test_db()
    # Directly use the fixture data for parametrization
    query = "SELECT workflow_id, action_id FROM webhook"
    triggers = (
        pl.read_database_uri(query, uri=TRACECAT__DB_URI, engine="adbc")
        .join(pl.read_ndjson("tests/data/trigger_payloads.ndjson"), on="action_id")
        .to_dicts()
    )
    metafunc.parametrize(
        "workflow_trigger",
        triggers,
        ids=[str(trigger["workflow_id"]) for trigger in triggers],
    )


def test_workflow_run(workflow_trigger):
    """End-to-end integration test for a workflow run.

    Tests:
    - Workflow run is triggered by a webhook
    - Workflow sink actions complete successfully before timeout
    """
    trigger = workflow_trigger
    client.post(
        f"/workflows/{trigger['workflow_id']}/trigger",
        json={"action_key": trigger["action_id"], "payload": trigger["payload"]},
    )
    # Check status of workflow run
