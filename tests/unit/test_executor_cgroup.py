"""Action and Run Python cgroup contracts."""

from pathlib import Path

import pytest

from tracecat.agent.sandbox.config import AgentResourceLimits
from tracecat.sandbox.cgroup import CGROUP_PATH_ENV
from tracecat.sandbox.exceptions import SandboxValidationError
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.service import SandboxService
from tracecat.sandbox.types import ResourceLimits, SandboxConfig, nsjail_rlimit_as


@pytest.mark.parametrize(
    "address_space_mb,cgroup_memory_limit,expected",
    [(None, True, None), (None, False, 642), (2048, True, 2048), (2048, False, 2048)],
)
@pytest.mark.parametrize("limits_cls", [ResourceLimits, AgentResourceLimits])
def test_address_space_cap_is_uncapped_only_under_cgroup(
    limits_cls: type[ResourceLimits] | type[AgentResourceLimits],
    address_space_mb: int | None,
    cgroup_memory_limit: bool,
    expected: int | None,
) -> None:
    """Invariant: the cgroup bounds RAM, so an unset rlimit_as stays uncapped there.

    Without a cgroup, rlimit_as is the only memory guard and falls back to twice
    the memory budget. An explicit cap applies either way.
    """
    limits = limits_cls(memory_mb=321, address_space_mb=address_space_mb)
    assert (
        limits.address_space_limit_mb(cgroup_memory_limit=cgroup_memory_limit)
        == expected
    )


def test_nsjail_rlimit_as_writes_infinity_explicitly() -> None:
    """Invariant: uncapped is explicit; an omitted rlimit_as means nsjail's 4096 MiB."""
    assert nsjail_rlimit_as(None) == "rlimit_as_type: INF"
    assert nsjail_rlimit_as(2048) == "rlimit_as: 2048"


@pytest.mark.parametrize(
    "address_space_mb,expected",
    [(None, "rlimit_as_type: INF"), (2048, "rlimit_as: 2048")],
)
def test_memory_budget_is_shared_across_all_execution_phases(
    tmp_path: Path, address_space_mb: int | None, expected: str
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
        assert expected in text.splitlines()
        assert (address_space_mb is None) == ("rlimit_as:" not in text)


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
