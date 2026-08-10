from __future__ import annotations

import types
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import pytest
from pydantic import Field

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.executor import untrusted_runner
from tracecat.executor.schemas import ActionImplementation, ResolvedContext
from tracecat.executor.secret_preprocessors import SecretEnvProjection
from tracecat.identifiers.workflow import ExecutionUUID, WorkflowUUID
from tracecat.registry.lock.types import RegistryLock


def _import_test_module(test_module: Any):
    def import_test_module(path: str, *args: object, **kwargs: object) -> Any:
        assert path == "test_module"
        return test_module

    return import_test_module


@pytest.mark.regression
@pytest.mark.anyio
async def test_run_udf_strips_tracecat_metadata_before_applying_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_module: Any = types.ModuleType("test_module")
    received: dict[str, Any] = {}

    def create_ticket(
        summary: Annotated[str, Field(..., description="Required summary")],
        client_id: Annotated[
            int | None, Field(default=None, description="Optional client")
        ],
        **kwargs: Any,
    ) -> dict[str, Any]:
        received.update({"summary": summary, "client_id": client_id, **kwargs})
        return received

    test_module.create_ticket = create_ticket

    monkeypatch.setattr(
        untrusted_runner.importlib,
        "import_module",
        _import_test_module(test_module),
    )

    result = await untrusted_runner._run_udf(
        "test_module",
        "create_ticket",
        {"summary": "Ticket without client_id", "__tracecat": {"meta": True}},
    )

    assert result == {"summary": "Ticket without client_id", "client_id": None}
    assert received == {"summary": "Ticket without client_id", "client_id": None}


@pytest.mark.regression
@pytest.mark.anyio
async def test_run_action_untrusted_applies_annotated_field_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_module: Any = types.ModuleType("test_module")

    def create_ticket(
        summary: Annotated[str, Field(..., description="Required summary")],
        client_id: Annotated[
            int | None, Field(default=None, description="Optional client")
        ],
        priority: Annotated[int, Field(default=3, description="Priority")],
    ) -> dict[str, str | int | None]:
        return {"summary": summary, "client_id": client_id, "priority": priority}

    test_module.create_ticket = create_ticket
    monkeypatch.setattr(
        untrusted_runner.importlib,
        "import_module",
        _import_test_module(test_module),
    )

    wf_id = WorkflowUUID.new_uuid4()
    exec_id = ExecutionUUID.new_uuid4()
    role = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )
    input = RunActionInput(
        task=ActionStatement(
            action="testing.create_ticket",
            args={"summary": "Ticket without client_id"},
            ref="create_ticket",
        ),
        exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/{exec_id.short()}",
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test-version"},
            actions={"testing.create_ticket": "tracecat_registry"},
        ),
    )
    resolved_context = ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(
            type="udf",
            action_name="testing.create_ticket",
            module="test_module",
            name="create_ticket",
            origin="tracecat_registry",
        ),
        evaluated_args={"summary": "Ticket without client_id"},
        workspace_id=str(role.workspace_id),
        workflow_id=str(wf_id),
        run_id=str(input.run_context.wf_run_id),
        executor_token="test-token",
        logical_time=input.run_context.logical_time,
        secret_projection=SecretEnvProjection(env={}, mask_values=set()),
    )

    result = await untrusted_runner.run_action_untrusted(
        input=input,
        role=role,
        resolved_context=resolved_context,
    )

    assert result == {
        "summary": "Ticket without client_id",
        "client_id": None,
        "priority": 3,
    }
