"""Test suite helpers."""

from __future__ import annotations

import os

import httpx
from slugify import slugify

from tracecat.identifiers.workflow import EXEC_ID_PREFIX, WorkflowUUID


def user_client() -> httpx.AsyncClient:
    """Returns an asynchronous httpx client with the user's JWT token."""
    return httpx.AsyncClient(
        headers={"Authorization": "Bearer super-secret-jwt-token"},
        base_url=os.environ.get("TRACECAT__PUBLIC_API_URL", "http://localhost:8000"),
    )


TEST_WF_ID = WorkflowUUID(int=0)


def generate_test_exec_id(name: str) -> str:
    return TEST_WF_ID.short() + f"/{EXEC_ID_PREFIX}" + slugify(name, separator="_")
