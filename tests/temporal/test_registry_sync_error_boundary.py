"""Registry sync failures keep their diagnostic across the Temporal boundary."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment

from tests.temporal import runtime_error_attribution_harness as harness
from tracecat import config
from tracecat.executor.enums import ExecutorBackendType
from tracecat.registry.sync import workflow as sync_workflow
from tracecat.registry.sync.base_service import _workflow_failure_detail
from tracecat.registry.sync.runner import (
    ActionDiscoveryError,
    RegistrySyncRunner,
    _ResolvedPackage,
)
from tracecat.registry.sync.sandbox import NsjailExecutor, RegistrySyncSandbox
from tracecat.registry.sync.schemas import RegistrySyncRequest
from tracecat.registry.sync.workflow import (
    REGISTRY_SYNC_WORKFLOW_EXECUTION_TIMEOUT,
    RegistrySyncWorkflow,
    sync_registry_activity,
)
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.sandbox.types import SandboxErrorCode, SandboxResult
from tracecat.temporal.errors import extract_error_classifications

pytestmark = [pytest.mark.temporal]

env = harness.env


@pytest.fixture(autouse=True)
def fast_activity_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the production retry policy without waiting out its backoff."""
    monkeypatch.setattr(
        sync_workflow,
        "SYNC_ACTIVITY_RETRY_POLICY",
        dataclasses.replace(
            sync_workflow.SYNC_ACTIVITY_RETRY_POLICY,
            initial_interval=timedelta(milliseconds=10),
        ),
    )


@dataclass(frozen=True, slots=True)
class _SyncFailure:
    classification: RuntimeErrorClassification
    detail: str
    history_json: str


async def _run_sync_to_failure(
    env: WorkflowEnvironment,
    test_worker_factory: Any,
    request: RegistrySyncRequest,
) -> _SyncFailure:
    """Run the sync workflow like the API does and return its single failed run."""
    task_queue = f"registry-sync-boundary-{uuid.uuid4()}"
    workflow_id = f"registry-sync-{uuid.uuid4()}"
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
            execution_timeout=REGISTRY_SYNC_WORKFLOW_EXECUTION_TIMEOUT,
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    failure = exc_info.value
    classifications = extract_error_classifications(
        failure, include_implicit_context=False
    )
    # The workflow is not rerun after its activity fails.
    latest = await env.client.get_workflow_handle(workflow_id).describe()
    assert latest.run_id == handle.result_run_id
    history = await handle.fetch_history()
    return _SyncFailure(
        classification=classifications[0],
        detail=_workflow_failure_detail(failure, classifications),
        history_json=history.to_json(),
    )


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
    request = RegistrySyncRequest(
        repository_id=uuid.uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    failure = await _run_sync_to_failure(env, test_worker_factory, request)

    assert failure.detail.startswith("Registry action discovery failed: ")
    assert "RuntimeError: worker lost" in failure.detail
    assert "unclassified" not in failure.detail
    assert "synthetic-registry-token-value" not in failure.detail
    assert "synthetic-registry-token-value" not in failure.history_json
    assert attempts == sync_workflow.SYNC_ACTIVITY_RETRY_POLICY.maximum_attempts


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("origin_type", "error_code", "owner", "retry", "detail_prefix"),
    [
        pytest.param(
            "git",
            None,
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "No solution found when resolving dependencies",
            id="git_workload",
        ),
        pytest.param(
            "git",
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Sandboxed registry package installation stopped by the sandbox",
            id="git_phase_limit",
        ),
        pytest.param(
            "git",
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "Sandboxed registry package installation failed before the workload",
            id="git_infrastructure",
        ),
        pytest.param(
            "builtin",
            None,
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
            "No solution found when resolving dependencies",
            id="builtin_workload",
        ),
    ],
)
async def test_sandbox_install_failure_reaches_caller_through_interceptor(
    origin_type: Literal["builtin", "git"],
    error_code: SandboxErrorCode | None,
    owner: RuntimeErrorOwner,
    retry: RetryDisposition,
    detail_prefix: str,
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
            error_code=error_code,
        )

    async def _resolved_package(
        _self: RegistrySyncRunner, *_args: object
    ) -> _ResolvedPackage:
        return _ResolvedPackage(package_path, None)

    monkeypatch.setattr(
        config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.NSJAIL
    )
    monkeypatch.setattr("tracecat.registry.sync.sandbox.sandbox_cgroup", lambda: None)
    monkeypatch.setattr(NsjailExecutor, "execute_install", _failed_install)
    monkeypatch.setattr(
        RegistrySyncRunner, "_resolve_sandboxed_package", _resolved_package
    )
    request = RegistrySyncRequest(
        repository_id=uuid.uuid4(),
        origin="git+ssh://git@git.example.test/acme/registry.git",
        origin_type=origin_type,
    )

    failure = await _run_sync_to_failure(env, test_worker_factory, request)

    assert failure.classification.owner is owner
    assert failure.classification.retry_disposition is retry
    assert failure.detail.startswith("Registry package installation failed: ")
    assert detail_prefix in failure.detail
    assert "unclassified" not in failure.detail
    assert "synthetic-index-token-value" not in failure.detail
    assert "synthetic-index-token-value" not in failure.history_json
    max_attempts = sync_workflow.SYNC_ACTIVITY_RETRY_POLICY.maximum_attempts
    assert attempts == (1 if retry is RetryDisposition.NON_RETRYABLE else max_attempts)


_RUN_DISCOVERY_SCRIPT = """
import json, runpy, sys
namespace = runpy.run_path("script.py")
with open("inputs.json", encoding="utf-8") as file:
    inputs = json.load(file)
json.dump(namespace["main"](**inputs), sys.stdout)
"""


@pytest.mark.anyio
async def test_builtin_discovery_import_failure_is_platform_owned(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A broken bundled runtime fails fast and still reaches platform reporting."""
    attempts = 0
    package_path = tmp_path / "registry"
    package_path.mkdir()
    site_packages = tmp_path / "sandbox-install" / "cache" / "site-packages"
    site_packages.mkdir(parents=True)
    # A regular package shadows the installed OpenTelemetry namespace package,
    # so Tracecat's own discovery imports fail like a conflicting dependency.
    shadow = tmp_path / "shadow"
    (shadow / "opentelemetry").mkdir(parents=True)
    (shadow / "opentelemetry" / "__init__.py").write_text("")

    async def _resolved_package(
        _self: RegistrySyncRunner, *_args: object
    ) -> _ResolvedPackage:
        return _ResolvedPackage(package_path, None)

    async def _installed(_self: RegistrySyncSandbox, **_kwargs: object) -> Path:
        nonlocal attempts
        attempts += 1
        return site_packages

    async def _run_discovery_script(
        _self: NsjailExecutor, job_dir: Path, *_args: object, **_kwargs: object
    ) -> SandboxResult:
        completed = subprocess.run(
            [sys.executable, "-c", _RUN_DISCOVERY_SCRIPT],
            cwd=job_dir,
            env={"PYTHONPATH": ":".join([str(shadow), *sys.path])},
            capture_output=True,
            text=True,
            check=True,
        )
        return SandboxResult(success=True, output=json.loads(completed.stdout))

    monkeypatch.setattr(
        config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.NSJAIL
    )
    monkeypatch.setattr("tracecat.registry.sync.sandbox.sandbox_cgroup", lambda: None)
    monkeypatch.setattr(
        RegistrySyncRunner, "_resolve_sandboxed_package", _resolved_package
    )
    monkeypatch.setattr(
        RegistrySyncRunner, "_load_prebuilt_actions", lambda _self, _request: None
    )
    monkeypatch.setattr(RegistrySyncSandbox, "install_package", _installed)
    monkeypatch.setattr(NsjailExecutor, "execute", _run_discovery_script)
    request = RegistrySyncRequest(
        repository_id=uuid.uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    failure = await _run_sync_to_failure(env, test_worker_factory, request)

    assert failure.classification.owner is RuntimeErrorOwner.PLATFORM
    assert (
        failure.classification.kind is RuntimeErrorKind.REGISTRY_SYNC_VALIDATION_FAILED
    )
    assert "Tracecat's runtime failed to import" in failure.detail
    assert attempts == 1
