"""Test that TestBackend executes UDFs without querying RegistryAction/RegistryActionsService.

This test verifies the refactoring that removes DB lookups from TestBackend.
Workflow execution resolves implementations from RegistryVersion.manifest via
registry_resolver (manifest-based). TestBackend should simply execute a UDF
using the already-resolved ActionImplementation (module/name/origin).
"""

from __future__ import annotations

import asyncio
import sys
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tracecat_registry import secrets as registry_secrets

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.executor.backends.test import TestBackend
from tracecat.executor.registry_artifacts import bundled_builtin_registry_uri
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorResult,
    ResolvedContext,
)
from tracecat.executor.secret_preprocessors import SecretEnvProjection
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

    def test_backend_instances_do_not_share_loop_affine_cache(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Function-scoped backends can run in successive pytest event loops."""
        monkeypatch.setattr(
            "tracecat.executor.backends.test.config"
            ".TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR",
            str(tmp_path),
        )
        first_backend = TestBackend()
        second_backend = TestBackend()

        async def sweep(backend: TestBackend) -> None:
            await backend._registry_artifact_cache().ensure_swept()

        asyncio.run(sweep(first_backend))
        asyncio.run(sweep(second_backend))

        assert (
            first_backend._registry_artifact_cache()
            is not second_backend._registry_artifact_cache()
        )

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

    @pytest.mark.anyio
    async def test_execute_holds_artifact_leases_for_the_whole_execution(
        self,
        test_role: Role,
        test_resolved_context: ResolvedContext,
        test_run_action_input: RunActionInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Leases span execution, and one bad artifact does not drop the others."""
        good_path = tmp_path / "good-artifact"
        good_path.mkdir()
        broken_uri = "s3://bucket/broken.tar.gz"
        good_uri = "s3://bucket/good.tar.gz"

        class FakeRegistryArtifacts:
            def __init__(self) -> None:
                self.active = 0

            async def ensure_swept(self) -> None:
                pass

            @asynccontextmanager
            async def lease(
                self,
                artifact_uris: list[str] | None = None,
                *,
                paths_may_be_modified: bool = False,
            ) -> AsyncIterator[list[Path]]:
                assert paths_may_be_modified is True
                if broken_uri in (artifact_uris or []):
                    raise RuntimeError("artifact unavailable")
                self.active += 1
                try:
                    yield [good_path]
                finally:
                    self.active -= 1

        class FakeActionRunner:
            def __init__(self) -> None:
                self.registry_artifacts = FakeRegistryArtifacts()

        fake_runner = FakeActionRunner()
        observed: list[tuple[int, bool]] = []

        async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
            return [broken_uri, good_uri]

        backend = TestBackend()
        await backend.start()

        try:
            monkeypatch.setattr(
                "tracecat.executor.backends.test.config"
                ".TRACECAT__LOCAL_REPOSITORY_ENABLED",
                False,
            )
            monkeypatch.setattr(
                backend,
                "_registry_artifact_cache",
                lambda: fake_runner.registry_artifacts,
            )
            monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)
            monkeypatch.setattr(
                backend,
                "_load_udf_callable",
                lambda _action_impl: (
                    lambda **_kwargs: observed.append(
                        (
                            fake_runner.registry_artifacts.active,
                            str(good_path) in sys.path,
                        )
                    )
                ),
            )

            result = await backend.execute(
                input=test_run_action_input,
                role=test_role,
                resolved_context=test_resolved_context,
                timeout=30.0,
            )

            assert result.type == "success"
            assert observed == [(1, True)]
            assert fake_runner.registry_artifacts.active == 0
            assert str(good_path) not in sys.path
        finally:
            await backend.shutdown()

    @pytest.mark.anyio
    async def test_execute_surfaces_registry_cache_sweep_failure(
        self,
        test_role: Role,
        test_run_action_input: RunActionInput,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Global cache inspection errors are not treated as one bad artifact."""
        artifact_uri = "s3://bucket/registry.tar.gz"

        class FakeRegistryArtifacts:
            def __init__(self) -> None:
                self.lease_attempted = False

            async def ensure_swept(self) -> None:
                raise PermissionError("cannot inspect registry cache")

            @asynccontextmanager
            async def lease(
                self,
                artifact_uris: list[str] | None = None,
                *,
                paths_may_be_modified: bool = False,
            ) -> AsyncIterator[list[Path]]:
                del artifact_uris, paths_may_be_modified
                self.lease_attempted = True
                yield []

        class FakeActionRunner:
            def __init__(self) -> None:
                self.registry_artifacts = FakeRegistryArtifacts()

        fake_runner = FakeActionRunner()

        async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
            return [artifact_uri]

        backend = TestBackend()
        monkeypatch.setattr(
            "tracecat.executor.backends.test.config.TRACECAT__LOCAL_REPOSITORY_ENABLED",
            False,
        )
        monkeypatch.setattr(
            backend,
            "_registry_artifact_cache",
            lambda: fake_runner.registry_artifacts,
        )
        monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)

        with pytest.raises(PermissionError, match="cannot inspect registry cache"):
            async with AsyncExitStack() as leases:
                await backend._lease_registry_artifacts(
                    leases,
                    test_run_action_input,
                    test_role,
                )

        assert fake_runner.registry_artifacts.lease_attempted is False

    @pytest.mark.anyio
    async def test_builtin_only_execution_skips_registry_cache_sweep(
        self,
        test_role: Role,
        test_run_action_input: RunActionInput,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cache-free builtin execution is independent of cache inspection."""
        artifact_uri = bundled_builtin_registry_uri("1.2.3")

        class FakeRegistryArtifacts:
            def __init__(self) -> None:
                self.sweep_attempted = False
                self.lease_attempted = False

            async def ensure_swept(self) -> None:
                self.sweep_attempted = True
                raise PermissionError("cannot inspect registry cache")

            @asynccontextmanager
            async def lease(
                self,
                artifact_uris: list[str] | None = None,
                *,
                paths_may_be_modified: bool = False,
            ) -> AsyncIterator[list[Path]]:
                assert artifact_uris == [artifact_uri]
                assert paths_may_be_modified is False
                self.lease_attempted = True
                yield []

        registry_artifacts = FakeRegistryArtifacts()

        async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
            return [artifact_uri]

        backend = TestBackend()
        monkeypatch.setattr(
            "tracecat.executor.backends.test.config.TRACECAT__LOCAL_REPOSITORY_ENABLED",
            False,
        )
        monkeypatch.setattr(
            backend,
            "_registry_artifact_cache",
            lambda: registry_artifacts,
        )
        monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)

        async with AsyncExitStack() as leases:
            assert (
                await backend._lease_registry_artifacts(
                    leases,
                    test_run_action_input,
                    test_role,
                )
                == []
            )

        assert registry_artifacts.sweep_attempted is False
        assert registry_artifacts.lease_attempted is True

    @pytest.mark.anyio
    async def test_timed_out_sync_udf_keeps_artifact_lease_until_thread_finishes(
        self,
        test_role: Role,
        test_resolved_context: ResolvedContext,
        test_run_action_input: RunActionInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A soft timeout cannot release paths beneath a live UDF thread."""
        artifact_path = tmp_path / "artifact"
        artifact_path.mkdir()
        artifact_uri = "s3://bucket/sync-timeout.tar.gz"
        worker_started = threading.Event()
        finish_worker = threading.Event()
        timeout_triggered = asyncio.Event()

        class FakeRegistryArtifacts:
            def __init__(self) -> None:
                self.active = 0

            async def ensure_swept(self) -> None:
                pass

            @asynccontextmanager
            async def lease(
                self,
                artifact_uris: list[str] | None = None,
                *,
                paths_may_be_modified: bool = False,
            ) -> AsyncIterator[list[Path]]:
                assert artifact_uris == [artifact_uri]
                assert paths_may_be_modified is True
                self.active += 1
                try:
                    yield [artifact_path]
                finally:
                    self.active -= 1

        class FakeActionRunner:
            def __init__(self) -> None:
                self.registry_artifacts = FakeRegistryArtifacts()

        fake_runner = FakeActionRunner()

        async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
            return [artifact_uri]

        def blocking_udf(**_kwargs: object) -> str:
            worker_started.set()
            assert finish_worker.wait(timeout=5)
            return "finished"

        async def wait_for_after_worker_started[T](
            awaitable: Awaitable[T],
            timeout: float | None,
        ) -> T:
            """Drive wait_for cancellation only after the UDF thread exists."""
            del timeout
            task = asyncio.ensure_future(awaitable)
            assert await asyncio.to_thread(worker_started.wait, 1)
            task.cancel()
            timeout_triggered.set()
            try:
                return await task
            except asyncio.CancelledError as e:
                raise TimeoutError from e

        backend = TestBackend()
        await backend.start()
        execution: asyncio.Task[ExecutorResult] | None = None
        try:
            monkeypatch.setattr(
                "tracecat.executor.backends.test.config"
                ".TRACECAT__LOCAL_REPOSITORY_ENABLED",
                False,
            )
            monkeypatch.setattr(
                backend,
                "_registry_artifact_cache",
                lambda: fake_runner.registry_artifacts,
            )
            monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)
            monkeypatch.setattr(
                backend,
                "_load_udf_callable",
                lambda _action_impl: blocking_udf,
            )
            monkeypatch.setattr(asyncio, "wait_for", wait_for_after_worker_started)

            execution = asyncio.create_task(
                backend.execute(
                    input=test_run_action_input,
                    role=test_role,
                    resolved_context=test_resolved_context,
                    timeout=30.0,
                )
            )
            await timeout_triggered.wait()

            assert not execution.done()
            assert fake_runner.registry_artifacts.active == 1
            assert str(artifact_path) in sys.path

            finish_worker.set()
            result = await execution
            assert result.type == "failure"
            assert result.error.type == "TimeoutError"
            assert fake_runner.registry_artifacts.active == 0
            assert str(artifact_path) not in sys.path
        finally:
            finish_worker.set()
            if execution is not None:
                await execution
            await backend.shutdown()

    @pytest.mark.anyio
    async def test_execute_udf_reuses_cached_secret_projection(
        self,
        test_role: Role,
        test_resolved_context: ResolvedContext,
        test_run_action_input: RunActionInput,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cached_projection = SecretEnvProjection(
            env={"TOKEN": "cached-token"},
            mask_values={"cached-token"},
        )
        test_resolved_context.secret_projection = cached_projection

        async def _unexpected_project_secret_env(*args, **kwargs):
            pytest.fail(
                "project_secret_env should not be called when projection is cached"
            )

        backend = TestBackend()
        await backend.start()

        try:
            monkeypatch.setattr(
                "tracecat.executor.backends.test.project_secret_env",
                _unexpected_project_secret_env,
            )
            monkeypatch.setattr(
                backend,
                "_load_udf_callable",
                lambda _action_impl: lambda **_kwargs: registry_secrets.get("TOKEN"),
            )

            result = await backend.execute(
                input=test_run_action_input,
                role=test_role,
                resolved_context=test_resolved_context,
                timeout=30.0,
            )

            assert result.type == "success"
            assert result.result == "cached-token"
        finally:
            await backend.shutdown()
