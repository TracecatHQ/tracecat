"""Test CLI commands."""

import io
import json
import logging
import re
import uuid
from pathlib import Path

import pytest
import rich
from rich.console import Console

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).parent.parent.joinpath("data/workflows")


@pytest.fixture(scope="session")
def capture_cli(monkeysession):
    # Create a StringIO object to capture the output
    buffer = io.StringIO()

    # Create a Console object with the StringIO as the file
    console = Console(file=buffer, width=300)

    # Use monkeypatch to replace rich.print with console.print
    monkeysession.setattr(rich, "print", console.print)

    def get_and_reset() -> str:
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    yield get_and_reset

    # Close the StringIO object
    buffer.close()


def test_whoami(capture_cli):
    from cli.tracecat_cli.auth import whoami

    whoami(as_json=True)
    captured_output = capture_cli()
    actual = json.loads(captured_output)
    expected_keys = {
        "email": "admin@domain.com",
        "is_active": True,
        "is_superuser": True,
        "is_verified": True,
        "role": "admin",
        "first_name": "Admin",
        "last_name": "User",
        "settings": {},
    }
    for key in expected_keys:
        assert actual[key] == expected_keys[key]
    assert isinstance(uuid.UUID(actual["id"]), uuid.UUID)


def test_manage_workspaces(capture_cli, test_config_manager):
    # XXX: Import monkeypatched functions here
    from cli.tracecat_cli.workspace import current as current_workspace
    from cli.tracecat_cli.workspace import list_workspaces

    # Assert current workspace
    current_workspace()
    captured_output = capture_cli()
    curr_workspace = json.loads(captured_output)

    exptected_workspace = test_config_manager.get_workspace()
    assert curr_workspace["id"] == exptected_workspace["id"]
    assert curr_workspace["name"] == exptected_workspace["name"]

    # Assert that current workspace is in the list of workspaces
    list_workspaces(as_json=True)
    captured_output = capture_cli()
    all_workspaces = json.loads(captured_output)
    # This might fail depending on the number of workspaces in the database
    assert len(all_workspaces) > 0

    # Find the workspace with matching properties
    matching_ws = next(
        ws
        for ws in all_workspaces
        if ws["id"] == curr_workspace["id"] and ws["name"] == curr_workspace["name"]
    )
    assert matching_ws is not None


def test_manage_secrets(capture_cli, monkeypatch):
    """Test creating, listing, and deleting secrets."""
    # XXX: Import monkeypatched functions here
    from cli.tracecat_cli.secret import create as create_secret
    from cli.tracecat_cli.secret import delete as delete_secret
    from cli.tracecat_cli.secret import list_secrets

    # Assert no secrets
    list_secrets(as_json=True)
    captured_output = capture_cli()
    initial = json.loads(captured_output)
    assert initial == []

    # Create 2 secrets
    secret_name1 = "__test_secret1"
    secret_name2 = "__test_secret2"

    create_secret(secret_name=secret_name1, keyvalues=["KEY1=VAL1"])
    assert "Secret created successfully!" in capture_cli()

    create_secret(secret_name=secret_name2, keyvalues=["KEY2=VAL2"])
    assert "Secret created successfully!" in capture_cli()

    # Assert there are two secrets
    list_secrets(as_json=True)
    captured_output = capture_cli()
    actual = json.loads(captured_output)
    expected = [
        {
            "type": "custom",
            "name": secret_name1,
            "description": None,
            "keys": ["KEY1"],
        },
        {
            "type": "custom",
            "name": secret_name2,
            "description": None,
            "keys": ["KEY2"],
        },
    ]
    for i, secret in enumerate(actual):
        assert re.match(r"^secret-[0-9a-f]{32}$", secret["id"])
        assert secret["type"] == expected[i]["type"]
        assert secret["name"] == expected[i]["name"]
        assert secret["description"] == expected[i]["description"]
        assert secret["keys"] == expected[i]["keys"]

    # Delete one secret
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    delete_secret(secret_names=[secret_name1])
    assert "Secret deleted successfully!" in capture_cli()

    # Assert there is one secret (secret_name2)
    list_secrets(as_json=True)
    captured_output = capture_cli()
    actual = json.loads(captured_output)
    assert len(actual) == 1
    expected = {
        "type": "custom",
        "name": secret_name2,
        "description": None,
        "keys": ["KEY2"],
    }

    assert re.match(r"^secret-[0-9a-f]{32}$", actual[0]["id"])
    assert expected["type"] == actual[0]["type"]
    assert expected["name"] == actual[0]["name"]
    assert expected["description"] == actual[0]["description"]
    assert expected["keys"] == actual[0]["keys"]

    # Delete the last secret
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    delete_secret(secret_names=[secret_name2])
    assert "Secret deleted successfully!" in capture_cli()

    # Assert no secrets
    list_secrets(as_json=True)
    captured_output = capture_cli()
    actual = json.loads(captured_output)
    assert actual == []


def test_manage_workflows(capture_cli):
    from cli.tracecat_cli.workflow import _create_workflow, list_workflows

    title1 = "__test_workflow1"
    description1 = "This is the first test workflow"
    title2 = "__test_workflow2"
    description2 = "This is the second test workflow"

    # Call the private function as typer passes a typer.Option in optional fields
    wf1 = _create_workflow(title=title1, description=description1)
    captured_output = capture_cli()
    assert "Created workflow" in captured_output
    assert wf1["title"] == title1
    assert wf1["description"] == description1

    wf2 = _create_workflow(title=title2, description=description2)
    captured_output = capture_cli()
    assert "Created workflow" in captured_output
    assert wf2["title"] == title2
    assert wf2["description"] == description2

    list_workflows(as_json=True)
    captured_output = capture_cli()
    workflows = json.loads(captured_output)

    # Match first
    first_workflow = next(
        wf
        for wf in workflows
        if wf["title"] == title1
        and wf["description"] == description1
        and wf1["id"] == wf["id"]
    )
    assert first_workflow is not None

    # Match second
    second_workflow = next(
        wf
        for wf in workflows
        if wf["title"] == title2
        and wf["description"] == description2
        and wf2["id"] == wf["id"]
    )
    assert second_workflow is not None


def test_create_workflow_with_file(capture_cli):
    from cli.tracecat_cli.workflow import _create_workflow, list_workflows

    expected_title = "Reshape data in a loop"
    expected_description = (
        "Test reshaping data from a list of mappings to a list of key-value pairs"
    )

    file = DATA_PATH.joinpath("unit_transform_reshape_arrange_loop.yml")
    wf = _create_workflow(file=file)
    captured_output = capture_cli()

    assert "Created workflow from file" in captured_output

    assert wf["title"] == expected_title
    assert wf["description"] == expected_description
    assert wf["version"] is None

    list_workflows(as_json=True)
    captured_output = capture_cli()
    workflows = json.loads(captured_output.replace("\n", ""))

    # Match workflow
    matching_workflow = next(
        wf
        for wf in workflows
        if wf["title"] == expected_title
        and wf["description"] == expected_description
        and wf["id"] == wf["id"]
    )
    assert matching_workflow is not None
