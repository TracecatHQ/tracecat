"""Tests for the admin API client."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
from httpx import Response
from tracecat_admin import client as client_module
from tracecat_admin.client import AdminClient

from .conftest import API_URL

_LIMIT_OVERRIDES = (
    "max_concurrent_workflows",
    "max_action_executions_per_workflow",
    "max_concurrent_actions",
    "max_concurrent_tool_calls",
    "max_concurrent_agent_turns",
    "api_rate_limit",
    "api_burst_capacity",
)


@pytest.mark.anyio
@pytest.mark.usefixtures("mock_env")
@respx.mock
async def test_update_org_tier_clear_overrides_clears_every_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "load_cookies", httpx.Cookies)
    org_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    route = respx.patch(f"{API_URL}/admin/tiers/organizations/{org_id}").mock(
        return_value=Response(
            200,
            json={
                "id": str(uuid.uuid4()),
                "organization_id": org_id,
                "tier_id": str(uuid.uuid4()),
                **dict.fromkeys(_LIMIT_OVERRIDES),
                "entitlement_overrides": None,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
                "expires_at": None,
                "created_at": now,
                "updated_at": now,
            },
        )
    )

    async with AdminClient() as admin:
        await admin.update_org_tier(org_id, clear_overrides=True)

    sent = json.loads(route.calls.last.request.content)
    for field in _LIMIT_OVERRIDES:
        assert field in sent
        assert sent[field] is None
    assert sent["entitlement_overrides"] is None
