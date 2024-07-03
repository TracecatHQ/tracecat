"""Integration test helpers."""

import os

import httpx


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


def user_client() -> httpx.AsyncClient:
    """Returns an asynchronous httpx client with the user's JWT token."""
    return httpx.AsyncClient(
        headers={"Authorization": "Bearer super-secret-jwt-token"},
        base_url=os.environ.get("TRACECAT__PUBLIC_API_URL", "http://localhost:8000"),
    )
