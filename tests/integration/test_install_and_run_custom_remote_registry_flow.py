"""Integration test: install and execute a remote custom registry repo.

This checks the full "happy path" in production mode:
1. Login with test credentials
2. Get or create a RegistryRepository record that points to the internal-registry repo
3. Create GitHub SSH deploy key secret for repository access
4. Sync the repo, which clones via git+ssh and installs with `uv add`
5. Verify the action is registered in the repository
6. Run a DSLWorkflow that calls `tracecat.math.add_300` via Temporal,
   verifying the worker can import and execute the freshly-installed code
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
import requests
from pydantic import SecretStr, ValidationError
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy

from tests.shared import generate_test_exec_id, to_data
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretCreate
from tracecat.settings.schemas import GitSettingsUpdate

GIT_SSH_URL = "git+ssh://git@github.com/TracecatHQ/internal-registry.git"


@pytest.mark.anyio
async def test_remote_custom_registry_repo() -> None:
    """
    End-to-end assertion that the worker can:
    1. Clone & install the remote repo via `uv add`.
    2. Execute the `tracecat.math.add_300` action inside a workflow via Temporal.
    """
    logger.info("Starting remote custom registry repo test")

    # ---------------------------------------------------------------------
    # 1.  Create a session and register/login with test credentials
    # ---------------------------------------------------------------------
    logger.info("Step 1: Creating session and setting up test user")
    base_url = os.environ.get("TRACECAT__PUBLIC_API_URL", "http://localhost/api")
    session = requests.Session()

    # First, try to register the test user (in case it doesn't exist)
    register_response = session.post(
        f"{base_url}/auth/register",
        json={"email": "test@tracecat.com", "password": "password1234"},
    )
    if register_response.status_code == 201:
        logger.info("Successfully registered test user")
    elif register_response.status_code in [400, 409]:
        logger.info("Test user already exists, proceeding to login")
    else:
        pytest.fail(
            f"Registration returned unexpected status: {register_response.status_code} - {register_response.text}"
        )

    # Login with test credentials
    login_response = session.post(
        f"{base_url}/auth/login",
        data={"username": "test@tracecat.com", "password": "password1234"},
    )
    assert login_response.status_code == 204, f"Login failed: {login_response.text}"
    logger.info("Successfully logged in with test credentials")

    # Set organization context (required for superusers and org-scoped endpoints)
    # Use the admin endpoint which doesn't require org context
    orgs_resp = session.get(f"{base_url}/admin/organizations")
    assert orgs_resp.status_code == 200, (
        f"Failed to get organizations: {orgs_resp.text}"
    )
    orgs_list = orgs_resp.json()
    assert len(orgs_list) > 0, "No organizations found"
    org_id = orgs_list[0]["id"]
    session.cookies.set("tracecat-org-id", org_id)
    logger.info("Set organization context", organization_id=org_id)

    # ---------------------------------------------------------------------
    # 2.  Get or create a RegistryRepository pointing to the remote Git repo
    # ---------------------------------------------------------------------
    logger.info(
        "Step 2: Setting up organization settings and RegistryRepository for remote Git repo",
        git_url=GIT_SSH_URL,
    )

    # Set organization settings for git repo URL and package name
    logger.info("Setting organization git repo URL setting")
    git_repo_setting_response = session.patch(
        f"{base_url}/settings/git",
        json=GitSettingsUpdate(
            git_repo_url=GIT_SSH_URL,
            git_repo_package_name="custom_actions",
        ).model_dump(mode="json"),
    )
    assert git_repo_setting_response.status_code == 204, (
        f"Git repo URL setting failed: {git_repo_setting_response.text}"
    )
    logger.info("Git repo URL setting configured successfully")

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
    if not github_deploy_key:
        pytest.fail("CUSTOM_REPO_SSH_PRIVATE_KEY environment variable is not set")
    # Validate that newlines are preserved in the decoded SSH private key
    assert "\n" in github_deploy_key, "Key should contain newlines"
    secret_github_ssh_key = SecretStr(github_deploy_key)

    # Create or update the github-ssh-key secret
    # Create the secret model and serialize with custom handling for SecretStr
    secret_dict = {
        "type": SecretType.SSH_KEY.value,
        "name": "github-ssh-key",
        "keys": [
            {
                "key": "PRIVATE_KEY",
                "value": secret_github_ssh_key.get_secret_value(),
            }
        ],
        "description": "GitHub SSH deploy key for accessing private repositories",
    }

    # Validate the secret dict
    try:
        SecretCreate.model_validate(secret_dict)
    except ValidationError:
        pytest.fail("SecretCreate validation failed")

    secret_response = session.post(
        f"{base_url}/organization/secrets",
        json=secret_dict,
    )
    # Accept 200 (updated), 201 (created), and 409 (already exists) status codes
    assert secret_response.status_code in [200, 201, 409], (
        f"Secret creation failed: {secret_response.text}"
    )
    logger.info(
        "GitHub SSH deploy key secret created successfully",
        length=len(secret_github_ssh_key),
    )

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
    assert sync_response.status_code == 200, f"Sync failed: {sync_response.text}"
    sync_data = sync_response.json()
    assert sync_data["success"] is True, f"Sync was not successful: {sync_data}"
    logger.info(
        "Repository sync completed successfully",
        version=sync_data.get("version"),
        actions_count=sync_data.get("actions_count"),
    )

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
    # 6.  Execute the action via Temporal workflow
    # ---------------------------------------------------------------------
    logger.info("Step 6: Executing action via Temporal workflow")

    # Get the workspace ID and organization ID from the API to create a proper role
    workspaces_response = session.get(f"{base_url}/workspaces")
    assert workspaces_response.status_code == 200, (
        f"Failed to get workspaces: {workspaces_response.text}"
    )
    workspaces = workspaces_response.json()
    assert len(workspaces) > 0, "No workspaces found"
    workspace_id = uuid.UUID(workspaces[0]["id"])

    # Fetch full workspace details to get organization_id
    workspace_response = session.get(f"{base_url}/workspaces/{workspace_id}")
    assert workspace_response.status_code == 200, (
        f"Failed to get workspace details: {workspace_response.text}"
    )
    workspace_data = workspace_response.json()
    organization_id = uuid.UUID(workspace_data["organization_id"])
    logger.info(
        "Using workspace for workflow execution",
        workspace_id=workspace_id,
        organization_id=organization_id,
    )

    # Create a role for workflow execution
    role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=uuid.UUID(int=0),
    )
    ctx_role.set(role)

    # Create a simple DSL workflow that calls the action
    test_name = "test_remote_custom_registry_repo"
    wf_id = WorkflowUUID.new_uuid4()
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title=test_name,
        description="Test workflow for custom registry action",
        entrypoint=DSLEntrypoint(ref="add_action"),
        actions=[
            ActionStatement(
                ref="add_action",
                action="tracecat.math.add_300",
                args={"number": 1},
            )
        ],
        returns="${{ ACTIONS.add_action.result }}",
    )

    # Get Temporal client and execute workflow
    # The worker service running in Docker will handle the execution
    client = await get_temporal_client()
    # Use the default task queue that matches the Docker worker
    # (conftest sets config.TEMPORAL__CLUSTER_QUEUE to "test-tracecat-task-queue" for unit tests,
    # but this integration test runs against the Docker worker which uses the default queue)
    task_queue = "tracecat-task-queue"

    try:
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=role, wf_id=wf_id),
            id=wf_exec_id,
            task_queue=task_queue,
            retry_policy=RetryPolicy(maximum_attempts=1),
            execution_timeout=timedelta(seconds=60),  # Prevent indefinite hangs
        )
        # The workflow returns the action result via the returns expression
        # Unwrap StoredObject to compare actual data (handles both inline and external)
        actual_result = await to_data(result)
        assert actual_result == 301, f"Result should be 301, got {actual_result}"
        logger.info("Action execution successful", result=actual_result)
    except WorkflowFailureError as e:
        pytest.fail(f"Workflow execution failed: {e}")
