"""Reject invalid sandbox deployments before either worker starts services."""

import shutil
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
    for directory in ("usr/local/bin", "lib", "bin", "etc"):
        (rootfs / directory).mkdir(parents=True)
    python = rootfs / "usr/local/bin/python3.12"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    python.with_name("python3").symlink_to(python.name)
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
    ("relative_path", "invalid"),
    [
        ("nsjail", "missing"),
        ("nsjail", "directory"),
        ("nsjail", "not_executable"),
        ("rootfs", "missing"),
        ("rootfs", "file"),
        ("rootfs", "directory"),
        ("rootfs/usr", "missing"),
        ("rootfs/usr", "file"),
        ("rootfs/lib", "missing"),
        ("rootfs/lib", "file"),
        ("rootfs/bin", "missing"),
        ("rootfs/bin", "file"),
        ("rootfs/etc", "missing"),
        ("rootfs/etc", "file"),
        ("rootfs/usr/local/bin/python3", "missing"),
        ("rootfs/usr/local/bin/python3", "directory"),
        ("rootfs/usr/local/bin/python3", "not_executable"),
        ("rootfs/usr/local/bin/python3.12", "missing"),
    ],
)
async def test_workers_reject_invalid_sandbox_before_starting_services(
    module: ModuleType,
    relative_path: str,
    invalid: str,
    tmp_path: Path,
    sandbox_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = tmp_path / "ready"
    ready.touch()
    monkeypatch.setattr("tracecat.executor.startup.WORKER_READY_FILE", ready)
    path = tmp_path / relative_path
    if invalid == "not_executable":
        path.chmod(0o644)
    else:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        if invalid == "directory":
            path.mkdir()
        elif invalid == "file":
            path.write_text("not a directory")
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
    assert not ready.exists()
