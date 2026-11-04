"""DSL agent actions run on their session's agent backend.

When the prepare activity hands back a turn on an installed backend, the DSL
workflow starts that backend's turn workflow as a child by type name. The
turn's approvals go through the backend's own control path, and its
structured output becomes the action's result. Otherwise the built-in durable
agent workflow runs, as before.
"""

import asyncio
import uuid
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.common import Priority, RetryPolicy
from temporalio.service import RPCError
from temporalio.worker import Worker

from tests.shared import to_data
from tests.temporal.installed_backend_turn_harness import (
    INSTALLED_TURN_WORKFLOW,
    BuiltinAgentWorkflow,
    InstalledBackend,
    InstalledBackendTurnWorkflow,
)
from tracecat import config
from tracecat.agent.backends.schemas import (
    ChildAgentTurn,
    WorkflowApprovalSubmission,
)
from tracecat.agent.worker import get_activities as get_agent_worker_activities
from tracecat.agent.worker import new_sandbox_runner as new_agent_sandbox_runner
from tracecat.agent.workflow_turns.activities import (
    prepare_workflow_agent_turn_activity,
)
from tracecat.agent.workflow_turns.schemas import (
    PrepareWorkflowAgentTurnInput,
    PrepareWorkflowAgentTurnResult,
)
from tracecat.auth.types import Role
from tracecat.dsl.common import RETRY_POLICIES, DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.worker import get_activities as get_dsl_worker_activities
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures("registry_version_with_manifest"),
]


def _agent_action_dsl(**args: Any) -> DSLInput:
    return DSLInput(
        title="Workflow agent action",
        description="Verify where ai.agent runs",
        entrypoint=DSLEntrypoint(ref="agent"),
        actions=[
            ActionStatement(
                ref="agent",
                action="ai.agent",
                args={
                    "user_prompt": "Investigate this alert",
                    "model_name": "gpt-4o-mini",
                    "model_provider": "openai",
                    **args,
                },
            )
        ],
        returns="${{ ACTIONS.agent.result }}",
    )


def _prepare_on_installed_backend(
    *, run_id: uuid.UUID, task_queue: str
) -> Callable[..., Any]:
    """Prepare every agent action's turn on the installed backend."""

    @activity.defn(name="prepare_workflow_agent_turn_activity")
    async def prepare(
        input: PrepareWorkflowAgentTurnInput,
    ) -> PrepareWorkflowAgentTurnResult:
        agent_args = input.args.agent_args
        assert agent_args.config is not None
        return PrepareWorkflowAgentTurnResult(
            turn=ChildAgentTurn(
                backend_id="installed",
                session_id=agent_args.session_id,
                run_id=run_id,
                workflow_type=INSTALLED_TURN_WORKFLOW,
                workflow_id=f"agent/{run_id}",
                task_queue=task_queue,
                priority=Priority(),
                retry_policy=RetryPolicy(maximum_attempts=1),
                args={
                    "prompt": agent_args.user_prompt,
                    "output_type": agent_args.config.output_type,
                },
            )
        )

    return prepare


# The agent actions resolve through this lock, so no registry sync is needed.
_REGISTRY_LOCK = RegistryLock(
    origins={"tracecat_registry": "test-version"},
    actions={"ai.agent": "tracecat_registry"},
)


async def _wait_until_started(handle: WorkflowHandle[Any, Any]) -> None:
    for _ in range(100):
        try:
            await handle.describe()
            return
        except RPCError:
            await asyncio.sleep(0.1)
    raise AssertionError("The backend turn never started")


def _with_activity(
    activities: Sequence[Callable[..., Any]], replacement: Callable[..., Any]
) -> list[Callable[..., Any]]:
    kept = [a for a in activities if a is not prepare_workflow_agent_turn_activity]
    assert len(kept) == len(activities) - 1
    return [*kept, replacement]


@pytest.mark.anyio
async def test_agent_action_runs_on_the_sessions_installed_backend(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[..., Worker],
    threadpool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()
    backend = InstalledBackend()
    agent_queue = config.TRACECAT__AGENT_QUEUE
    monkeypatch.setattr(
        "tracecat.agent.workflow_turns.activities.find_agent_backend",
        lambda _backend_id: backend,
    )
    monkeypatch.setattr(
        "tracecat.agent.backends.base.get_temporal_client",
        AsyncMock(return_value=temporal_client),
    )
    output_type = {"type": "object", "properties": {"verdict": {"type": "string"}}}
    dsl = _agent_action_dsl(
        output_type=output_type,
        actions=["core.http_request"],
        tool_approvals={"core.http_request": True},
    )
    wf_id = WorkflowUUID.new_uuid4()
    agent_worker = Worker(
        client=temporal_client,
        task_queue=agent_queue,
        activities=_with_activity(
            get_agent_worker_activities(),
            _prepare_on_installed_backend(run_id=run_id, task_queue=agent_queue),
        ),
        workflows=[InstalledBackendTurnWorkflow],
        workflow_runner=new_agent_sandbox_runner(),
        activity_executor=threadpool,
    )

    async with (
        test_worker_factory(
            temporal_client, activities=list(get_dsl_worker_activities())
        ),
        agent_worker,
    ):
        handle = await temporal_client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl, role=test_role, wf_id=wf_id, registry_lock=_REGISTRY_LOCK
            ),
            id=generate_exec_id(wf_id),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            execution_timeout=timedelta(seconds=60),
        )
        child = temporal_client.get_workflow_handle(f"agent/{run_id}")
        await _wait_until_started(child)
        # The approvals inbox submits through the backend's control path.
        resumed = await backend.submit_approvals(
            run_id,
            WorkflowApprovalSubmission(
                approvals={"call_1": True}, new_stream_id=uuid.uuid4()
            ),
        )
        assert resumed is True
        result = await handle.result()
        memo = await (await child.describe()).memo()

    data = await to_data(result)
    assert data["output"] == {
        "verdict": "benign",
        "prompt": "Investigate this alert",
        "output_type": output_type,
        "approved": True,
    }
    session_id = uuid.UUID(str(data["session_id"]))
    # The run view finds the session through the child's memo.
    assert memo["action_ref"] == "agent"
    assert uuid.UUID(str(memo["session_id"])) == session_id


@pytest.mark.anyio
async def test_agent_action_keeps_the_builtin_workflow_by_default(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[..., Worker],
    threadpool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the built-in backend selected, the durable workflow runs as before."""
    monkeypatch.setattr(config, "TRACECAT__AGENT_SESSION_BACKEND", "oss")
    agent_queue = config.TRACECAT__AGENT_QUEUE
    wf_id = WorkflowUUID.new_uuid4()
    agent_worker = Worker(
        client=temporal_client,
        task_queue=agent_queue,
        activities=get_agent_worker_activities(),
        workflows=[BuiltinAgentWorkflow],
        workflow_runner=new_agent_sandbox_runner(),
        activity_executor=threadpool,
    )
    async with (
        test_worker_factory(
            temporal_client, activities=list(get_dsl_worker_activities())
        ),
        agent_worker,
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=_agent_action_dsl(),
                role=test_role,
                wf_id=wf_id,
                registry_lock=_REGISTRY_LOCK,
            ),
            id=generate_exec_id(wf_id),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            execution_timeout=timedelta(seconds=60),
        )
    data = await to_data(result)
    assert data["output"] == "built-in output"
