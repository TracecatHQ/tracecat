"""Action and Run Python cgroup contracts."""

from pathlib import Path

import pytest

from tracecat.sandbox.cgroup import CGROUP_PATH_ENV
from tracecat.sandbox.exceptions import SandboxValidationError
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.service import SandboxService
from tracecat.sandbox.types import ResourceLimits, SandboxConfig


@pytest.mark.parametrize("address_space_mb,expected", [(None, 642), (2048, 2048)])
def test_memory_budget_is_shared_across_all_execution_phases(
    tmp_path: Path, address_space_mb: int | None, expected: int
) -> None:
    resources = ResourceLimits(memory_mb=321, address_space_mb=address_space_mb)
    executor = NsjailExecutor(cgroup_mount=tmp_path / "cgroup")
    configs = [
        executor._build_action_config(
            tmp_path,
            ActionSandboxConfig(
                registry_paths=[], tracecat_app_dir=tmp_path, resources=resources
            ),
        ),
        *[
            executor._build_config(tmp_path, phase, SandboxConfig(resources=resources))
            for phase in ("install", "execute")
        ],
    ]
    for text in configs:
        assert "use_cgroupv2: true" in text
        assert f'cgroupv2_mount: "{tmp_path}/cgroup"' in text
        assert f"cgroup_mem_max: {321 * 1024 * 1024}" in text
        assert "cgroup_mem_swap_max: 0" in text
        assert f"rlimit_as: {expected}" in text


def test_action_cgroup_path_cannot_inject_config(tmp_path: Path) -> None:
    executor = NsjailExecutor(cgroup_mount=Path('/cgroup"\nrlimit_as: 0'))
    with pytest.raises(SandboxValidationError):
        executor._build_action_config(
            tmp_path, ActionSandboxConfig(registry_paths=[], tracecat_app_dir=tmp_path)
        )


def test_run_python_requires_delegation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CGROUP_PATH_ENV, raising=False)
    service = SandboxService(cache_dir=str(tmp_path / "cache"))
    with pytest.raises(RuntimeError, match="requires cgroup v2 delegation"):
        _ = service.nsjail_executor

    (tmp_path / "cgroup.subtree_control").write_text("memory")
    (tmp_path / "cgroup.procs").touch()
    monkeypatch.setenv(CGROUP_PATH_ENV, str(tmp_path))
    assert service.nsjail_executor.cgroup_mount == tmp_path


@pytest.mark.parametrize("memory_mb", [0, -1])
def test_memory_budget_must_be_positive(memory_mb: int) -> None:
    with pytest.raises(ValueError, match="memory_mb must be positive"):
        ResourceLimits(memory_mb=memory_mb)
