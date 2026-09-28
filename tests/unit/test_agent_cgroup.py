"""The worker must reject missing delegation before accepting activities."""

from pathlib import Path

import pytest

from tracecat.agent import executor_worker
from tracecat.agent.sandbox.cgroup import CGROUP_PATH_ENV, sandbox_cgroup


def test_missing_delegation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CGROUP_PATH_ENV, raising=False)
    with pytest.raises(RuntimeError, match="requires cgroup v2 delegation"):
        sandbox_cgroup()


@pytest.mark.anyio
async def test_worker_rejects_missing_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor_worker.config, "TRACECAT__DISABLE_NSJAIL", False)
    monkeypatch.delenv(CGROUP_PATH_ENV, raising=False)
    with pytest.raises(RuntimeError, match="requires cgroup v2 delegation"):
        await executor_worker.main()


def test_memory_controller_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CGROUP_PATH_ENV, str(tmp_path))
    (tmp_path / "cgroup.subtree_control").write_text("cpu pids")
    with pytest.raises(RuntimeError, match="Memory controller is not enabled"):
        sandbox_cgroup()
