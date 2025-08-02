"""Integration test: install and execute a remote custom registry repo.

This checks the full "happy path" in production mode:
1. Login with test credentials
2. Get or create a RegistryRepository record that points to the internal-registry repo
3. Create GitHub SSH deploy key secret for repository access
4. Sync the repo, which clones via git+ssh and installs with `uv add`
5. Verify the action is registered in the repository
6. Run a DSLWorkflow that calls `tracecat.math.add_300` via the executor
   service, verifying the executor container can import and execute the
   freshly-installed code
"""

from __future__ import annotations

import os
import uuid

import pytest
import requests
from pydantic import SecretStr

from tests.shared import generate_test_exec_id
from tracecat.dsl.models import ActionStatement, RunActionInput, RunContext, StreamID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.secrets.models import SecretCreate, SecretKeyValue
from tracecat.types.auth import system_role

GIT_SSH_URL = "git+ssh://git@github.com/TracecatHQ/internal-registry.git"


@pytest.mark.anyio
async def test_remote_custom_registry_repo() -> None:
    """
    End-to-end assertion that the executor can:
    1. Clone & install the remote repo via `uv add`.
    2. Execute the `tracecat.math.add_300` action inside a workflow.
    """
    logger.info("Starting remote custom registry repo test")

    # ---------------------------------------------------------------------
    # 1.  Create a session and login with test credentials
    # ---------------------------------------------------------------------
    logger.info("Step 1: Creating session and logging in with test credentials")
    base_url = os.environ.get("TRACECAT__PUBLIC_API_URL", "http://localhost/api")
    session = requests.Session()

    # Login with test credentials
    login_response = session.post(
        f"{base_url}/auth/login",
        data={"username": "test@tracecat.com", "password": "password1234"},
    )
    assert login_response.status_code == 204, f"Login failed: {login_response.text}"
    logger.info("Successfully logged in with test credentials")

    # ---------------------------------------------------------------------
    # 2.  Get or create a RegistryRepository pointing to the remote Git repo
    # ---------------------------------------------------------------------
    logger.info(
        "Step 2: Setting up RegistryRepository for remote Git repo", git_url=GIT_SSH_URL
    )
    # Get repository
    get_response = session.get(f"{base_url}/registry/repos")
    assert get_response.status_code == 200, f"Get failed: {get_response.text}"
    # Search for existing repository with matching origin
    repo_list = get_response.json()
    existing_repo = next(
        (repo for repo in repo_list if repo["origin"] == GIT_SSH_URL), None
    )

    if existing_repo:
        repository_id = existing_repo["id"]
        logger.info("Using existing repository", repository_id=repository_id)
    else:
        # Create repository if not found
        logger.info("Creating new repository for remote Git repo")
        create_response = session.post(
            f"{base_url}/registry/repos",
            json={"origin": GIT_SSH_URL},
        )
        assert create_response.status_code == 201, (
            f"Create failed: {create_response.text}"
        )
        repo_data = create_response.json()
        repository_id = repo_data["id"]
        logger.info("Created new repository", repository_id=repository_id)

    # ---------------------------------------------------------------------
    # 3.  Create GitHub SSH deploy key secret for repository access
    # ---------------------------------------------------------------------
    logger.info("Step 3: Creating GitHub SSH deploy key secret")
    github_deploy_key = os.environ.get("CUSTOM_REPO_SSH_PRIVATE_KEY")
    assert github_deploy_key, (
        "CUSTOM_REPO_SSH_PRIVATE_KEY environment variable is not set"
    )

    # Create or update the github-ssh-key secret
    secret_response = session.post(
        f"{base_url}/organization/secrets",
        json=SecretCreate(
            name="github-ssh-key",
            keys=[
                SecretKeyValue(
                    key="github-ssh-key",
                    value=SecretStr(github_deploy_key),
                )
            ],
            description="GitHub SSH deploy key for accessing private repositories",
        ).model_dump(mode="json"),
    )
    # Accept 200 (updated), 201 (created), and 409 (already exists) status codes
    assert secret_response.status_code in [200, 201, 409], (
        f"Secret creation failed: {secret_response.text}"
    )
    logger.info("GitHub SSH deploy key secret created successfully")

    # ---------------------------------------------------------------------
    # 4.  Sync the repository (clones & installs with uv add)
    # ---------------------------------------------------------------------
    logger.info(
        "Step 4: Syncing repository (cloning and installing with uv add)",
        repository_id=repository_id,
    )
    sync_response = session.post(
        f"{base_url}/registry/repos/{repository_id}/sync",
    )
    assert sync_response.status_code == 204, f"Sync failed: {sync_response.text}"
    logger.info("Repository sync completed successfully")

    # ---------------------------------------------------------------------
    # 5.  Verify the action is registered
    # ---------------------------------------------------------------------
    logger.info("Step 5: Verifying action registration")
    get_response = session.get(f"{base_url}/registry/repos/{repository_id}")
    assert get_response.status_code == 200
    repo_details = get_response.json()

    # Check that our action is in the actions list
    actions = repo_details.get("actions", [])
    action_found = any(
        action["action"] == "tracecat.math.add_300" for action in actions
    )
    assert action_found, "tracecat.math.add_300 should be in the repository actions"
    logger.info(
        "Action verification successful",
        action="tracecat.math.add_300",
        total_actions=len(actions),
    )

    # ---------------------------------------------------------------------
    # 6.  Execute the action via the executor service
    # ---------------------------------------------------------------------
    logger.info("Step 6: Executing action via executor service")
    # Hit the executor directly to run the action
    # Grab the service key from the environment
    service_key = os.environ.get("TRACECAT__SERVICE_KEY")
    assert service_key, "TRACECAT__SERVICE_KEY is not set"
    role = system_role()
    run_action_response = session.post(
        "http://localhost:8001/api/executor/run/tracecat.math.add_300",
        headers={
            "x-tracecat-service-key": service_key,
            **role.to_headers(),
        },
        json=RunActionInput(
            task=ActionStatement(
                ref="a",
                action="tracecat.math.add_300",
                args={"number": 1},
            ),
            stream_id=StreamID("stream_id"),
            exec_context={},
            run_context=RunContext(
                wf_id=WorkflowUUID.new_uuid4(),
                wf_exec_id=generate_test_exec_id("test-workflow"),
                wf_run_id=uuid.uuid4(),
                environment="default",
            ),
        ).model_dump(mode="json"),
    )
    assert run_action_response.status_code == 200, (
        f"Run action failed: {run_action_response.text}"
    )
    result = run_action_response.json()
    assert result == 301, "Result should be 301"
    logger.info("Action execution successful", result=result)
