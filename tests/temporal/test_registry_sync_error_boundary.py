"""Registry sync failures keep their diagnostic across the Temporal boundary."""

from __future__ import annotations

import gc
import json
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment

from tracecat import config
from tracecat.dsl._converter import get_data_converter
from tracecat.executor.enums import ExecutorBackendType
from tracecat.registry.sync.base_service import _workflow_failure_detail
from tracecat.registry.sync.runner import (
    ActionDiscoveryError,
    RegistrySyncRunner,
    _ResolvedPackage,
)
from tracecat.registry.sync.sandbox import NsjailExecutor, RegistrySyncSandbox
from tracecat.registry.sync.schemas import RegistrySyncRequest
from tracecat.registry.sync.workflow import (
    RegistrySyncWorkflow,
    sync_registry_activity,
)
from tracecat.runtime.errors import RuntimeErrorKind, RuntimeErrorOwner
from tracecat.sandbox.types import SandboxErrorCode, SandboxResult
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
@pytest.mark.parametrize(
    ("origin_type", "error_code", "owner"),
    [
        pytest.param("git", None, RuntimeErrorOwner.USER, id="git_workload"),
        pytest.param(
            "git",
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            RuntimeErrorOwner.PLATFORM,
            id="git_infrastructure",
        ),
        pytest.param(
            "builtin", None, RuntimeErrorOwner.PLATFORM, id="builtin_workload"
        ),
    ],
)
async def test_sandbox_install_failure_reaches_caller_through_interceptor(
    origin_type: Literal["builtin", "git"],
    error_code: SandboxErrorCode | None,
    owner: RuntimeErrorOwner,
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
    task_queue = f"registry-sync-boundary-{uuid.uuid4()}"
    workflow_id = f"registry-sync-{uuid.uuid4()}"
    request = RegistrySyncRequest(
        repository_id=uuid.uuid4(),
        origin="git+ssh://git@git.example.test/acme/registry.git",
        origin_type=origin_type,
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
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    failure = exc_info.value
    classifications = extract_error_classifications(
        failure, include_implicit_context=False
    )
    assert classifications[0].owner is owner
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
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    classifications = extract_error_classifications(
        exc_info.value, include_implicit_context=False
    )
    assert classifications[0].owner is RuntimeErrorOwner.PLATFORM
    assert classifications[0].kind is RuntimeErrorKind.REGISTRY_SYNC_VALIDATION_FAILED
    detail = _workflow_failure_detail(exc_info.value, classifications)
    assert "Registry dependencies conflict with Tracecat's runtime" in detail
    assert attempts == 1
