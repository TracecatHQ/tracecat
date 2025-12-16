from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
from tracecat_registry._internal import secrets_context
from tracecat_registry.context import RegistryContext, clear_context, set_context

TEST_WORKSPACE_ID = str(uuid.UUID("11111111-1111-4111-8111-111111111111"))
TEST_WORKFLOW_ID = str(uuid.UUID("22222222-2222-4222-8222-222222222222"))
TEST_RUN_ID = str(uuid.UUID("33333333-3333-4333-8333-333333333333"))


@pytest.fixture(autouse=True, scope="function")
async def registry_context() -> AsyncGenerator[RegistryContext, None]:
    """Provide a registry execution context for registry UDF tests."""

    ctx = RegistryContext(
        workspace_id=TEST_WORKSPACE_ID,
        workflow_id=TEST_WORKFLOW_ID,
        run_id=TEST_RUN_ID,
        api_url="http://test-api.local",
        token="test-executor-token",
    )
    set_context(ctx)

    # Set up secrets context with infrastructure config from environment
    secrets = {}
    if redis_url := os.environ.get("REDIS_URL"):
        secrets["REDIS_URL"] = redis_url

    with secrets_context.env_sandbox(secrets):
        try:
            yield ctx
        finally:
            clear_context()
