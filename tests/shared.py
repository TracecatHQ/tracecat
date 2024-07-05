"""Test suite helpers."""

from __future__ import annotations

import asyncio
import os

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
