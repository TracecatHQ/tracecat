"""Host-side nsjail setup and launch attribution tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracecat.sandbox.exceptions import SandboxInfrastructureError
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.nsjail_protocol import invoke_nsjail


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


@pytest.mark.parametrize("failed_file", ["launcher.py", "nsjail.cfg"])
@pytest.mark.anyio
async def test_executor_owned_file_write_is_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_file: str,
) -> None:
    original_write_text = Path.write_text

    def fail_selected_write(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if path.name == failed_file:
            raise OSError("synthetic host filesystem failure")
        return original_write_text(
            path,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", fail_selected_write)

    with pytest.raises(SandboxInfrastructureError) as exc_info:
        await invoke_nsjail(
            nsjail_path=tmp_path / "missing-nsjail",
            job_dir=tmp_path,
            config_text="mode: ONCE",
            env={},
            timeout_seconds=1,
            timeout_message="synthetic timeout",
            workload_launcher_name="launcher.py",
            workload_launcher_script="pass",
        )

    assert isinstance(exc_info.value.__cause__, OSError)
    assert not (tmp_path / "nsjail.cfg").exists()
