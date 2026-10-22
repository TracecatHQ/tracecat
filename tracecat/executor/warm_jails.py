"""Pre-spawned, single-use nsjail jails for sandboxed actions.

A warm jail is launched with exactly the configuration a cold action jail
would get, minus its per-action environment, and has already started Python
and imported the registry stack by the time an action claims it. The claimer
hands over one payload on stdin; the jail runs that action and exits. A jail
never serves a second action.

The pool replaces what it hands out: every sandboxed action asks for one jail
matching its configuration, up to a process-wide cap. A miss, a dead jail, or
a full pool falls back to a cold jail.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tracecat.executor import minimal_runner as minimal_runner_module
from tracecat.executor.registry_artifacts import RegistryArtifactCache
from tracecat.logger import logger
from tracecat.sandbox.cgroup import sandbox_cgroup
from tracecat.sandbox.executor import (
    ActionSandboxConfig,
    NsjailExecutor,
    validate_action_env,
)
from tracecat.sandbox.nsjail_protocol import NsjailProcess, close_nsjail_files
from tracecat.sandbox.types import SandboxResult
from tracecat.sandbox.utils import terminate_process_group


@dataclass(eq=False, slots=True)
class WarmJail:
    """One launched jail waiting for its only action."""

    config: ActionSandboxConfig
    """The jail's configuration, with an empty ``handover_env``."""
    executor: NsjailExecutor
    job_dir: Path
    handle: NsjailProcess
    released: asyncio.Event = field(default_factory=asyncio.Event)
    """Set when the claimer is done with the jail, or to evict an idle one."""
    claimed: bool = False


def _without_handover_env(config: ActionSandboxConfig) -> ActionSandboxConfig:
    return dataclasses.replace(config, handover_env={})


class WarmJailPool:
    """Keeps a few launched action jails ready for hand-over.

    Each jail belongs to an owner task that holds its registry lease and job
    directory until the jail is either evicted or its claimer is done.
    """

    def __init__(
        self,
        registry_artifacts: RegistryArtifactCache,
        *,
        max_jails: int,
        idle_seconds: float,
    ) -> None:
        self._registry_artifacts = registry_artifacts
        self._max_jails = max_jails
        self._idle_seconds = idle_seconds
        self._idle: list[WarmJail] = []
        self._unclaimed = 0
        """Owners whose jail is starting or idle; the cap applies to these."""
        self._owners: set[asyncio.Task[None]] = set()
        self._closed = False

    def claim(self, config: ActionSandboxConfig) -> WarmJail | None:
        """Take a live idle jail whose configuration matches ``config``.

        Matching compares every ``ActionSandboxConfig`` field except
        ``handover_env``, so a jail is only ever handed an action it was
        configured for. ``handover_env`` gets the cold path's key checks.

        Raises:
            SandboxValidationError: If ``handover_env`` has a disallowed key.
        """
        validate_action_env(config.handover_env)
        spec = _without_handover_env(config)
        for jail in list(self._idle):
            if jail.config != spec:
                continue
            self._idle.remove(jail)
            if jail.handle.process.returncode is not None:
                # Died while idle; its owner discards it.
                jail.released.set()
                continue
            jail.claimed = True
            self._unclaimed -= 1
            return jail
        return None

    def replenish(
        self, artifact_uris: list[str] | None, config: ActionSandboxConfig
    ) -> None:
        """Start one jail for ``config`` in the background, if under the cap."""
        if self._closed or self._unclaimed >= self._max_jails:
            return
        self._unclaimed += 1
        task = asyncio.create_task(
            self._own(list(artifact_uris or []), _without_handover_env(config))
        )
        self._owners.add(task)
        task.add_done_callback(self._owners.discard)

    async def run(self, jail: WarmJail, payload: bytes) -> SandboxResult:
        """Hand ``payload`` to a claimed jail and return its result."""
        try:
            return await jail.executor.handover_action(
                jail.handle, jail.job_dir, jail.config, payload
            )
        finally:
            jail.released.set()

    async def close(self) -> None:
        """Discard idle jails and wait for every owner to finish."""
        self._closed = True
        for jail in self._idle:
            jail.released.set()
        self._idle.clear()
        if self._owners:
            await asyncio.gather(*self._owners, return_exceptions=True)

    async def _own(self, artifact_uris: list[str], config: ActionSandboxConfig) -> None:
        jail: WarmJail | None = None
        try:
            async with self._registry_artifacts.lease(artifact_uris) as registry_paths:
                # The claimer's config names the paths it leased; a jail must
                # mount the same ones.
                if registry_paths != config.registry_paths:
                    return
                job_dir = Path(tempfile.mkdtemp(prefix="tracecat_action_"))
                try:
                    shutil.copy2(
                        minimal_runner_module.__file__,
                        job_dir / "minimal_runner.py",
                    )
                    executor = NsjailExecutor(cgroup_mount=sandbox_cgroup())
                    handle = await executor.spawn_action(
                        job_dir, config, idle_seconds=self._idle_seconds
                    )
                    jail = WarmJail(
                        config=config,
                        executor=executor,
                        job_dir=job_dir,
                        handle=handle,
                    )
                    if self._closed:
                        await _discard(jail)
                        return
                    self._idle.append(jail)
                    await self._hold(jail)
                finally:
                    shutil.rmtree(job_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(
                "Warm action jail failed; actions fall back to cold jails",
                error_type=type(e).__name__,
            )
        finally:
            if jail is None or not jail.claimed:
                self._unclaimed -= 1

    async def _hold(self, jail: WarmJail) -> None:
        """Keep the lease and job directory until the jail is evicted or done."""
        try:
            await asyncio.wait_for(jail.released.wait(), self._idle_seconds)
        except TimeoutError:
            pass
        if jail.claimed:
            # The claimed action still imports from this lease.
            await jail.released.wait()
            return
        if jail in self._idle:
            self._idle.remove(jail)
        await _discard(jail)


async def _discard(jail: WarmJail) -> None:
    """Kill an unclaimed jail and release its executor-owned files."""
    process = jail.handle.process
    if process.stdin is not None:
        process.stdin.close()
    try:
        await terminate_process_group(process)
    finally:
        close_nsjail_files(jail.handle)
