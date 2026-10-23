"""Warm action jail pool: matching, single use, capacity, and eviction."""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import tempfile
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic_core import PydanticSerializationError

from tracecat.executor import warm_jails as warm_jails_module
from tracecat.executor.registry_artifacts import RegistryArtifactCache
from tracecat.executor.warm_jails import WarmJailPool
from tracecat.sandbox import executor as executor_module
from tracecat.sandbox.exceptions import SandboxValidationError
from tracecat.sandbox.executor import (
    WARM_HANDOVER_ENV_VAR,
    ActionSandboxConfig,
    NsjailExecutor,
)
from tracecat.sandbox.nsjail_protocol import NsjailProcess

HANDOVER_ENV = {
    "TRACECAT__WORKSPACE_ID": "workspace-a",
    "TRACECAT__EXECUTOR_TOKEN": "token-a",
}


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdin = None
        self.pid = -1


class FakeRegistryArtifacts:
    def __init__(self, paths: list[Path]) -> None:
        self.paths = paths
        self.active_leases = 0
        self.pressure_listeners: list[Callable[[], None]] = []

    def add_lease_pressure_listener(self, listener: Callable[[], None]) -> None:
        self.pressure_listeners.append(listener)

    @asynccontextmanager
    async def lease(
        self, artifact_uris: list[str] | None
    ) -> AsyncGenerator[list[Path]]:
        self.active_leases += 1
        try:
            yield self.paths
        finally:
            self.active_leases -= 1


def _config(registry_path: Path, **overrides: Any) -> ActionSandboxConfig:
    config = ActionSandboxConfig(
        registry_paths=[registry_path],
        tracecat_app_dir=registry_path,
        env_vars={"TRACECAT__API_URL": "http://api:8000"},
        handover_env=dict(HANDOVER_ENV),
        timeout_seconds=360,
    )
    return dataclasses.replace(config, **overrides)


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[FakeProcess]:
    """Replace jail launches with fake processes; record kills."""
    processes: list[FakeProcess] = []

    async def spawn_action(
        _self: NsjailExecutor,
        job_dir: Path,
        config: ActionSandboxConfig,
        *,
        idle_seconds: float,
    ) -> NsjailProcess:
        process = FakeProcess()
        processes.append(process)
        return NsjailProcess(
            process=cast(asyncio.subprocess.Process, process),
            config_path=job_dir / "nsjail.cfg",
            launcher_path=None,
            log_file=tempfile.TemporaryFile(),
            workload_started_marker=None,
        )

    async def kill(process: FakeProcess) -> None:
        process.returncode = -9

    monkeypatch.setattr(NsjailExecutor, "spawn_action", spawn_action)
    monkeypatch.setattr(warm_jails_module, "sandbox_cgroup", lambda: None)
    monkeypatch.setattr(warm_jails_module, "_kill_jail_process", kill)
    return processes


def _pool(
    registry_path: Path, *, max_jails: int = 2, idle_seconds: float = 60
) -> tuple[WarmJailPool, FakeRegistryArtifacts]:
    artifacts = FakeRegistryArtifacts([registry_path])
    pool = WarmJailPool(
        cast(RegistryArtifactCache, artifacts),
        max_jails=max_jails,
        idle_seconds=idle_seconds,
    )
    return pool, artifacts


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.anyio
async def test_jail_is_handed_out_once_and_only_for_its_config(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    """A jail matches every config field except the per-action environment,
    and a claimed jail never serves a second action."""
    pool, _ = _pool(tmp_path)
    config = _config(tmp_path)
    pool.replenish(["s3://registry/a.tar.gz"], config)
    await _settle()

    other_registry = tmp_path / "other"
    assert pool.claim(_config(other_registry)) is None
    assert pool.claim(_config(tmp_path, timeout_seconds=30)) is None

    other_action = _config(
        tmp_path, handover_env={"TRACECAT__EXECUTOR_TOKEN": "token-b"}
    )
    jail = pool.claim(other_action)
    assert jail is not None
    assert jail.config.handover_env == {}
    assert pool.claim(other_action) is None

    jail.released.set()
    await pool.close()


@pytest.mark.anyio
async def test_dead_idle_jail_is_discarded_not_handed_out(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    pool, artifacts = _pool(tmp_path)
    config = _config(tmp_path)
    pool.replenish([], config)
    await _settle()

    spawned[0].returncode = 1
    assert pool.claim(config) is None
    await _settle()
    assert artifacts.active_leases == 0
    await pool.close()


@pytest.mark.anyio
async def test_pool_caps_unclaimed_jails(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    """A burst grows the pool only up to its cap; claimed jails free a slot."""
    pool, _ = _pool(tmp_path, max_jails=2)
    config = _config(tmp_path)
    for _ in range(5):
        pool.replenish([], config)
    await _settle()
    assert len(spawned) == 2

    jail = pool.claim(config)
    assert jail is not None
    pool.replenish([], config)
    await _settle()
    assert len(spawned) == 3

    jail.released.set()
    await pool.close()


@pytest.mark.anyio
async def test_idle_jail_is_evicted_and_lease_released(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    pool, artifacts = _pool(tmp_path, idle_seconds=0.05)
    config = _config(tmp_path)
    pool.replenish([], config)
    await _settle()
    assert artifacts.active_leases == 1

    await asyncio.sleep(0.2)
    assert spawned[0].returncode == -9
    assert artifacts.active_leases == 0
    assert pool.claim(config) is None
    await pool.close()


@pytest.mark.anyio
async def test_claimed_jail_keeps_its_lease_until_released(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    """The owner must not release the registry lease while the action runs,
    even past the idle timeout."""
    pool, artifacts = _pool(tmp_path, idle_seconds=0.05)
    config = _config(tmp_path)
    pool.replenish([], config)
    await _settle()
    jail = pool.claim(config)
    assert jail is not None

    await asyncio.sleep(0.2)
    assert artifacts.active_leases == 1
    assert spawned[0].returncode is None

    jail.released.set()
    await _settle()
    assert artifacts.active_leases == 0
    await pool.close()


@pytest.mark.anyio
async def test_close_discards_idle_jails_and_stops_refills(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    pool, artifacts = _pool(tmp_path)
    config = _config(tmp_path)
    pool.replenish([], config)
    await _settle()

    await pool.close()
    assert spawned[0].returncode == -9
    assert artifacts.active_leases == 0
    pool.replenish([], config)
    await _settle()
    assert len(spawned) == 1


@pytest.mark.anyio
async def test_mismatched_lease_paths_spawn_nothing(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    pool, _ = _pool(tmp_path / "rematerialized")
    pool.replenish(["s3://registry/a.tar.gz"], _config(tmp_path))
    await _settle()
    assert spawned == []
    await pool.close()


@pytest.mark.anyio
async def test_unserializable_payload_kills_the_claimed_jail(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    """A payload that fails to serialize must not strand its claimed jail."""
    pool, artifacts = _pool(tmp_path)
    config = _config(tmp_path)
    pool.replenish([], config)
    await _settle()
    jail = pool.claim(config)
    assert jail is not None

    with pytest.raises(PydanticSerializationError):
        await pool.run(jail, {"value": object()})
    await _settle()

    assert spawned[0].returncode == -9
    assert artifacts.active_leases == 0
    await asyncio.wait_for(pool.close(), 1)


@pytest.mark.anyio
async def test_cache_lease_pressure_evicts_idle_jails(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    """An idle jail's lease must not block another registry's admission."""
    pool, artifacts = _pool(tmp_path)
    config = _config(tmp_path)
    pool.replenish([], config)
    await _settle()
    assert artifacts.active_leases == 1

    for listener in artifacts.pressure_listeners:
        listener()
    await _settle()

    assert spawned[0].returncode == -9
    assert artifacts.active_leases == 0
    assert pool.claim(config) is None
    await pool.close()


@pytest.mark.anyio
async def test_cancelled_owner_kills_its_unclaimed_jail(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    pool, artifacts = _pool(tmp_path)
    config = _config(tmp_path)
    pool.replenish([], config)
    await _settle()

    for owner in list(pool._owners):
        owner.cancel()
    await _settle()

    assert spawned[0].returncode == -9
    assert artifacts.active_leases == 0
    assert pool.claim(config) is None


@pytest.mark.anyio
async def test_kill_drains_output_written_before_hand_over() -> None:
    """asyncio reports the exit only once the pipes close, so a jail that
    filled its pipes before hand-over must still be reaped."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys, time; sys.stdout.write('x' * 1_000_000); sys.stdout.flush(); time.sleep(60)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    await asyncio.sleep(0.5)

    await asyncio.wait_for(warm_jails_module._kill_jail_process(process), 10)

    assert process.returncode is not None


def test_claim_rejects_handover_env_the_cold_path_rejects(tmp_path: Path) -> None:
    pool, _ = _pool(tmp_path)
    config = _config(tmp_path, handover_env={WARM_HANDOVER_ENV_VAR: "0"})

    with pytest.raises(SandboxValidationError):
        pool.claim(config)


def test_warm_env_withholds_per_action_values(tmp_path: Path) -> None:
    """A pre-spawned jail starts without the token or run identifiers; a cold
    jail gets the same values in its environment."""
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    config = _config(tmp_path)

    warm_env = executor._build_action_env_map(config, warm=True)
    cold_env = executor._build_action_env_map(config)

    assert warm_env[WARM_HANDOVER_ENV_VAR] == "1"
    assert HANDOVER_ENV.keys().isdisjoint(warm_env)
    assert WARM_HANDOVER_ENV_VAR not in cold_env
    assert cold_env.items() >= HANDOVER_ENV.items()
    assert {k: v for k, v in cold_env.items() if k not in HANDOVER_ENV} == {
        k: v for k, v in warm_env.items() if k != WARM_HANDOVER_ENV_VAR
    }


@pytest.mark.anyio
async def test_spawned_jail_time_limit_covers_idle_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def start_nsjail(**kwargs: Any) -> NsjailProcess:
        captured.update(kwargs)
        return cast(NsjailProcess, None)

    monkeypatch.setattr(executor_module, "start_nsjail", start_nsjail)
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    await executor.spawn_action(
        job_dir, _config(tmp_path, network=None), idle_seconds=60
    )

    assert "time_limit: 420" in captured["config_text"]
    assert captured["stdin_pipe"] is True
    assert "TRACECAT__EXECUTOR_TOKEN" not in captured["env"]
