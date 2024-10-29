"""Test suite helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from slugify import slugify

from tracecat.db.schemas import BaseSecret
from tracecat.dsl.validation import validate_trigger_inputs_activity
from tracecat.identifiers.resource import ResourcePrefix
from tracecat.workflow.management.definitions import get_workflow_definition_activity


def write_cookies(cookies: httpx.Cookies, cookies_path: Path) -> None:
    """Write cookies to file."""
    cookies_dict = dict(cookies)

    # Overwrite the cookies file
    with cookies_path.open(mode="w") as f:
        json.dump(cookies_dict, f)


def read_cookies(cookies_path: Path) -> httpx.Cookies:
    """Read cookies from file."""
    try:
        with cookies_path.open() as f:
            cookies_dict = json.load(f)
        return httpx.Cookies(cookies_dict)
    except (FileNotFoundError, json.JSONDecodeError):
        return httpx.Cookies()


def delete_cookies(cookies_path: Path) -> None:
    """Delete cookies file."""
    cookies_path.unlink(missing_ok=True)


def user_client() -> httpx.AsyncClient:
    """Returns an asynchronous httpx client with the user's JWT token."""
    return httpx.AsyncClient(
        headers={"Authorization": "Bearer super-secret-jwt-token"},
        base_url=os.environ.get("TRACECAT__PUBLIC_API_URL", "http://localhost:8000"),
    )


async def create_workflow(
    title: str | None = None, description: str | None = None, file: Path | None = None
):
    # Passing a file supercedes creating a blank workflow with title and description
    if file:
        with file.open() as f:
            yaml_content = f.read()
        async with user_client() as client:
            res = await client.post(
                "/workflows",
                files={"file": (file.name, yaml_content, "application/yaml")},
            )
    else:
        params = {}
        if title:
            params["title"] = title
        if description:
            params["description"] = description
        async with user_client() as client:
            # Get the webhook url
            res = await client.post("/workflows", data=params)

    return handle_response(res)


async def activate_workflow(workflow_id: str, with_webhook: bool = False):
    async with user_client() as client:
        res = await client.patch(f"/workflows/{workflow_id}", json={"status": "online"})
        res.raise_for_status()
        if with_webhook:
            res = await client.patch(
                f"/workflows/{workflow_id}/webhook", json={"status": "online"}
            )
            res.raise_for_status()


def format_secrets_as_json(secrets: list[BaseSecret]) -> dict[str, str]:
    """Format secrets as a dict."""
    secret_dict = {}
    for secret in secrets:
        secret_dict[secret.name] = {
            kv.key: kv.value.get_secret_value() for kv in secret.encrypted_keys
        }
    return secret_dict


async def commit_workflow(workflow_id: str):
    """Create a workflow definition from a workflow."""
    async with user_client() as client:
        res = await client.post(f"/workflows/{workflow_id}/commit")
        res.raise_for_status()
        return res.json()


def handle_response(res: httpx.Response):
    """Handle API responses.

    1. Checks for specific status codes and raises exceptions.
    2. Raise for status
    3. Try to decode JSON response
    4. If 3 fails, return the response text.
    """
    if res.status_code == 422:
        # Unprocessable entity
        raise ValueError(res.json())
    if res.status_code == 204:
        # No content
        return None
    res.raise_for_status()
    try:
        return res.json()
    except json.JSONDecodeError:
        return res.text


TEST_WF_ID = "wf-00000000000000000000000000000000"


def generate_test_exec_id(name: str) -> str:
    return (
        TEST_WF_ID
        + f":{ResourcePrefix.WORKFLOW_EXECUTION}-"
        + slugify(name, separator="_")
    )


DSL_UTILITIES = [get_workflow_definition_activity, validate_trigger_inputs_activity]
