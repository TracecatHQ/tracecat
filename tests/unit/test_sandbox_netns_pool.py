"""Leased action network namespaces: pool accounting and jail configuration."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import NoReturn, cast

import pytest

from tracecat.sandbox import executor as executor_module
from tracecat.sandbox import netns_pool
from tracecat.sandbox.executor import (
    _WORKLOAD_STARTED_MARKER,
    ActionSandboxConfig,
    NsjailExecutor,
)
from tracecat.sandbox.netns_pool import ActionNetnsPool, SharedNetns, _Holder
from tracecat.sandbox.nsjail_protocol import NsjailCompletedProcess

SHARED = SharedNetns(
    user_ns_fd=101,
    net_ns_fd=102,
    inside_id=1000,
    dns_mount_lines=('mount { src: "/holder/resolv.conf" dst: "/etc/resolv.conf" }',),
)


def _executor(tmp_path: Path) -> NsjailExecutor:
    return NsjailExecutor(
        nsjail_path=str(tmp_path / "nsjail"),
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "cache"),
    )


def _action_config(tmp_path: Path) -> ActionSandboxConfig:
    return ActionSandboxConfig(registry_paths=[], tracecat_app_dir=tmp_path)


def test_shared_netns_jail_joins_the_holder_instead_of_creating_a_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leased jail gets no NSTUN of its own, only the holder's resolvers.

    It maps its user onto the executor's uid inside the holder's user
    namespace, where nsjail runs after nsenter.
    """

    def no_network_plan(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("a leased jail must not build its own network")

    monkeypatch.setattr(
        executor_module, "resolve_sandbox_network_plan", no_network_plan
    )
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    config_text = _executor(tmp_path)._build_action_config(
        job_dir, _action_config(tmp_path), SHARED
    )

    assert "clone_newnet: false" in config_text
    assert "user_net" not in config_text
    assert SHARED.dns_mount_lines[0] in config_text
    assert 'uidmap { inside_id: "1000" outside_id: "1000" count: 1 }' in config_text
    assert 'gidmap { inside_id: "1000" outside_id: "1000" count: 1 }' in config_text


def test_unleased_jail_keeps_its_own_network_and_host_uid(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    config_text = _executor(tmp_path)._build_action_config(
        job_dir, _action_config(tmp_path)
    )

    assert "clone_newnet: true" in config_text
    assert f'outside_id: "{os.getuid()}"' in config_text


@pytest.mark.anyio
async def test_leased_action_runs_nsjail_through_nsenter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_invoke_nsjail(**kwargs: object) -> NsjailCompletedProcess:
        captured.update(kwargs)
        return NsjailCompletedProcess(
            returncode=1,
            stdout=b"",
            stderr=b"",
            workload_started=True,
            nsjail_log=b"",
            nsjail_signaled=False,
        )

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    await _executor(tmp_path).execute_action(job_dir, _action_config(tmp_path), SHARED)

    assert captured["command_prefix"] == [
        "/usr/bin/nsenter",
        "--preserve-credentials",
        "--user=/proc/self/fd/101",
        "--net=/proc/self/fd/102",
        "--",
    ]
    assert captured["extra_pass_fds"] == (101, 102)
    assert captured["workload_started_marker"] == _WORKLOAD_STARTED_MARKER


# --- Pool accounting ---


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _process(holder: _Holder) -> _FakeProcess:
    return cast(_FakeProcess, holder.process)


def _fake_holder(tmp_path: Path, index: int) -> _Holder:
    holder_dir = tmp_path / f"holder-{index}"
    holder_dir.mkdir()
    return _Holder(
        process=cast(asyncio.subprocess.Process, _FakeProcess()),
        jailed_pid=os.getpid(),
        holder_dir=holder_dir,
        shared=SharedNetns(
            user_ns_fd=os.open(os.devnull, os.O_RDONLY),
            net_ns_fd=os.open(os.devnull, os.O_RDONLY),
            inside_id=1000,
            dns_mount_lines=(),
        ),
    )


def _pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[_Holder]:
    """Start fake holders; liveness follows only the fake nsjail process."""
    started: list[_Holder] = []

    async def start_holder(_self: ActionNetnsPool) -> _Holder:
        holder = _fake_holder(tmp_path, len(started))
        started.append(holder)
        return holder

    monkeypatch.setattr(ActionNetnsPool, "_start_holder", start_holder)
    monkeypatch.setattr(_Holder, "alive", lambda self: self.process.returncode is None)
    return started


@pytest.mark.anyio
async def test_released_holder_is_reused_by_the_next_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _pool(tmp_path, monkeypatch)
    pool = ActionNetnsPool(max_size=2)

    async with pool.lease() as first:
        pass
    async with pool.lease() as second:
        pass

    assert first is not None and second is first
    assert len(started) == 1
    await pool.aclose()


@pytest.mark.anyio
async def test_concurrent_leases_are_exclusive_and_full_pool_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two live jails never share a namespace; past max_size a jail gets None."""
    _pool(tmp_path, monkeypatch)
    pool = ActionNetnsPool(max_size=2)

    async with pool.lease() as a, pool.lease() as b, pool.lease() as c:
        assert a is not None and b is not None and a is not b
        assert c is None
    await pool.aclose()


@pytest.mark.anyio
async def test_dead_holder_is_discarded_and_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _pool(tmp_path, monkeypatch)
    pool = ActionNetnsPool(max_size=1)

    async with pool.lease():
        pass
    _process(started[0]).returncode = 1  # NSTUN's nsjail parent died while idle
    async with pool.lease() as replacement:
        assert replacement is started[1].shared

    assert not started[0].holder_dir.exists()
    await pool.aclose()


@pytest.mark.anyio
async def test_aclose_stops_idle_holders_and_late_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _pool(tmp_path, monkeypatch)
    pool = ActionNetnsPool(max_size=2)

    async with pool.lease():
        async with pool.lease():
            pass  # the inner holder goes idle
        await pool.aclose()
        assert _process(started[1]).terminated
        assert not _process(started[0]).terminated

    assert _process(started[0]).terminated
    async with pool.lease() as after_close:
        assert after_close is None


@pytest.mark.anyio
async def test_failed_holder_start_falls_back_and_cools_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken holder start costs one attempt, not one per action."""
    attempts = 0

    async def failing_start(_self: ActionNetnsPool) -> _Holder:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("synthetic holder failure")

    monkeypatch.setattr(ActionNetnsPool, "_start_holder", failing_start)
    pool = ActionNetnsPool(max_size=2)

    for _ in range(3):
        async with pool.lease() as shared:
            assert shared is None

    assert attempts == 1
    assert pool._size == 0


def test_pool_is_disabled_at_size_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netns_pool, "TRACECAT__SANDBOX_ACTION_NETNS_POOL_SIZE", 0)
    monkeypatch.setattr(netns_pool, "_pool", None)

    assert netns_pool.get_action_netns_pool() is None
