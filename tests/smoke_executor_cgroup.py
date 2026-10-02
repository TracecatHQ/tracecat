"""Real nsjail memory containment; run through the sandbox cgroup bootstrap."""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Literal

from tracecat.sandbox.cgroup import sandbox_cgroup
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.types import ResourceLimits, SandboxConfig, SandboxErrorCode

# Each process stays below the address-space cap; their combined resident memory
# exceeds memory.max. The child alone cannot exceed the cgroup budget.
OOM_SCRIPT = """
import os
import resource
import time
assert resource.getrlimit(resource.RLIMIT_AS) == (512 * 1024 * 1024,) * 2
child = os.fork()
if child == 0:
    data = bytearray(80 * 1024 * 1024)
    time.sleep(2)
    os._exit(0)
time.sleep(0.2)
data = bytearray(80 * 1024 * 1024)
_, status = os.waitpid(child, 0)
# If the kernel chose the child, propagate its signal as nsjail does.
if os.WIFSIGNALED(status):
    os._exit(128 + os.WTERMSIG(status))
raise AssertionError('aggregate memory limit did not kill either process')
"""
HEALTHY_SCRIPT = """
import json
import mmap
import time
from pathlib import Path
# Reserve more virtual memory than the 128 MiB resident-memory budget.
reservation = mmap.mmap(-1, 256 * 1024 * 1024)
time.sleep(1)
Path('/work/result.json').write_text(json.dumps(
    {'success': True, 'output': 42, 'result': 42}
))
"""


async def main() -> None:
    """Check aggregate OOM, sibling survival, supervisor survival, and reuse."""
    assert os.getuid() == 1001
    root = sandbox_cgroup()
    parent_group = Path("/proc/self/cgroup").read_text()
    initial_children = set(root.iterdir())
    executor = NsjailExecutor(cgroup_mount=root)
    resources = ResourceLimits(memory_mb=128, address_space_mb=512, timeout_seconds=15)

    def oom_kills() -> int:
        return int(
            dict(
                line.split()
                for line in (root / "memory.events").read_text().splitlines()
            )["oom_kill"]
        )

    async def run(kind: Literal["action", "python"], script: str):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            if kind == "action":
                (job / "minimal_runner.py").write_text(script)
                return await executor.execute_action(
                    job,
                    ActionSandboxConfig(
                        registry_paths=[],
                        tracecat_app_dir=job,
                        network=None,
                        resources=resources,
                        timeout_seconds=15,
                    ),
                )
            (job / "wrapper.py").write_text(script)
            return await executor.execute(job, SandboxConfig(resources=resources))

    for kind in ("action", "python"):
        before = oom_kills()
        failed, sibling = await asyncio.gather(
            run(kind, OOM_SCRIPT), run(kind, HEALTHY_SCRIPT)
        )
        assert failed.exit_code == 137, failed
        assert failed.error_code is SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED, failed
        assert oom_kills() > before
        assert sibling.success, sibling
        assert (await run(kind, HEALTHY_SCRIPT)).success
        assert Path("/proc/self/cgroup").read_text() == parent_group
        assert set(root.iterdir()) == initial_children, "nsjail leaked a child cgroup"
        print(
            f"PASS {kind}: aggregate OOM contained, sibling survived, recovery succeeded",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
