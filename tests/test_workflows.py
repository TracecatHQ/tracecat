import json
import time
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Request, Response
from slugify import slugify

from tracecat.config import TRACECAT__API_URL
from tracecat.runner.app import app
from tracecat.types.api import ActionResponse, WorkflowResponse

TEST_WORKFLOWS_DIR = Path("tests/workflow_schemas")
TIMEOUT_PER_WORKFLOW = 10


# Create mock client for runner API

client = TestClient(app)

# Override check valid workflow dependency
# since it requires a running API endpoint


@pytest.fixture(
    params=[
        (
            "branching.json",
            "689cd16eba7a4d9897074e7c7ceed797.webhook",
            {"text": "happy"},
            {"text": "I am happy :)"},
        )
    ],
    ids=["branching"],
)
def sample_workflow(request) -> tuple[WorkflowResponse, str, dict[str, any]]:
    workflow_path, entrypoint_key, entrypoint_payload, workflow_result = request.param
    with open(TEST_WORKFLOWS_DIR / workflow_path) as f:
        workflow_response_object = json.load(f)
        # Create workflow response model
        workflow_response = WorkflowResponse(
            id=workflow_response_object["id"],
            title=workflow_response_object["title"],
            description=workflow_response_object["description"],
            status=workflow_response_object["status"],
            actions={
                action_id: ActionResponse(
                    # NOTE: This is not very nice at all...
                    # Need consolidate duplicate request / response params in API types
                    id=action_response["id"],
                    type=action_response["type"],
                    title=action_response["title"],
                    description=action_response["description"],
                    status=action_response["status"],
                    inputs=json.loads(action_response["inputs"]),
                    key=f"{action_id}.{slugify(action_response["title"], separator="_")}",
                )
                for action_id, action_response in workflow_response_object[
                    "actions"
                ].items()
            },
            object=workflow_response_object["object"],
        )

    return workflow_response, entrypoint_key, entrypoint_payload, workflow_result


TEST_WORKFLOW_RESULT = {}


def _capture_post_request(request: Request) -> dict[str, Any]:
    TEST_WORKFLOW_RESULT["result"] = json.loads(request.content)
    return Response(200, json={"status": "received"})


def test_all_workflows(sample_workflow):
    (
        workflow_response,
        entrypoint_key,
        entrypoint_payload,
        expected_workflow_result,
    ) = sample_workflow

    # Health check
    client.get("/")

    with respx.mock:
        workflow_id = workflow_response.id

        # Mock workflow getter from API side
        get_workflow_url = f"{TRACECAT__API_URL}/workflows/{workflow_id}"
        respx.get(get_workflow_url).mock(
            return_value=Response(200, json=workflow_response.model_dump())
        )

        # Mock getter to listen for sink http request
        sink_webhook_url = "http://testing/webhook"
        respx.post(sink_webhook_url).mock(side_effect=_capture_post_request)

        # Start workflow
        response = client.post(
            f"/workflows/{workflow_id}",
            json={
                "entrypoint_key": entrypoint_key,
                "entrypoint_payload": entrypoint_payload,
            },
        )
        response.raise_for_status()

    # Polling loop to wait for the background task to complete
    timeout = 10  # Max time to wait in seconds
    start_time = time.time()
    while time.time() - start_time < timeout:
        if "result" in TEST_WORKFLOW_RESULT:  # Check if the captured_data has been set
            break
        time.sleep(0.1)  # Sleep a bit before checking again

    # Wait for the background task with polling
    assert TEST_WORKFLOW_RESULT["result"] == expected_workflow_result
