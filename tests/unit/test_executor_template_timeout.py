"""Action timeout budgets.

A template is one action whose steps share its unclamped timeout, each UDF call
is capped at the deployment maximum, and run_python keeps the action timeout.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from tracecat.auth.types import Role
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import (
    ActionRetryPolicy,
    ActionStatement,
    RunActionInput,
    RunContext,
)
from tracecat.exceptions import ExecutionError
from tracecat.executor import service
from tracecat.executor.error_policy import classify_execute_action_error
from tracecat.executor.minimal_runner import ActionTimeoutError
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ExecutorResultFailure,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import RetryDisposition

pytestmark = pytest.mark.anyio


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        organization_id=UUID(int=1),
        workspace_id=UUID(int=2),
        service_id="tracecat-executor",
    )


@pytest.fixture(autouse=True)
def deployment_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.config, "TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 300.0)


def _template_input(timeout: int) -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(
            ref="a",
            action="testing.template",
            args={},
            retry_policy=ActionRetryPolicy(timeout=timeout),
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=generate_exec_id(wf_id),
            wf_run_id=UUID(int=3, version=4),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(origins={}, actions={}),
    )


def _use_template(
    monkeypatch: pytest.MonkeyPatch, role: Role, input: RunActionInput, steps: int
) -> None:
    template_impl = ActionImplementation(
        type="template",
        action_name="testing.template",
        template_definition={
            "name": "template",
            "namespace": "testing",
            "title": "template",
            "description": "Synthetic multi-step template",
            "display_group": "Testing",
            "expects": {},
            "steps": [
                {"ref": f"s{i}", "action": "testing.step", "args": {}}
                for i in range(steps)
            ],
            "returns": "done",
        },
    )
    _use_action(monkeypatch, role, input, template_impl)


def _use_action(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    input: RunActionInput,
    action_impl: ActionImplementation,
) -> None:
    step_impl = ActionImplementation(type="udf", action_name="testing.step")
    resolved = ResolvedContext(
        secrets={},
        variables={},
        action_impl=action_impl,
        evaluated_args={},
        workspace_id=str(role.workspace_id),
        workflow_id=str(input.run_context.wf_id),
        run_id=str(input.run_context.wf_run_id),
        executor_token="synthetic-token",
    )
    monkeypatch.setattr(service.registry_resolver, "prefetch_lock", AsyncMock())
    monkeypatch.setattr(
        service.registry_resolver, "resolve_action", AsyncMock(return_value=step_impl)
    )
    monkeypatch.setattr(
        service, "_mint_action_executor_token", Mock(return_value="token")
    )
    monkeypatch.setattr(
        service,
        "prepare_resolved_context",
        AsyncMock(
            return_value=service.PreparedContext(
                resolved_context=resolved, mask_values=set(), provenance={}
            )
        ),
    )


def _backend(
    timeouts: list[float],
    results: list[Any] | None = None,
    step_seconds: float = 0.0,
) -> Mock:
    async def execute(**kwargs: Any) -> Any:
        timeouts.append(kwargs["timeout"])
        await asyncio.sleep(step_seconds)
        if results:
            return results.pop(0)
        return ExecutorResultSuccess(result="ok")

    return Mock(execute=AsyncMock(side_effect=execute))


async def test_template_step_running_at_the_timeout_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    """The step running at the template's timeout is cancelled and later steps never start."""
    action_input = _template_input(timeout=1)
    _use_template(monkeypatch, role, action_input, steps=3)
    timeouts: list[float] = []
    cancelled = asyncio.Event()

    async def execute(**kwargs: Any) -> Any:
        timeouts.append(kwargs["timeout"])
        if len(timeouts) == 1:
            return ExecutorResultSuccess(result="ok")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(ExecutionError) as caught:
        await service.invoke_once(
            Mock(execute=AsyncMock(side_effect=execute)),
            action_input,
            service.DispatchActionContext(role),
        )

    assert timeouts == [1.0, 1.0]
    assert cancelled.is_set()
    assert caught.value.info.type == ActionTimeoutError.__name__
    assert caught.value.info.message == "Action execution timed out after 1s"
    classification = classify_execute_action_error(
        caught.value, action_name=action_input.task.action
    )
    assert classification.retry_disposition is RetryDisposition.RETRYABLE


async def test_template_timeout_error_from_platform_work_is_not_the_action_timeout(
    monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    """A TimeoutError raised inside the template before its deadline keeps its type."""
    action_input = _template_input(timeout=300)
    _use_template(monkeypatch, role, action_input, steps=1)
    monkeypatch.setattr(
        service.registry_resolver,
        "resolve_action",
        AsyncMock(side_effect=TimeoutError("registry lookup timed out")),
    )

    with pytest.raises(ExecutionError) as caught:
        await service.invoke_once(
            _backend([]), action_input, service.DispatchActionContext(role)
        )

    assert caught.value.info.type == TimeoutError.__name__


async def test_template_steps_are_capped_but_share_the_unclamped_timeout(
    monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    """Each step call is capped, but the template runs up to its own timeout."""
    monkeypatch.setattr(service.config, "TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 0.1)
    action_input = _template_input(timeout=1)
    _use_template(monkeypatch, role, action_input, steps=3)
    timeouts: list[float] = []

    result = await service.invoke_once(
        _backend(timeouts, step_seconds=0.15),
        action_input,
        service.DispatchActionContext(role),
    )

    assert result == "done"
    assert timeouts == [0.1, 0.1, 0.1]


async def test_capped_template_step_timeout_names_the_cap(
    monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    """A step stopped by the deployment cap says the cap ended it."""
    action_input = _template_input(timeout=900)
    _use_template(monkeypatch, role, action_input, steps=2)
    step_timeout = ExecutorResultFailure(
        error=ExecutorActionErrorInfo(
            action_name="testing.step",
            type=ActionTimeoutError.__name__,
            message="Action execution timed out after 300s",
            filename="minimal_runner.py",
            function="_await_with_timeout",
        )
    )
    timeouts: list[float] = []

    with pytest.raises(ExecutionError) as caught:
        await service.invoke_once(
            _backend(timeouts, [step_timeout]),
            action_input,
            service.DispatchActionContext(role),
        )

    assert timeouts == [300.0]
    assert caught.value.info.message == (
        "Action execution timed out after 300s, this deployment's maximum action "
        "timeout (TRACECAT__EXECUTOR_CLIENT_TIMEOUT). The action's own timeout "
        "is 900s."
    )


async def test_run_python_keeps_the_unclamped_action_timeout(
    monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    """run_python gets the action's own timeout, not the deployment cap."""
    action_input = _template_input(timeout=900)
    _use_action(
        monkeypatch,
        role,
        action_input,
        ActionImplementation(type="udf", action_name=PlatformAction.RUN_PYTHON),
    )
    timeouts: list[float] = []

    await service.invoke_once(
        _backend(timeouts),
        action_input,
        service.DispatchActionContext(role),
    )

    assert timeouts == [900.0]
