"""Execution mode parsing and isolation routing regressions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat import config
from tracecat.executor.action_runner import ActionRunner
from tracecat.executor.backends import _create_backend
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.backends.ephemeral import EphemeralBackend
from tracecat.executor.enums import ExecutorBackendType
from tracecat.sandbox import service as sandbox_service
from tracecat.sandbox.exceptions import SandboxInfrastructureError
from tracecat.sandbox.service import SandboxService


@pytest.mark.parametrize("module", ["minimal_runner", "process_supervisor"])
def test_executor_entrypoints_import_without_site_packages(module: str) -> None:
    """Sibling modules must not shadow stdlib imports in standalone scripts."""
    executor_dir = Path(__file__).parents[2] / "tracecat" / "executor"
    # Site initialization can preload stdlib types and hide a sibling types.py.
    subprocess.run(
        [sys.executable, "-S", "-c", f"import {module}"],
        cwd=executor_dir,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "direct"),
        ("", "direct"),
        ("   ", "direct"),
        ("direct", "direct"),
        ("nsjail", "nsjail"),
        ("ephemeral", "nsjail"),
        ("test", "test"),
    ],
)
def test_workers_and_agent_runtime_agree_on_backend(
    value: str | None,
    expected: str,
) -> None:
    env = os.environ.copy()
    env.pop("TRACECAT__EXECUTOR_BACKEND", None)
    if value is not None:
        env["TRACECAT__EXECUTOR_BACKEND"] = value
    # Removed flags cannot override the selected backend.
    env["TRACECAT__DISABLE_NSJAIL"] = "true" if expected == "nsjail" else "false"
    env["TRACECAT__EXECUTOR_SANDBOX_ENABLED"] = (
        "false" if expected == "nsjail" else "true"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tracecat import config; "
            "from tracecat.agent.common import config as agent_config; "
            "print(config.TRACECAT__EXECUTOR_BACKEND, agent_config.TRACECAT__EXECUTOR_BACKEND)",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == f"{expected} {expected}"


@pytest.mark.parametrize("value", ["auto", "invalid"])
def test_unknown_backend_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid TRACECAT__EXECUTOR_BACKEND"):
        ExecutorBackendType.from_config(value)


@pytest.mark.parametrize(
    ("value", "backend_class"),
    [
        ("direct", DirectBackend),
        ("nsjail", EphemeralBackend),
        ("ephemeral", EphemeralBackend),
    ],
)
def test_backend_names_select_existing_implementations(
    value: str,
    backend_class: type[DirectBackend | EphemeralBackend],
) -> None:
    assert (
        type(_create_backend(ExecutorBackendType.from_config(value))) is backend_class
    )


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["direct", "nsjail"])
async def test_action_runner_uses_selected_backend(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_BACKEND", backend)
    runner = ActionRunner(cache_dir=tmp_path)
    direct = AsyncMock(return_value="direct")
    jailed = AsyncMock(return_value="nsjail")
    monkeypatch.setattr(runner, "_execute_direct", direct)
    monkeypatch.setattr(runner, "_execute_sandboxed", jailed)
    result = await runner.execute_action(
        input=MagicMock(),
        role=MagicMock(),
        resolved_context=MagicMock(),
    )
    assert result == backend
    assert direct.await_count == (backend == "direct")
    assert jailed.await_count == (backend == "nsjail")


@pytest.mark.anyio
async def test_python_script_does_not_fall_back_when_nsjail_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_service, "TRACECAT__EXECUTOR_BACKEND", "nsjail")
    service = SandboxService(cache_dir=str(tmp_path))
    monkeypatch.setattr(
        service, "_require_action_gateway_socket", lambda _: tmp_path / "gateway.sock"
    )
    jailed = AsyncMock(side_effect=SandboxInfrastructureError("nsjail unavailable"))
    monkeypatch.setattr(service, "_run_with_nsjail", jailed)
    direct = AsyncMock()
    monkeypatch.setattr(service, "_unsafe_pid_executor", direct)
    with pytest.raises(SandboxInfrastructureError, match="nsjail unavailable"):
        await service.run_python(script="def main(): return 1")
    direct.execute.assert_not_awaited()
