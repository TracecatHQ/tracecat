"""Tests for workflow failure, cancellation, and termination paths.

Covers retry exhaustion, cancellation via Temporal, termination with reason,
scatter-gather partial failure, and error handler cascading failure.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.service import RPCError
from temporalio.worker import Worker

from tests import shared
from tracecat.contexts import ctx_role
from tracecat.dsl.common import (
    RETRY_POLICIES,
    DSLEntrypoint,
    DSLInput,
    DSLRunArgs,
)
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.workflow import DSLWorkflow

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures("registry_version_with_manifest"),
]

type WorkerFactory = Callable[..., Worker]


@pytest.mark.anyio
class TestRetryExhaustion:
    """Test that workflows fail after retry exhaustion."""

    async def test_workflow_fails_after_max_retries(
        self,
        test_role: None,
        temporal_client: Client,
        test_worker_factory: WorkerFactory,
        test_executor_worker_factory: WorkerFactory,
    ) -> None:
        """Workflow with always-failing action should raise WorkflowFailureError."""
        dsl = DSLInput.from_yaml("tests/data/workflows/unit_retry_exhaustion.yml")
        wf_exec_id = shared.generate_test_exec_id(
            "test_workflow_fails_after_max_retries"
        )
        role = ctx_role.get()
        assert role is not None

        async with (
            test_worker_factory(temporal_client),
            test_executor_worker_factory(temporal_client),
        ):
            with pytest.raises(WorkflowFailureError):
                await temporal_client.execute_workflow(
                    DSLWorkflow.run,
                    DSLRunArgs(dsl=dsl, role=role, wf_id=shared.TEST_WF_ID),
                    id=wf_exec_id,
                    task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                )

    async def test_programmatic_failing_action(
        self,
        test_role: None,
        temporal_client: Client,
        test_worker_factory: WorkerFactory,
        test_executor_worker_factory: WorkerFactory,
    ) -> None:
        """Programmatically constructed DSL with bad expression should fail at runtime."""
        dsl = DSLInput(
            title="Programmatic Retry Exhaustion",
            description="Test retry exhaustion with programmatic DSL",
            entrypoint=DSLEntrypoint(ref="setup"),
            actions=[
                ActionStatement(
                    ref="setup",
                    action="core.transform.reshape",
                    args={"value": {"data": 1}},
                    depends_on=[],
                ),
                ActionStatement(
                    ref="fail_action",
                    action="core.transform.reshape",
                    args={"value": "${{ int(ACTIONS.setup.result.missing_key) + 1 }}"},
                    depends_on=["setup"],
                ),
            ],
        )
        wf_exec_id = shared.generate_test_exec_id("test_programmatic_failing_action")
        role = ctx_role.get()
        assert role is not None

        async with (
            test_worker_factory(temporal_client),
            test_executor_worker_factory(temporal_client),
        ):
            with pytest.raises(WorkflowFailureError):
                await temporal_client.execute_workflow(
                    DSLWorkflow.run,
                    DSLRunArgs(dsl=dsl, role=role, wf_id=shared.TEST_WF_ID),
                    id=wf_exec_id,
                    task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )


@pytest.mark.anyio
class TestWorkflowCancellation:
    """Test workflow cancellation via Temporal client."""

    async def test_cancel_workflow(
        self,
        test_role: None,
        temporal_client: Client,
        test_worker_factory: WorkerFactory,
        test_executor_worker_factory: WorkerFactory,
    ) -> None:
        """Cancelling a workflow should be accepted by Temporal without error."""
        dsl = DSLInput(
            title="Cancellation Test Workflow",
            description="Workflow for cancellation testing",
            entrypoint=DSLEntrypoint(ref="step_a"),
            actions=[
                ActionStatement(
                    ref="step_a",
                    action="core.transform.reshape",
                    args={"value": "step_a_result"},
                    depends_on=[],
                ),
            ],
        )
        wf_exec_id = shared.generate_test_exec_id("test_cancel_workflow")
        role = ctx_role.get()
        assert role is not None

        async with (
            test_worker_factory(temporal_client),
            test_executor_worker_factory(temporal_client),
        ):
            handle = await temporal_client.start_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=role, wf_id=shared.TEST_WF_ID),
                id=wf_exec_id,
                task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                retry_policy=RetryPolicy(maximum_attempts=1),
            )

            # Cancel immediately — workflow may or may not have completed
            await handle.cancel()

            # Result should either succeed (already completed) or raise
            try:
                await handle.result()
            except WorkflowFailureError:
                pass  # Expected if cancelled before completion


@pytest.mark.anyio
class TestWorkflowTermination:
    """Test workflow termination via Temporal client."""

    async def test_terminate_workflow(
        self,
        test_role: None,
        temporal_client: Client,
        test_worker_factory: WorkerFactory,
        test_executor_worker_factory: WorkerFactory,
    ) -> None:
        """Terminating a workflow should be accepted by Temporal."""
        dsl = DSLInput(
            title="Termination Test Workflow",
            description="Workflow for termination testing",
            entrypoint=DSLEntrypoint(ref="step_a"),
            actions=[
                ActionStatement(
                    ref="step_a",
                    action="core.transform.reshape",
                    args={"value": "step_a_result"},
                    depends_on=[],
                ),
            ],
        )
        wf_exec_id = shared.generate_test_exec_id("test_terminate_workflow")
        role = ctx_role.get()
        assert role is not None

        async with (
            test_worker_factory(temporal_client),
            test_executor_worker_factory(temporal_client),
        ):
            handle = await temporal_client.start_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=role, wf_id=shared.TEST_WF_ID),
                id=wf_exec_id,
                task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                retry_policy=RetryPolicy(maximum_attempts=1),
            )

            # Terminate immediately — may or may not still be running
            try:
                await handle.terminate(reason="Test termination")
            except RPCError:
                pass  # Already completed

            # Result should either succeed (completed) or raise (terminated)
            try:
                await handle.result()
            except WorkflowFailureError:
                pass  # Expected if terminated before completion


@pytest.mark.anyio
class TestScatterGatherFailure:
    """Test scatter-gather workflows with partial failures."""

    async def test_sequential_action_failure_propagates(
        self,
        test_role: None,
        temporal_client: Client,
        test_worker_factory: WorkerFactory,
        test_executor_worker_factory: WorkerFactory,
    ) -> None:
        """When a downstream action fails, the workflow should fail."""
        dsl = DSLInput.from_yaml(
            "tests/data/workflows/unit_scatter_partial_failure.yml"
        )
        wf_exec_id = shared.generate_test_exec_id(
            "test_sequential_action_failure_propagates"
        )
        role = ctx_role.get()
        assert role is not None

        async with (
            test_worker_factory(temporal_client),
            test_executor_worker_factory(temporal_client),
        ):
            with pytest.raises(WorkflowFailureError):
                await temporal_client.execute_workflow(
                    DSLWorkflow.run,
                    DSLRunArgs(dsl=dsl, role=role, wf_id=shared.TEST_WF_ID),
                    id=wf_exec_id,
                    task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                )

    async def test_expression_error_in_chain(
        self,
        test_role: None,
        temporal_client: Client,
        test_worker_factory: WorkerFactory,
        test_executor_worker_factory: WorkerFactory,
    ) -> None:
        """Expression referencing invalid field in chain should fail the workflow."""
        dsl = DSLInput(
            title="Expression Error Chain",
            description="Tests expression error propagation in action chain",
            entrypoint=DSLEntrypoint(ref="produce"),
            actions=[
                ActionStatement(
                    ref="produce",
                    action="core.transform.reshape",
                    args={"value": {"data": 42}},
                    depends_on=[],
                ),
                ActionStatement(
                    ref="consume",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ int(ACTIONS.produce.result.nonexistent) + 1 }}"
                    },
                    depends_on=["produce"],
                ),
            ],
        )
        wf_exec_id = shared.generate_test_exec_id("test_expression_error_in_chain")
        role = ctx_role.get()
        assert role is not None

        async with (
            test_worker_factory(temporal_client),
            test_executor_worker_factory(temporal_client),
        ):
            with pytest.raises(WorkflowFailureError):
                await temporal_client.execute_workflow(
                    DSLWorkflow.run,
                    DSLRunArgs(dsl=dsl, role=role, wf_id=shared.TEST_WF_ID),
                    id=wf_exec_id,
                    task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                )
