"""Delegate a container's cgroup v2 subtree before starting its agent worker."""

import errno
import os
import subprocess
import sys
from pathlib import Path

from tracecat.config import env_bool

CGROUP_PATH_ENV = "TRACECAT__AGENT_SANDBOX_CGROUP_PATH"
# UID/GID of apiuser in the Tracecat container image.
APIUSER_ID = 1001


def sandbox_cgroup() -> Path:
    """Require a writable, delegated parent for nsjail's per-sandbox cgroups."""
    value = os.environ.get(CGROUP_PATH_ENV)
    if not value:
        raise RuntimeError(
            "Agent nsjail requires cgroup v2 delegation. Start the worker via "
            "python -m tracecat.agent.sandbox.cgroup, or provide a delegated "
            f"subtree through {CGROUP_PATH_ENV}."
        )
    root = Path(value)
    if "memory" not in (root / "cgroup.subtree_control").read_text().split():
        raise RuntimeError(f"Memory controller is not enabled at {root}")
    for path in (root, root / "cgroup.procs", root / "cgroup.subtree_control"):
        if not os.access(path, os.W_OK):
            raise PermissionError(f"Agent cgroup is not writable: {path}")
    return root


def delegate_cgroup(uid: int, gid: int) -> Path:
    """Prepare only this container's subtree; nsjail owns individual child groups."""
    relative = next(
        (
            line.removeprefix("0::")
            for line in Path("/proc/self/cgroup").read_text().splitlines()
            if line.startswith("0::")
        ),
        None,
    )
    if relative is None:
        raise RuntimeError("Agent nsjail requires cgroup v2")
    if ".." in Path(relative).parts:
        raise RuntimeError("The container's cgroup must be visible in its namespace")
    root = Path("/sys/fs/cgroup") / relative.lstrip("/")
    # Docker mounts cgroupfs read-only even with SYS_ADMIN. Remount within the
    # container's private mount/cgroup namespaces before delegating its subtree.
    subprocess.run(["mount", "-o", "remount,rw", "/sys/fs/cgroup"], check=True)
    worker = root / "worker"
    worker.mkdir(exist_ok=True)
    for pid in (root / "cgroup.procs").read_text().split():
        try:
            (worker / "cgroup.procs").write_text(pid)
        except OSError as exc:
            if exc.errno not in {errno.ENOENT, errno.ESRCH}:
                raise
    # cgroup v2 requires the parent to be empty before enabling controllers.
    (root / "cgroup.subtree_control").write_text("+memory")
    for path in (root, root / "cgroup.procs", root / "cgroup.subtree_control"):
        os.chown(path, uid, gid)
    return root


def main() -> None:
    """Run as root for delegation, then permanently drop to the image's apiuser."""
    if os.getuid() != 0 or len(sys.argv) < 2:
        raise RuntimeError(
            "Run as root: python -m tracecat.agent.sandbox.cgroup COMMAND..."
        )
    if not env_bool("TRACECAT__DISABLE_NSJAIL", default=True):
        root = delegate_cgroup(APIUSER_ID, APIUSER_ID)
        os.environ[CGROUP_PATH_ENV] = str(root)
    os.environ.update(HOME="/home/apiuser", USER="apiuser", LOGNAME="apiuser")
    os.setgroups([])
    os.setgid(APIUSER_ID)
    os.setuid(APIUSER_ID)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
