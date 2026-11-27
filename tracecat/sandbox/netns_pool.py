"""Long-lived network namespaces that action sandboxes lease instead of creating.

Each action jail used to create its own network namespace with an NSTUN TUN
device. Unregistering that device when the jail exits waits for RCU grace
periods: ~120 ms per action on staging, plus a slower cgroup setup for the next
jail. A holder is one long-lived nsjail process with the default action network
policy; a jail leases a holder exclusively, joins its network namespace with
``clone_newnet: false``, and returns it on exit. Everything else in the jail
(user, mount, PID, IPC and UTS namespaces, ``/tmp``, processes) stays fresh per
action. A jail's sockets die with its PID namespace, so a released namespace
carries no state the next lessee can reach beyond kernel TIME_WAIT entries.

The executor runs without capabilities, so it cannot join a network namespace
directly. It owns the holder's user namespace, though, so it joins that first
and then the network namespace it owns (``nsenter --preserve-credentials``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

from tracecat.config import (
    TRACECAT__SANDBOX_ACTION_NETNS_POOL_SIZE,
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)
from tracecat.logger import logger
from tracecat.sandbox.networking import resolve_sandbox_network_plan
from tracecat.sandbox.types import SandboxNetworkPurpose, SandboxNetworkRequest

NSENTER_PATH = "/usr/bin/nsenter"
HOLDER_INSIDE_ID = 1000
_HOLDER_START_TIMEOUT_SECONDS = 5.0
_HOLDER_START_POLL_SECONDS = 0.005
# After a holder fails to start, actions use fresh namespaces for this long
# rather than paying a failed start on every call.
_HOLDER_START_COOLDOWN_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class SharedNetns:
    """A leased network namespace for one action jail.

    Attributes:
        user_ns_fd: Open descriptor of the holder's user namespace.
        net_ns_fd: Open descriptor of the holder's network namespace.
        inside_id: The executor's uid and gid inside the holder's user
            namespace; the jail maps its own user onto these.
        dns_mount_lines: Resolver mounts matching the holder's NSTUN DNS rules.
    """

    user_ns_fd: int
    net_ns_fd: int
    inside_id: int
    dns_mount_lines: tuple[str, ...]

    def command_prefix(self) -> list[str]:
        """Return the argv prefix that starts a command inside the namespace.

        The descriptors must be passed to the child process (``pass_fds``).
        """
        return [
            NSENTER_PATH,
            "--preserve-credentials",
            f"--user=/proc/self/fd/{self.user_ns_fd}",
            f"--net=/proc/self/fd/{self.net_ns_fd}",
            "--",
        ]

    def pass_fds(self) -> tuple[int, int]:
        """Descriptors the nsenter child needs."""
        return (self.user_ns_fd, self.net_ns_fd)


@dataclass(slots=True)
class _Holder:
    """One long-lived nsjail process that keeps a network namespace alive."""

    process: asyncio.subprocess.Process
    jailed_pid: int
    holder_dir: Path
    shared: SharedNetns

    def alive(self) -> bool:
        """Whether NSTUN (in the nsjail parent) and the jailed process still run."""
        return (
            self.process.returncode is None
            and Path(f"/proc/{self.jailed_pid}").exists()
        )

    async def close(self) -> None:
        """Stop the holder and release its namespace descriptors and files."""
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        for fd in self.shared.pass_fds():
            with contextlib.suppress(OSError):
                os.close(fd)
        shutil.rmtree(self.holder_dir, ignore_errors=True)


def _holder_config(rootfs: Path, holder_dir: Path) -> tuple[str, tuple[str, ...]]:
    """Return the holder's nsjail config and the resolver mounts jails must reuse."""
    plan = resolve_sandbox_network_plan(
        holder_dir, SandboxNetworkRequest(SandboxNetworkPurpose.ACTION)
    )
    lines = [
        'name: "action_netns_holder"',
        "mode: ONCE",
        'hostname: "sandbox"',
        "keep_env: false",
        "clone_newnet: true",
        "clone_newuser: true",
        "clone_newns: true",
        "clone_newpid: true",
        "clone_newipc: true",
        "clone_newuts: true",
        f'uidmap {{ inside_id: "{HOLDER_INSIDE_ID}" outside_id: "{os.getuid()}" count: 1 }}',
        f'gidmap {{ inside_id: "{HOLDER_INSIDE_ID}" outside_id: "{os.getgid()}" count: 1 }}',
        *plan.user_net_lines,
        f'mount {{ src: "{rootfs}/usr" dst: "/usr" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/lib" dst: "/lib" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/bin" dst: "/bin" is_bind: true rw: false }}',
    ]
    if (rootfs / "lib64").exists():
        lines.append(
            f'mount {{ src: "{rootfs}/lib64" dst: "/lib64" is_bind: true rw: false }}'
        )
    lines.extend(
        [
            "time_limit: 0",
            "log_level: WARNING",
            'exec_bin { path: "/bin/sleep" arg: "infinity" }',
        ]
    )
    return "\n".join(lines), plan.dns_mount_lines


async def _jailed_child_pid(nsjail_pid: int) -> int | None:
    """Wait for nsjail to start its jailed child and return the child's pid."""
    children = Path(f"/proc/{nsjail_pid}/task/{nsjail_pid}/children")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _HOLDER_START_TIMEOUT_SECONDS
    while loop.time() < deadline:
        try:
            pids = children.read_text().split()
        except OSError:
            return None
        if pids:
            return int(pids[0])
        await asyncio.sleep(_HOLDER_START_POLL_SECONDS)
    return None


class ActionNetnsPool:
    """Exclusive leases on long-lived action network namespaces.

    Holders start on demand up to ``max_size``. When none is free and the pool is
    full, or a holder fails to start, the lease yields None and the caller falls
    back to a fresh per-jail network namespace.
    """

    def __init__(
        self,
        *,
        max_size: int = TRACECAT__SANDBOX_ACTION_NETNS_POOL_SIZE,
        nsjail_path: str = TRACECAT__SANDBOX_NSJAIL_PATH,
        rootfs_path: str = TRACECAT__SANDBOX_ROOTFS_PATH,
    ) -> None:
        self.max_size = max_size
        self.nsjail_path = Path(nsjail_path)
        self.rootfs = Path(rootfs_path)
        self._idle: list[_Holder] = []
        self._size = 0
        self._closed = False
        self._start_blocked_until = 0.0

    @contextlib.asynccontextmanager
    async def lease(self) -> AsyncGenerator[SharedNetns | None]:
        """Lease a namespace for one jail, or yield None to use a fresh one."""
        holder = await self._acquire()
        try:
            yield holder.shared if holder is not None else None
        finally:
            if holder is not None:
                await self._release(holder)

    async def _acquire(self) -> _Holder | None:
        while self._idle:
            holder = self._idle.pop()
            if holder.alive():
                return holder
            await self._discard(holder)
        loop = asyncio.get_running_loop()
        if (
            self._closed
            or self._size >= self.max_size
            or loop.time() < self._start_blocked_until
        ):
            return None
        # Reserve the slot before awaiting so concurrent leases respect max_size.
        self._size += 1
        try:
            return await self._start_holder()
        except Exception as exc:
            self._size -= 1
            self._start_blocked_until = loop.time() + _HOLDER_START_COOLDOWN_SECONDS
            logger.warning(
                "Could not start action netns holder; using fresh namespaces",
                error=str(exc),
                retry_after_seconds=_HOLDER_START_COOLDOWN_SECONDS,
            )
            return None

    async def _release(self, holder: _Holder) -> None:
        if self._closed or not holder.alive():
            await self._discard(holder)
            return
        self._idle.append(holder)

    async def _discard(self, holder: _Holder) -> None:
        self._size -= 1
        await holder.close()

    async def _start_holder(self) -> _Holder:
        if not Path(NSENTER_PATH).exists():
            raise RuntimeError(f"{NSENTER_PATH} is required to join a holder")
        holder_dir = Path(tempfile.mkdtemp(prefix="tracecat_netns_"))
        config_text, dns_mount_lines = _holder_config(self.rootfs, holder_dir)
        config_path = holder_dir / "nsjail.cfg"
        config_path.write_text(config_text)
        config_path.chmod(0o600)
        process = await asyncio.create_subprocess_exec(
            str(self.nsjail_path),
            "--config",
            str(config_path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
            start_new_session=True,
        )
        jailed_pid = await _jailed_child_pid(process.pid)
        fds: list[int] = []
        try:
            if jailed_pid is None:
                raise RuntimeError("nsjail did not start the holder process")
            for kind in ("user", "net"):
                fds.append(
                    os.open(f"/proc/{jailed_pid}/ns/{kind}", os.O_RDONLY | os.O_CLOEXEC)
                )
        except Exception:
            for fd in fds:
                os.close(fd)
            if process.returncode is None:
                process.kill()
            await process.wait()
            shutil.rmtree(holder_dir, ignore_errors=True)
            raise
        logger.info(
            "Started action netns holder", jailed_pid=jailed_pid, pool_size=self._size
        )
        return _Holder(
            process=process,
            jailed_pid=jailed_pid,
            holder_dir=holder_dir,
            shared=SharedNetns(
                user_ns_fd=fds[0],
                net_ns_fd=fds[1],
                inside_id=HOLDER_INSIDE_ID,
                dns_mount_lines=dns_mount_lines,
            ),
        )

    async def aclose(self) -> None:
        """Stop every idle holder; leased holders stop when they are released."""
        self._closed = True
        idle, self._idle = self._idle, []
        for holder in idle:
            await self._discard(holder)


_pool: ActionNetnsPool | None = None


def get_action_netns_pool() -> ActionNetnsPool | None:
    """Return the process-wide pool, or None when it is disabled."""
    global _pool
    if TRACECAT__SANDBOX_ACTION_NETNS_POOL_SIZE <= 0:
        return None
    if _pool is None:
        _pool = ActionNetnsPool()
    return _pool


async def close_action_netns_pool() -> None:
    """Stop the process-wide pool's holders, if it was started."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
