"""Count main-pool connections held at once while the gateway starts a workflow.

``TRACECAT__DB_ORG_SESSION_CAP`` budgets connections per gateway operation, so
``POST /internal/workflows/run`` must hold only its request session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event

from tracecat import config
from tracecat.auth.executor_tokens import mint_executor_token
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_engine
from tracecat.dsl.common import DSLInput
from tracecat.executor.action_gateway.app import create_app
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock
from tracecat.validation import service as validation_service
from tracecat.variables.schemas import VariableCreate
from tracecat.variables.service import VariablesService
from tracecat.workflow.executions import service as exec_service_module
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService

pytestmark = pytest.mark.usefixtures("db")

_VARIABLE_NAME = "run_sessions_var"
_REGISTRY_LOCK = RegistryLock(
    origins={"tracecat_registry": "test-version"},
    actions={"core.transform.reshape": "tracecat_registry"},
)


@dataclass(slots=True)
class _CheckoutCounter:
    held: int = 0
    peak: int = 0
    held_at_start: list[int] = field(default_factory=list)


@contextmanager
def _count_main_pool_checkouts() -> Generator[_CheckoutCounter]:
    """Track how many main-pool connections are checked out at once."""
    counter = _CheckoutCounter()
    pool = get_async_engine().sync_engine.pool

    def on_checkout(*_: Any) -> None:
        counter.held += 1
        counter.peak = max(counter.peak, counter.held)

    def on_checkin(*_: Any) -> None:
        counter.held -= 1

    event.listen(pool, "checkout", on_checkout)
    event.listen(pool, "checkin", on_checkin)
    try:
        yield counter
    finally:
        event.remove(pool, "checkout", on_checkout)
        event.remove(pool, "checkin", on_checkin)


def _dsl() -> dict[str, Any]:
    """A one-action workflow whose draft run validates a variable expression."""
    return {
        "title": "run sessions",
        "description": "Counts gateway sessions",
        "entrypoint": {"ref": "start", "expects": {}},
        "actions": [
            {
                "ref": "start",
                "action": "core.transform.reshape",
                "args": {"value": f"${{{{ VARS.{_VARIABLE_NAME}.key }}}}"},
            }
        ],
    }


@pytest.fixture
async def workflow_id(monkeypatch: pytest.MonkeyPatch, svc_role: Role) -> WorkflowUUID:
    """A workflow with a draft graph and a published definition."""
    # The test DB has no builtin registry to lock against.
    monkeypatch.setattr(
        RegistryLockService,
        "resolve_lock_with_bindings",
        AsyncMock(return_value=_REGISTRY_LOCK),
    )
    async with VariablesService.with_session(role=svc_role) as var_svc:
        await var_svc.create_variable(
            VariableCreate(name=_VARIABLE_NAME, values={"key": "value"})
        )
    async with WorkflowsManagementService.with_session(role=svc_role) as mgmt:
        created = await mgmt.create_workflow_from_dsl(
            _dsl(), skip_secret_validation=True
        )
        assert created.workflow is not None, created.errors
        wf_id = WorkflowUUID.new(created.workflow.id)
    async with WorkflowDefinitionsService.with_session(role=svc_role) as defn_svc:
        await defn_svc.create_workflow_definition(
            wf_id, DSLInput.model_validate(_dsl()), registry_lock=_REGISTRY_LOCK
        )
    return wf_id


@pytest.fixture
def temporal_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[dict[str, Any]]]:
    """Replace the Temporal client; record each start's call."""
    starts: list[dict[str, Any]] = []

    async def start_workflow(*_: Any, **kwargs: Any) -> None:
        starts.append(kwargs)

    client = AsyncMock()
    client.start_workflow = start_workflow
    monkeypatch.setattr(
        exec_service_module, "get_temporal_client", AsyncMock(return_value=client)
    )
    yield starts


@pytest.fixture
async def gateway(
    monkeypatch: pytest.MonkeyPatch, svc_role: Role
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    assert svc_role.workspace_id is not None
    token = mint_executor_token(
        workspace_id=svc_role.workspace_id,
        user_id=svc_role.user_id,
        wf_id="wf-gateway-sessions",
        wf_exec_id="wf-gateway-sessions/exec-1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://gateway",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


@pytest.mark.anyio
@pytest.mark.parametrize("use_draft", [False, True], ids=["published", "draft"])
async def test_run_holds_one_main_pool_connection(
    monkeypatch: pytest.MonkeyPatch,
    gateway: httpx.AsyncClient,
    workflow_id: WorkflowUUID,
    temporal_starts: list[dict[str, Any]],
    use_draft: bool,
) -> None:
    # The audit-settings cache is warm from the fixture's audited writes; a cold
    # per-process cache briefly takes a second connection (see
    # TRACECAT__DB_ORG_SESSION_CAP). Start pacing is off outside multi-tenant.
    # The test DB has no registry index; this tier only reads the session it gets.
    monkeypatch.setattr(
        validation_service, "validate_dsl_actions", AsyncMock(return_value=[])
    )

    with _count_main_pool_checkouts() as counter:
        original_start = exec_service_module.WorkflowExecutionsService._start_workflow

        async def start_and_record(*args: Any, **kwargs: Any) -> None:
            counter.held_at_start.append(counter.held)
            await original_start(*args, **kwargs)

        monkeypatch.setattr(
            exec_service_module.WorkflowExecutionsService,
            "_start_workflow",
            start_and_record,
        )
        response = await gateway.post(
            "/internal/workflows/run",
            json={"workflow_id": workflow_id.short(), "use_draft": use_draft},
        )

    assert response.status_code == 201, response.text
    assert len(temporal_starts) == 1
    # Only the request session is ever checked out, and it is still open
    # when the start reaches Temporal.
    assert (counter.peak, counter.held_at_start) == (1, [1])
    assert counter.held == 0
