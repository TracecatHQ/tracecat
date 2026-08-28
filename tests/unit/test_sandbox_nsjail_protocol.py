"""Host-side nsjail setup and launch attribution tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracecat.sandbox import executor as executor_module
from tracecat.sandbox import nsjail_protocol
from tracecat.sandbox.exceptions import (
    SandboxInfrastructureError,
    SandboxTimeoutError,
)
from tracecat.sandbox.executor import (
    _WORKLOAD_STARTED_SENTINEL,
    ActionSandboxConfig,
    NsjailExecutor,
)
from tracecat.sandbox.nsjail_protocol import NsjailCompletedProcess, invoke_nsjail
from tracecat.sandbox.types import SandboxErrorCode


@pytest.mark.anyio
async def test_missing_nsjail_binary_is_infrastructure_failure(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    executor = NsjailExecutor(
        nsjail_path=str(tmp_path / "missing-nsjail"),
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "cache"),
    )

    with pytest.raises(SandboxInfrastructureError) as exc_info:
        await executor.execute_action(
            job_dir,
            ActionSandboxConfig(
                registry_paths=[],
                tracecat_app_dir=tmp_path,
                network=None,
            ),
        )

    assert isinstance(exc_info.value.__cause__, FileNotFoundError)
    assert not (job_dir / "nsjail.cfg").exists()


@pytest.mark.anyio
async def test_executor_owned_file_write_is_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write_text = Path.write_text

    def fail_config_write(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if path.name == "nsjail.cfg":
            raise OSError("synthetic host filesystem failure")
        return original_write_text(
            path,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", fail_config_write)

    with pytest.raises(SandboxInfrastructureError) as exc_info:
        await invoke_nsjail(
            nsjail_path=tmp_path / "missing-nsjail",
            job_dir=tmp_path,
            config_text="mode: ONCE",
            env={},
            timeout_seconds=1,
            timeout_message="synthetic timeout",
        )

    assert isinstance(exc_info.value.__cause__, OSError)
    assert not (tmp_path / "nsjail.cfg").exists()


@pytest.mark.anyio
async def test_timeout_is_reported_and_config_cleaned_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(
        nsjail_protocol,
        "communicate_process_group",
        raise_timeout,
    )

    with pytest.raises(SandboxTimeoutError, match="synthetic timeout"):
        await invoke_nsjail(
            nsjail_path=Path("/bin/sh"),
            job_dir=tmp_path,
            config_text="mode: ONCE",
            env={},
            timeout_seconds=1,
            timeout_message="synthetic timeout",
        )

    assert not (tmp_path / "nsjail.cfg").exists()


@pytest.mark.parametrize(
    ("write_sentinel", "expected_code"),
    [
        pytest.param(
            False,
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            id="launch-failure-before-workload-start",
        ),
        pytest.param(
            True,
            SandboxErrorCode.WORKLOAD_FAILURE,
            id="workload-exit-255-after-start",
        ),
    ],
)
@pytest.mark.anyio
async def test_workload_start_sentinel_attributes_exit_255(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_sentinel: bool,
    expected_code: SandboxErrorCode,
) -> None:
    """The in-jail sentinel file, not the exit code, proves the workload ran."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    async def fake_invoke_nsjail(**kwargs: object) -> NsjailCompletedProcess:
        if write_sentinel:
            (job_dir / _WORKLOAD_STARTED_SENTINEL).touch()
        return NsjailCompletedProcess(returncode=0xFF, stdout=b"", stderr=b"")

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)

    executor = NsjailExecutor(
        nsjail_path=str(tmp_path / "nsjail"),
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "cache"),
    )
    result = await executor.execute_action(
        job_dir,
        ActionSandboxConfig(
            registry_paths=[],
            tracecat_app_dir=tmp_path,
            network=None,
        ),
    )

    assert result.success is False
    assert result.error_code is expected_code
