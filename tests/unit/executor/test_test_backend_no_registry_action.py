"""Test that TestBackend executes UDFs without querying RegistryAction/RegistryActionsService.

This test verifies the refactoring that removes DB lookups from TestBackend.
Workflow execution resolves implementations from RegistryVersion.manifest via
registry_resolver (manifest-based). TestBackend should simply execute a UDF
using the already-resolved ActionImplementation (module/name/origin).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.executor.backends.test import TestBackend
from tracecat.executor.schemas import ActionImplementation, ResolvedContext
from tracecat.identifiers.workflow import ExecutionUUID, WorkflowUUID
from tracecat.registry.lock.types import RegistryLock


@pytest.fixture
def test_role() -> Role:
    """Create a test role for the test."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.UUID("38be3315-c172-4332-aea6-53fc4b93f053"),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.fixture
def test_resolved_context() -> ResolvedContext:
    """Create a ResolvedContext with a known built-in UDF.

    Uses core.transform.reshape which simply returns its input value.
    """
    return ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(
            type="udf",
            action_name="core.transform.reshape",
            module="tracecat_registry.core.transform",
            name="reshape",
            origin="tracecat_registry",
        ),
        evaluated_args={"value": {"test": "data", "number": 42}},
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        executor_token="test-token",
        logical_time=datetime.now(UTC),
    )


@pytest.fixture
def test_run_action_input() -> RunActionInput:
    """Create a RunActionInput for testing."""
    wf_id = WorkflowUUID.new_uuid4()
    exec_id = ExecutionUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(
            action="core.transform.reshape",
            args={"value": {"test": "data", "number": 42}},
            ref="test_action",
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
            actions={"core.transform.reshape": "tracecat_registry"},
        ),
    )


class TestTestBackendNoRegistryAction:
    """Test that TestBackend does not query RegistryActionsService."""

    @pytest.mark.anyio
    async def test_execute_udf_without_db_lookup(
        self,
        test_role: Role,
        test_resolved_context: ResolvedContext,
        test_run_action_input: RunActionInput,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TestBackend should execute UDFs directly without DB lookup.

        This test monkeypatches RegistryActionsService.with_session to raise
        an error if called. The test passes if the action executes successfully,
        proving that no DB lookup path was used.
        """

        # Monkeypatch RegistryActionsService.with_session to raise if called
        def raise_if_called(*args, **kwargs):
            raise AssertionError(
                "RegistryActionsService.with_session was called! "
                "TestBackend should not query the database."
            )

        # Import and patch at the module level where DirectBackend would import from
        from tracecat.registry.actions import service as registry_service

        monkeypatch.setattr(
            registry_service.RegistryActionsService,
            "with_session",
            classmethod(lambda cls, *args, **kwargs: raise_if_called()),
        )

        # Also patch in the backend module in case of any direct imports
        # (though we've removed them, this ensures the test catches any regression)
        import tracecat.executor.backends.test as test_module

        # Verify the import was removed (should raise AttributeError)
        assert not hasattr(test_module, "RegistryActionsService"), (
            "RegistryActionsService should not be imported in test.py"
        )

        # Create and start the backend
        backend = TestBackend()
        await backend.start()

        try:
            # Execute the action - this should work without any DB lookup
            result = await backend.execute(
                input=test_run_action_input,
                role=test_role,
                resolved_context=test_resolved_context,
                timeout=30.0,
            )

            # Verify the result
            assert result.type == "success", f"Expected success but got: {result}"
            assert result.result == {"test": "data", "number": 42}
        finally:
            await backend.shutdown()

    @pytest.mark.anyio
    async def test_execute_rejects_template_actions(
        self,
        test_role: Role,
        test_run_action_input: RunActionInput,
    ) -> None:
        """TestBackend should reject template actions.

        Templates must be orchestrated at the service layer (_execute_template_action).
        TestBackend should only receive UDF leaf nodes.
        """
        # Create a ResolvedContext with a template action
        template_resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="template",
                action_name="testing.my_template",
                template_definition={"steps": []},
            ),
            evaluated_args={},
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            executor_token="test-token",
            logical_time=datetime.now(UTC),
        )

        backend = TestBackend()
        await backend.start()

        try:
            result = await backend.execute(
                input=test_run_action_input,
                role=test_role,
                resolved_context=template_resolved_context,
                timeout=30.0,
            )

            # Should fail with NotImplementedError wrapped in ExecutorResultFailure
            assert result.type == "failure", f"Expected failure but got: {result}"
            assert "NotImplementedError" in result.error.type
            assert "service layer" in result.error.message.lower()
        finally:
            await backend.shutdown()

    @pytest.mark.anyio
    async def test_execute_udf_missing_module_fails(
        self,
        test_role: Role,
        test_run_action_input: RunActionInput,
    ) -> None:
        """TestBackend should fail with clear error when UDF module is missing."""
        # Create a ResolvedContext with missing module
        bad_resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                action_name="core.transform.reshape",
                module=None,  # Missing!
                name="reshape",
            ),
            evaluated_args={"value": {}},
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            executor_token="test-token",
            logical_time=datetime.now(UTC),
        )

        backend = TestBackend()
        await backend.start()

        try:
            result = await backend.execute(
                input=test_run_action_input,
                role=test_role,
                resolved_context=bad_resolved_context,
                timeout=30.0,
            )

            assert result.type == "failure"
            assert "module" in result.error.message.lower()
        finally:
            await backend.shutdown()
