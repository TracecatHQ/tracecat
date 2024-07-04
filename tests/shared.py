"""Test suite helpers."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx

from tracecat.auth.clients import AuthenticatedAPIClient
from tracecat.db.schemas import Secret
from tracecat.types.auth import Role


def user_client() -> httpx.AsyncClient:
    """Returns an asynchronous httpx client with the user's JWT token."""
    return httpx.AsyncClient(
        headers={"Authorization": "Bearer super-secret-jwt-token"},
        base_url=os.environ.get("TRACECAT__PUBLIC_API_URL", "http://localhost:8000"),
    )


async def create_workflow(title: str | None = None, description: str | None = None):
    async with user_client() as client:
        # Get the webhook url
        params = {}
        if title:
            params["title"] = title
        if description:
            params["description"] = description
        res = await client.post("/workflows", json=params)
        res.raise_for_status()
        return res.json()


async def activate_workflow(workflow_id: str, with_webhook: bool = False):
    async with user_client() as client:
        res = await client.patch(f"/workflows/{workflow_id}", json={"status": "online"})
        res.raise_for_status()
        if with_webhook:
            res = await client.patch(
                f"/workflows/{workflow_id}/webhook", json={"status": "online"}
            )
            res.raise_for_status()


async def batch_get_secrets(role: Role, secret_names: list[str]) -> list[Secret]:
    """Retrieve secrets from the secrets API."""

    async with AuthenticatedAPIClient(role=role) as client:
        # NOTE(perf): This is not really batched - room for improvement
        secret_responses = await asyncio.gather(
            *[client.get(f"/secrets/{secret_name}") for secret_name in secret_names]
        )
        return [
            Secret.model_validate_json(secret_bytes.content)
            for secret_bytes in secret_responses
        ]


def format_secrets_as_json(secrets: list[Secret]) -> dict[str, str]:
    """Format secrets as a dict."""
    secret_dict = {}
    for secret in secrets:
        secret_dict[secret.name] = {
            kv.key: kv.value.get_secret_value() for kv in secret.encrypted_keys
        }
    return secret_dict


async def commit_workflow(yaml_path: Path, workflow_id: str):
    kwargs = {}
    if yaml_path:
        with yaml_path.open() as f:
            yaml_content = f.read()
        kwargs["files"] = {
            "yaml_file": (yaml_path.name, yaml_content, "application/yaml")
        }

    async with user_client() as client:
        res = await client.post(f"/workflows/{workflow_id}/commit", **kwargs)
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
