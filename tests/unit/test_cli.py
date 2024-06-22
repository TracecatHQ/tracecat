"""Test CLI commands."""

import os
import subprocess

IMAGE_TAG = os.environ["TRACECAT__IMAGE_TAG"]
DOCKER_RUN_CMD = [
    "docker",
    "run",
    "--rm",  # Automatically remove the container when it exits
    "--network host",  # Use the host's network stack to call localhost
    f"ghcr.io/tracecathq/tracecat:{IMAGE_TAG}",
]


def test_create_secret():
    secret_name = "test_secret"
    keyvalues = ["KEY1=VAL1", "KEY2=VAL2"]
    cmd = [
        *DOCKER_RUN_CMD,
        "secret",
        "create",
        secret_name,
        *keyvalues,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    assert "Secret created successfully!" in result.stdout


def test_delete_secret():
    secret_name = "test_secret_to_delete"
    keyvalues = ["KEY1=VAL1", "KEY2=VAL2"]

    # Create the secret first
    create_cmd = [
        *DOCKER_RUN_CMD,
        "secret",
        "create",
        secret_name,
        *keyvalues,
    ]
    create_result = subprocess.run(create_cmd, capture_output=True, text=True)
    assert create_result.returncode == 0
    assert "Secret created successfully!" in create_result.stdout

    # Now delete the secret
    delete_cmd = [
        *DOCKER_RUN_CMD,
        "secret",
        "delete",
        secret_name,
    ]
    delete_result = subprocess.run(delete_cmd, capture_output=True, text=True)
    assert delete_result.returncode == 0
    assert "Secret deleted successfully!" in delete_result.stdout


def test_list_secrets():
    secret_name1 = "test_secret1"
    secret_name2 = "test_secret2"
    keyvalues1 = ["KEY1=VAL1"]
    keyvalues2 = ["KEY2=VAL2"]

    # Create the first secret
    create_cmd1 = [
        *DOCKER_RUN_CMD,
        "secret",
        "create",
        secret_name1,
        *keyvalues1,
    ]
    create_result1 = subprocess.run(create_cmd1, capture_output=True, text=True)
    assert create_result1.returncode == 0
    assert "Secret created successfully!" in create_result1.stdout

    # Create the second secret
    create_cmd2 = [
        *DOCKER_RUN_CMD,
        "secret",
        "create",
        secret_name2,
        *keyvalues2,
    ]
    create_result2 = subprocess.run(create_cmd2, capture_output=True, text=True)
    assert create_result2.returncode == 0
    assert "Secret created successfully!" in create_result2.stdout

    # Now list the secrets
    list_cmd = [
        *DOCKER_RUN_CMD,
        "secret",
        "list",
    ]
    list_result = subprocess.run(list_cmd, capture_output=True, text=True)
    assert list_result.returncode == 0
    assert (
        "Secrets" in list_result.stdout
    )  # Check if the table title "Secrets" is in the output
    assert secret_name1 in list_result.stdout  # Check if the created secrets are listed
    assert secret_name2 in list_result.stdout


def test_create_workflow():
    title = "Test Workflow"
    description = "This is a test workflow"
    cmd = [
        *DOCKER_RUN_CMD,
        "workflow",
        "create",
        "--title",
        title,
        "--description",
        description,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    assert "Created workflow" in result.stdout


def test_list_workflows():
    # Create first workflow
    title1 = "Test Workflow 1"
    description1 = "This is the first test workflow"
    create_cmd1 = [
        *DOCKER_RUN_CMD,
        "workflow",
        "create",
        "--title",
        title1,
        "--description",
        description1,
    ]
    create_result1 = subprocess.run(create_cmd1, capture_output=True, text=True)
    assert create_result1.returncode == 0
    assert "Created workflow" in create_result1.stdout

    # Create second workflow
    title2 = "Test Workflow 2"
    description2 = "This is the second test workflow"
    create_cmd2 = [
        *DOCKER_RUN_CMD,
        "workflow",
        "create",
        "--title",
        title2,
        "--description",
        description2,
    ]
    create_result2 = subprocess.run(create_cmd2, capture_output=True, text=True)
    assert create_result2.returncode == 0
    assert "Created workflow" in create_result2.stdout

    # List workflows
    list_cmd = [
        *DOCKER_RUN_CMD,
        "workflow",
        "list",
    ]
    list_result = subprocess.run(list_cmd, capture_output=True, text=True)
    assert list_result.returncode == 0
    assert (
        "Workfows" in list_result.stdout
    )  # Assuming the dynamic_table has a title "Workfows"
    assert title1 in list_result.stdout  # Check if the first created workflow is listed
    assert (
        title2 in list_result.stdout
    )  # Check if the second created workflow is listed
