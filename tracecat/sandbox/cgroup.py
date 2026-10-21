"""Delegate a container's cgroup v2 subtree before starting a sandbox worker."""

import errno
import os
import subprocess
import sys
from pathlib import Path

from tracecat import config

CGROUP_PATH_ENV = "TRACECAT__SANDBOX_CGROUP_PATH"
# UID/GID of apiuser in the Tracecat container image.
APIUSER_ID = 1001


def sandbox_cgroup(path_env: str = CGROUP_PATH_ENV) -> Path:
    """Require a writable, delegated parent for nsjail's per-sandbox cgroups."""
    value = os.environ.get(path_env)
    if not value:
        raise RuntimeError(
            "NsJail requires cgroup v2 delegation. Start the worker through its "
            "sandbox bootstrap, or provide a delegated "
            f"subtree through {path_env}."
        )
    root = Path(value)
    if "memory" not in (root / "cgroup.subtree_control").read_text().split():
        raise RuntimeError(f"Memory controller is not enabled at {root}")
    for path in (root, root / "cgroup.procs", root / "cgroup.subtree_control"):
        if not os.access(path, os.W_OK):
            raise PermissionError(f"Sandbox cgroup is not writable: {path}")
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
        raise RuntimeError("NsJail requires cgroup v2")
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


def main(path_env: str = CGROUP_PATH_ENV) -> None:
    """Run as root for delegation, then permanently drop to the image's apiuser."""
    if os.getuid() != 0 or len(sys.argv) < 2:
        raise RuntimeError(
            "Start this sandbox bootstrap as root and provide a worker command."
        )
    nsjail_enabled = config.TRACECAT__EXECUTOR_BACKEND.uses_nsjail
    if nsjail_enabled and not os.environ.get(path_env):
        root = delegate_cgroup(APIUSER_ID, APIUSER_ID)
        os.environ[path_env] = str(root)
    if nsjail_enabled and not os.environ.get(CGROUP_PATH_ENV):
        # Agent workers also host shared Run Python sandboxes.
        os.environ[CGROUP_PATH_ENV] = os.environ[path_env]
    os.environ.update(HOME="/home/apiuser", USER="apiuser", LOGNAME="apiuser")
    os.setgroups([])
    os.setgid(APIUSER_ID)
    os.setuid(APIUSER_ID)
    if nsjail_enabled:
        sandbox_cgroup(path_env)
        if path_env != CGROUP_PATH_ENV:
            sandbox_cgroup(CGROUP_PATH_ENV)
    os.execvp(sys.argv[1], sys.argv[1:])
