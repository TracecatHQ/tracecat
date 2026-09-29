"""Reject invalid sandbox deployments before either worker starts services."""

from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from tracecat import config
from tracecat.agent import executor_worker
from tracecat.executor import worker
from tracecat.executor.enums import ExecutorBackendType
from tracecat.executor.startup import validate_execution_backend


@pytest.fixture
def sandbox_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    nsjail = tmp_path / "nsjail"
    nsjail.write_text("#!/bin/sh\nexit 0\n")
    nsjail.chmod(0o755)
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    monkeypatch.setattr(config, "TRACECAT__SANDBOX_NSJAIL_PATH", str(nsjail))
    monkeypatch.setattr(config, "TRACECAT__SANDBOX_ROOTFS_PATH", str(rootfs))
    return nsjail, rootfs


@pytest.mark.parametrize("backend", ["nsjail", "ephemeral"])
def test_valid_sandbox_prerequisites(
    backend: str,
    sandbox_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.from_config(backend)
    )
    validate_execution_backend()


@pytest.mark.parametrize("backend", ["direct"])
def test_other_backends_do_not_require_sandbox(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.from_config(backend)
    )
    monkeypatch.setattr(
        config, "TRACECAT__SANDBOX_NSJAIL_PATH", str(tmp_path / "missing")
    )
    monkeypatch.setattr(
        config, "TRACECAT__SANDBOX_ROOTFS_PATH", str(tmp_path / "missing")
    )
    validate_execution_backend()


@pytest.mark.anyio
@pytest.mark.parametrize("module", [worker, executor_worker], ids=["executor", "agent"])
@pytest.mark.parametrize(
    "invalid",
    [
        "missing_binary",
        "binary_directory",
        "not_executable",
        "missing_rootfs",
        "rootfs_file",
    ],
)
async def test_workers_reject_invalid_sandbox_before_starting_services(
    module: ModuleType,
    invalid: str,
    sandbox_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nsjail, rootfs = sandbox_paths
    if invalid == "missing_binary":
        nsjail.unlink()
    elif invalid == "binary_directory":
        nsjail.unlink()
        nsjail.mkdir()
    elif invalid == "not_executable":
        nsjail.chmod(0o644)
    elif invalid == "missing_rootfs":
        rootfs.rmdir()
    else:
        rootfs.rmdir()
        rootfs.write_text("not a directory")
    monkeypatch.setattr(
        config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.NSJAIL
    )
    initialize_tracing = Mock()
    monkeypatch.setattr(module, "initialize_platform_tracing", initialize_tracing)
    with pytest.raises(
        RuntimeError, match="TRACECAT__EXECUTOR_BACKEND=nsjail requires"
    ):
        await module.main()
    initialize_tracing.assert_not_called()
