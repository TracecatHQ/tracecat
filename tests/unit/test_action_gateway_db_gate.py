from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from fastapi import Depends, FastAPI
from tracecat_registry.sdk import client as sdk_client
from tracecat_registry.sdk.client import TracecatClient

from tests._route_utils import iter_effective_api_routes
from tracecat import config
from tracecat.auth.executor_tokens import mint_executor_token
from tracecat.db.exceptions import TenantBusyError
from tracecat.db.org_gate import OrgSessionGate
from tracecat.executor.action_gateway import db_gate
from tracecat.executor.action_gateway.app import create_app
from tracecat.executor.action_gateway.db_gate import (
    TENANT_BUSY_ERROR_CODE,
    hold_org_db_session_unit,
    tenant_busy_exception_handler,
)

pytestmark = pytest.mark.anyio


def _token(workspace_id: uuid.UUID) -> str:
    return mint_executor_token(
        workspace_id=workspace_id,
        user_id=None,
        wf_id="wf-1",
        wf_exec_id="run-1",
    )


@pytest.fixture
def busy_org_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FastAPI, OrgSessionGate, dict[uuid.UUID, uuid.UUID], list[str]]:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    gate = OrgSessionGate(cap=1, wait_seconds=0.1)
    monkeypatch.setattr(db_gate, "get_org_session_gate", lambda: gate)
    workspace_orgs: dict[uuid.UUID, uuid.UUID] = {}

    async def workspace_org(workspace_id: uuid.UUID) -> uuid.UUID | None:
        return workspace_orgs.get(workspace_id)

    monkeypatch.setattr(db_gate, "_get_workspace_org_id", workspace_org)

    calls: list[str] = []
    app = FastAPI(dependencies=[Depends(hold_org_db_session_unit)])
    app.add_exception_handler(TenantBusyError, tenant_busy_exception_handler)

    async def operation() -> dict[str, bool]:
        calls.append("operation")
        return {"ok": True}

    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route("/internal/tables/rows", operation, methods=["POST"])
    app.add_api_route("/internal/health", health, methods=["GET"])
    return app, gate, workspace_orgs, calls


async def test_org_at_cap_gets_retryable_503_before_the_operation_runs(
    busy_org_setup: tuple[
        FastAPI, OrgSessionGate, dict[uuid.UUID, uuid.UUID], list[str]
    ],
) -> None:
    app, gate, workspace_orgs, calls = busy_org_setup
    busy_workspace, other_workspace = uuid.uuid4(), uuid.uuid4()
    busy_org = uuid.uuid4()
    workspace_orgs[busy_workspace] = busy_org
    workspace_orgs[other_workspace] = uuid.uuid4()

    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()

    async def hold_busy_org() -> None:
        async with gate.hold(busy_org):
            holder_entered.set()
            await release_holder.wait()

    # Another task holds the org's only unit.
    holder = asyncio.create_task(hold_busy_org())
    await holder_entered.wait()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        busy = await client.post(
            "/internal/tables/rows",
            headers={"Authorization": f"Bearer {_token(busy_workspace)}"},
        )
        other = await client.post(
            "/internal/tables/rows",
            headers={"Authorization": f"Bearer {_token(other_workspace)}"},
        )
        health = await client.get(
            "/internal/health",
            headers={"Authorization": f"Bearer {_token(busy_workspace)}"},
        )

    assert busy.status_code == 503
    assert busy.headers["Retry-After"] == "1"
    assert busy.json()["detail"]["code"] == TENANT_BUSY_ERROR_CODE
    assert busy.json()["detail"]["retry_after_seconds"] == 1
    # Only the other org's call ran.
    assert calls == ["operation"]
    assert other.status_code == 200
    assert health.status_code == 200

    release_holder.set()
    await holder


async def test_unit_is_released_after_the_operation(
    busy_org_setup: tuple[
        FastAPI, OrgSessionGate, dict[uuid.UUID, uuid.UUID], list[str]
    ],
) -> None:
    app, gate, workspace_orgs, calls = busy_org_setup
    workspace = uuid.uuid4()
    workspace_orgs[workspace] = uuid.uuid4()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        for _ in range(3):
            response = await client.post(
                "/internal/tables/rows",
                headers={"Authorization": f"Bearer {_token(workspace)}"},
            )
            assert response.status_code == 200

    assert calls == ["operation"] * 3
    assert gate._entries == {}


async def test_requests_without_a_valid_token_pass_to_route_auth(
    busy_org_setup: tuple[
        FastAPI, OrgSessionGate, dict[uuid.UUID, uuid.UUID], list[str]
    ],
) -> None:
    app, gate, _, calls = busy_org_setup

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        missing = await client.post("/internal/tables/rows")
        invalid = await client.post(
            "/internal/tables/rows",
            headers={"Authorization": "Bearer not-a-token"},
        )

    # This test app has no route auth; the gate let both through untouched.
    assert missing.status_code == 200
    assert invalid.status_code == 200
    assert calls == ["operation", "operation"]
    assert gate._entries == {}


class _StatusRecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.statuses: list[int] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        self.statuses.append(response.status_code)
        return response


async def test_sdk_waits_out_a_long_capacity_hold_and_mutates_once(
    busy_org_setup: tuple[
        FastAPI, OrgSessionGate, dict[uuid.UUID, uuid.UUID], list[str]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted work never fails for capacity, and a refusal never runs twice.

    The org stays at its cap for many more refusals than the old SDK limit
    (4 attempts), then frees up; the action completes and its mutation ran
    exactly once.
    """
    app, gate, workspace_orgs, calls = busy_org_setup
    workspace, org = uuid.uuid4(), uuid.uuid4()
    workspace_orgs[workspace] = org
    # Short timings: 20 ms server-side wait, no client sleep between attempts.
    gate.wait_seconds = 0.02
    monkeypatch.setattr(sdk_client, "TENANT_BUSY_MIN_RETRY_AFTER_SECONDS", 0.0)
    monkeypatch.setattr(sdk_client, "TENANT_BUSY_MAX_RETRY_AFTER_SECONDS", 0.0)

    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()

    async def hold_org() -> None:
        async with gate.hold(org):
            holder_entered.set()
            await release_holder.wait()

    holder = asyncio.create_task(hold_org())
    await holder_entered.wait()

    transport = _StatusRecordingTransport(httpx.ASGITransport(app=app))
    client = TracecatClient(
        action_gateway_socket="/tmp/action-gateway.sock",
        token=_token(workspace),
        workspace_id=str(workspace),
    )
    monkeypatch.setattr(
        client,
        "_request_url_and_transport",
        lambda path: (f"http://gateway/internal{path}", transport),
    )
    action = asyncio.create_task(client.post("/tables/rows", json={"row": 1}))

    refusals_past_old_limit = 10
    async with asyncio.timeout(10):
        while transport.statuses.count(503) < refusals_past_old_limit:
            assert not action.done(), "the action gave up while the org was busy"
            await asyncio.sleep(0.01)
    assert calls == []

    release_holder.set()
    await holder
    async with asyncio.timeout(10):
        result = await action

    assert result == {"ok": True}
    assert calls == ["operation"]
    assert transport.statuses.count(503) >= refusals_past_old_limit
    assert transport.statuses[-1] == 200


def test_no_cap_configured_means_no_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__DB_ORG_SESSION_CAP", None)
    assert db_gate.get_org_session_gate() is None


def test_all_action_gateway_routes_hold_an_org_db_session_unit() -> None:
    app = create_app()

    assert app.exception_handlers[TenantBusyError] is tenant_busy_exception_handler
    for route in iter_effective_api_routes(app):
        dependency_calls = [
            dependency.call for dependency in route.dependant.dependencies
        ]
        assert hold_org_db_session_unit in dependency_calls, route.path
