"""Test CLI commands."""

import subprocess
from pathlib import Path

import pytest

DATA_PATH = Path(__file__).parent.parent.joinpath("data/workflows")


@pytest.fixture(scope="session", autouse=True)
def mock_login():
    cmd = [
        "tracecat",
        "auth",
        "login",
        "--username",
        "admin@domain.com",
        "--password",
        "password",
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    yield
    subprocess.run(["tracecat", "auth", "logout"], capture_output=True, text=True)


def test_whoami():
    cmd = ["tracecat", "auth", "whoami"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    data = result.stdout.replace("\n", " ")
    assert "admin@domain.com" in data


def test_create_secret():
    secret_name = "__test_secret"
    keyvalues = ["KEY1=VAL1", "KEY2=VAL2"]
    cmd = [
        "tracecat",
        "secret",
        "create",
        secret_name,
        *keyvalues,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0


def test_delete_secret():
    secret_name = "__test_secret_to_delete"
    keyvalues = ["KEY1=VAL1", "KEY2=VAL2"]

    # Create the secret first
    create_cmd = [
        "tracecat",
        "secret",
        "create",
        secret_name,
        *keyvalues,
    ]
    create_result = subprocess.run(create_cmd, capture_output=True, text=True)
    assert create_result.returncode == 0, create_result.stderr
    assert "Secret created successfully!" in create_result.stdout, create_result.stderr

    # XXX: Must run this command in a shell to pipe the 'y' input to the command
    delete_cmd = f"echo 'y' | tracecat secret delete {secret_name}"
    delete_result = subprocess.run(
        delete_cmd, capture_output=True, text=True, shell=True
    )
    assert delete_result.returncode == 0, delete_result.stderr
    assert "Secret deleted successfully!" in delete_result.stdout, delete_result.stderr


def test_list_secrets():
    secret_name1 = "__test_secret1"
    secret_name2 = "__test_secret2"
    keyvalues1 = ["KEY1=VAL1"]
    keyvalues2 = ["KEY2=VAL2"]

    # Create the first secret
    create_cmd1 = [
        "tracecat",
        "secret",
        "create",
        secret_name1,
        *keyvalues1,
    ]
    create_result1 = subprocess.run(create_cmd1, capture_output=True, text=True)
    assert create_result1.returncode == 0, create_result1.stderr
    assert "Secret created successfully!" in create_result1.stdout

    # Create the second secret
    create_cmd2 = [
        "tracecat",
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
        "tracecat",
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
    title = "__test_workflow"
    description = "This is a test workflow"
    cmd = [
        "tracecat",
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
    title1 = "__test_workflow1"
    description1 = "This is the first test workflow"
    create_cmd1 = [
        "tracecat",
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
    title2 = "__test_workflow2"
    description2 = "This is the second test workflow"
    create_cmd2 = [
        "tracecat",
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
    list_cmd = ["tracecat", "workflow", "list"]
    list_result = subprocess.run(list_cmd, capture_output=True, text=True)
    assert list_result.returncode == 0
    assert "Workflows" in list_result.stdout


def test_create_workflow_with_file():
    expected_title = "Reshape data in a loop"
    expected_description = (
        "Test reshaping data from a list of mappings to a list of  key-value pairs"
    )
    cmd = [
        "tracecat",
        "workflow",
        "create",
        "--file",
        DATA_PATH.joinpath("unit_transform_reshape_arrange_loop.yml").as_posix(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    cleaned_output = result.stdout.replace("\n", " ")
    assert "Created workflow from file" in cleaned_output
    assert expected_title in cleaned_output
    assert expected_description in cleaned_output
    assert "'version': None" in cleaned_output
