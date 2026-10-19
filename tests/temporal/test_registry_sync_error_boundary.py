"""Registry sync failures keep their diagnostic across the Temporal boundary."""

from __future__ import annotations

import gc
import uuid
from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment

from tracecat import config
from tracecat.dsl._converter import get_data_converter
from tracecat.executor.enums import ExecutorBackendType
from tracecat.registry.sync.base_service import _workflow_failure_detail
from tracecat.registry.sync.runner import ActionDiscoveryError, RegistrySyncRunner
from tracecat.registry.sync.sandbox import NsjailExecutor
from tracecat.registry.sync.schemas import RegistrySyncRequest
from tracecat.registry.sync.workflow import (
    RegistrySyncWorkflow,
    sync_registry_activity,
)
from tracecat.runtime.errors import RuntimeErrorOwner
from tracecat.sandbox.types import SandboxResult
from tracecat.temporal.errors import extract_error_classifications
from tracecat.workflow.executions.enums import TemporalSearchAttr

pytestmark = [pytest.mark.temporal]


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    gc.collect()
    async with await WorkflowEnvironment.start_local(
        data_converter=get_data_converter(compression_enabled=False),
        search_attributes=[TemporalSearchAttr.ERROR_OWNER.key],
        dev_server_log_level="error",
    ) as environment:
        yield environment


@pytest.mark.anyio
async def test_retryable_sync_failure_reaches_caller_through_interceptor(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow -> interceptor -> Temporal -> API keeps a redacted diagnostic."""
    secret = "Bearer synthetic-registry-token-value"
    attempts = 0

    class _FakeRunner:
        async def run(self, request: RegistrySyncRequest) -> None:
            del request
            nonlocal attempts
            attempts += 1
            raise ActionDiscoveryError(
                "Failed to discover actions: Sandboxed registry discovery "
                f"failed: RuntimeError: worker lost (Authorization: {secret})"
            )

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.RegistrySyncRunner", _FakeRunner
    )
    task_queue = f"registry-sync-boundary-{uuid.uuid4()}"
    workflow_id = f"registry-sync-{uuid.uuid4()}"
    request = RegistrySyncRequest(
        repository_id=uuid.uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    async with test_worker_factory(
        env.client,
        activities=[sync_registry_activity],
        workflows=[RegistrySyncWorkflow],
        task_queue=task_queue,
    ):
        handle = await env.client.start_workflow(
            RegistrySyncWorkflow.run,
            request,
            id=workflow_id,
            task_queue=task_queue,
            execution_timeout=timedelta(minutes=20),
            retry_policy=RetryPolicy(
                maximum_attempts=2,
                initial_interval=timedelta(seconds=5),
            ),
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    failure = exc_info.value
    detail = _workflow_failure_detail(
        failure,
        extract_error_classifications(failure, include_implicit_context=False),
    )
    assert detail.startswith("Registry action discovery failed: ")
    assert "RuntimeError: worker lost" in detail
    assert "unclassified" not in detail
    assert "synthetic-registry-token-value" not in detail

    history = await handle.fetch_history()
    assert "synthetic-registry-token-value" not in history.to_json()
    # Activity retries are exhausted once; the workflow is not rerun.
    assert attempts == 3
    latest = await env.client.get_workflow_handle(workflow_id).describe()
    assert latest.run_id == handle.result_run_id


@pytest.mark.anyio
async def test_sandbox_install_failure_reaches_caller_through_interceptor(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A real sandboxed install failure keeps its diagnostic and is not rerun."""
    secret = "Bearer synthetic-index-token-value"
    package_path = tmp_path / "registry"
    package_path.mkdir()
    (package_path / "pyproject.toml").write_text("[project]\nname='registry'\n")
    attempts = 0

    async def _failed_install(*_args: object, **_kwargs: object) -> SandboxResult:
        nonlocal attempts
        attempts += 1
        return SandboxResult(
            success=False,
            error=(
                "No solution found when resolving dependencies: "
                f"registry depends on missing-pkg (Authorization: {secret})"
            ),
        )

    async def _package_path(_self: RegistrySyncRunner) -> Path:
        return package_path

    monkeypatch.setattr(
        config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.NSJAIL
    )
    monkeypatch.setattr("tracecat.registry.sync.sandbox.sandbox_cgroup", lambda: None)
    monkeypatch.setattr(NsjailExecutor, "execute_install", _failed_install)
    monkeypatch.setattr(RegistrySyncRunner, "_get_builtin_package_path", _package_path)
    task_queue = f"registry-sync-boundary-{uuid.uuid4()}"
    workflow_id = f"registry-sync-{uuid.uuid4()}"
    request = RegistrySyncRequest(
        repository_id=uuid.uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    async with test_worker_factory(
        env.client,
        activities=[sync_registry_activity],
        workflows=[RegistrySyncWorkflow],
        task_queue=task_queue,
    ):
        handle = await env.client.start_workflow(
            RegistrySyncWorkflow.run,
            request,
            id=workflow_id,
            task_queue=task_queue,
            execution_timeout=timedelta(minutes=20),
            retry_policy=RetryPolicy(
                maximum_attempts=2,
                initial_interval=timedelta(seconds=5),
            ),
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    failure = exc_info.value
    classifications = extract_error_classifications(
        failure, include_implicit_context=False
    )
    assert classifications[0].owner is RuntimeErrorOwner.USER
    detail = _workflow_failure_detail(failure, classifications)
    assert detail.startswith("Registry package installation failed: ")
    assert "No solution found when resolving dependencies" in detail
    assert "unclassified" not in detail
    assert "synthetic-index-token-value" not in detail

    history = await handle.fetch_history()
    assert "synthetic-index-token-value" not in history.to_json()
    assert attempts == 3
    latest = await env.client.get_workflow_handle(workflow_id).describe()
    assert latest.run_id == handle.result_run_id
