"""Reproduce stale loop-device visibility inside an executor container.

Linux can create a new loop object through ``/dev/loop-control`` while the
matching ``/dev/loopN`` block node remains absent from a running container's
private ``/dev``. This privileged integration test constrains a disposable
executor container to one visible loop block node, occupies it, and proves
that the legacy ``mount -o loop`` path fails for a second image.

The companion regression exercises Tracecat's product mount path against the
same condition as the real non-root ``apiuser``. It requires the product path
to synchronize the missing node and successfully mount the second image.

Build the normal executor image once, then run without database-backed parent
fixtures. The test reuses that image and bind-mounts only this test source::

    docker compose -f docker-compose.dev.yml build executor
    uv run pytest --confcutdir=tests/integration \
        tests/integration/test_registry_artifact_loop_hotplug.py \
        -m integration -s
"""

from __future__ import annotations

import asyncio
import fcntl
import functools
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self, cast

_LOOP_HOTPLUG_CHILD_ENV = "TRACECAT__REGISTRY_LOOP_HOTPLUG_CHILD"
_LOOP_HOTPLUG_FLAG = "--run-registry-loop-hotplug"
_LOOP_HOTPLUG_RESULT = "TRACECAT_REGISTRY_LOOP_HOTPLUG_RESULT:"

_LOOP_BLOCK_MAJOR = 7
_LOOP_CTL_GET_FREE = 0x4C82

if os.environ.get(_LOOP_HOTPLUG_CHILD_ENV) != "1":
    import pytest

    pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class LoopHotplugPayload:
    """Observable outcomes from the disposable privileged container."""

    skipped: str | None
    legacy_holder_mounted: bool
    legacy_probe_failed: bool
    legacy_error: str
    kernel_device_exists_without_container_node: bool
    product_ran_as_apiuser: bool
    product_mount_succeeded: bool
    product_error: str | None
    product_created_device_node: bool
    product_module_readable: bool
    cleanup_unmounted: bool

    @classmethod
    def from_json(cls, raw: str) -> Self:
        """Validate and decode the child-process result payload."""
        decoded: object = json.loads(raw)
        if not isinstance(decoded, dict):
            raise TypeError("Loop hotplug payload must be a JSON object")
        data = cast(dict[str, object], decoded)

        def required_bool(key: str) -> bool:
            value = data.get(key)
            if not isinstance(value, bool):
                raise TypeError(f"Loop hotplug payload field {key!r} must be bool")
            return value

        def required_str(key: str) -> str:
            value = data.get(key)
            if not isinstance(value, str):
                raise TypeError(f"Loop hotplug payload field {key!r} must be str")
            return value

        def optional_str(key: str) -> str | None:
            value = data.get(key)
            if value is not None and not isinstance(value, str):
                raise TypeError(
                    f"Loop hotplug payload field {key!r} must be str or null"
                )
            return value

        return cls(
            skipped=optional_str("skipped"),
            legacy_holder_mounted=required_bool("legacy_holder_mounted"),
            legacy_probe_failed=required_bool("legacy_probe_failed"),
            legacy_error=required_str("legacy_error"),
            kernel_device_exists_without_container_node=required_bool(
                "kernel_device_exists_without_container_node"
            ),
            product_ran_as_apiuser=required_bool("product_ran_as_apiuser"),
            product_mount_succeeded=required_bool("product_mount_succeeded"),
            product_error=optional_str("product_error"),
            product_created_device_node=required_bool("product_created_device_node"),
            product_module_readable=required_bool("product_module_readable"),
            cleanup_unmounted=required_bool("cleanup_unmounted"),
        )


def _empty_payload(*, skipped: str | None = None) -> LoopHotplugPayload:
    """Return a result with every probe false."""
    return LoopHotplugPayload(
        skipped=skipped,
        legacy_holder_mounted=False,
        legacy_probe_failed=False,
        legacy_error="",
        kernel_device_exists_without_container_node=False,
        product_ran_as_apiuser=False,
        product_mount_succeeded=False,
        product_error=None,
        product_created_device_node=False,
        product_module_readable=False,
        cleanup_unmounted=False,
    )


def _run_loop_hotplug_in_docker_or_skip() -> LoopHotplugPayload:
    """Run the loop-device hotplug scenario in a disposable executor."""
    if os.environ.get(_LOOP_HOTPLUG_CHILD_ENV) == "1":
        raise unittest.SkipTest("already inside registry loop hotplug Docker child")
    if shutil.which("docker") is None:
        raise unittest.SkipTest("Docker CLI unavailable for registry loop hotplug")
    if (
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).returncode
        != 0
    ):
        raise unittest.SkipTest("Docker daemon unavailable for registry loop hotplug")

    repo_root = Path(__file__).resolve().parents[2]
    compose_env = os.environ.copy()
    compose_env.setdefault(
        "TRACECAT__LOCAL_REPOSITORY_PATH",
        str(repo_root / "packages"),
    )
    compose_env.setdefault("PUBLIC_APP_PORT", "80")
    compose_env.setdefault("BASE_DOMAIN", ":80")
    compose_env.setdefault("ADDRESS", "0.0.0.0")
    compose_env["LOG_LEVEL"] = "INFO"
    compose_env["TRACECAT__APP_ENV"] = "development"
    compose_env["TRACECAT__SERVICE_KEY"] = "test-service-key"
    compose_env["TRACECAT__LOCAL_REPOSITORY_ENABLED"] = "false"
    compose_env[_LOOP_HOTPLUG_CHILD_ENV] = "1"
    compose_env["PYTHONDONTWRITEBYTECODE"] = "1"

    override_fd, override_name = tempfile.mkstemp(
        prefix="tracecat-registry-loop-hotplug-",
        suffix=".yml",
    )
    os.close(override_fd)
    override_path = Path(override_name)
    override_path.write_text(
        "\n".join(
            [
                "services:",
                "  executor:",
                "    privileged: true",
                '    user: "0:0"',
                "    security_opt:",
                "      - seccomp:unconfined",
                "      - systempaths=unconfined",
                "    volumes:",
                f"      - {repo_root / 'tests'}:/app/tests:ro",
                "    environment:",
                f"      - {_LOOP_HOTPLUG_CHILD_ENV}",
                "      - TRACECAT__APP_ENV",
                "      - TRACECAT__SERVICE_KEY",
                "      - TRACECAT__LOCAL_REPOSITORY_ENABLED",
                "      - PYTHONDONTWRITEBYTECODE",
                "",
            ]
        )
    )
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(repo_root / "docker-compose.dev.yml"),
                "-f",
                str(override_path),
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "/app/.venv/bin/python",
                "executor",
                "-m",
                "tests.integration.test_registry_artifact_loop_hotplug",
                _LOOP_HOTPLUG_FLAG,
            ],
            cwd=repo_root,
            env=compose_env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    finally:
        override_path.unlink(missing_ok=True)

    if result.returncode != 0:
        raise AssertionError(
            "Dockerized registry loop hotplug scenario failed."
            f"\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )

    output = f"{result.stdout}\n{result.stderr}"
    for line in output.splitlines():
        if line.startswith(_LOOP_HOTPLUG_RESULT):
            return LoopHotplugPayload.from_json(line.removeprefix(_LOOP_HOTPLUG_RESULT))

    raise AssertionError(
        "Dockerized registry loop hotplug scenario did not emit a result."
        f"\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


@functools.cache
def _loop_hotplug_payload() -> LoopHotplugPayload:
    """Run the isolated privileged-container scenario once."""
    return _run_loop_hotplug_in_docker_or_skip()


def test_legacy_mount_reproduces_missing_loop_device_node() -> None:
    """Reproduce the production ``failed to setup loop device`` condition."""
    payload = _loop_hotplug_payload()
    if payload.skipped:
        raise unittest.SkipTest(payload.skipped)

    assert payload.legacy_holder_mounted is True
    assert payload.legacy_probe_failed is True
    assert "failed to setup loop device" in payload.legacy_error
    assert payload.kernel_device_exists_without_container_node is True


def test_product_mount_synchronizes_missing_loop_device_node() -> None:
    """Require Tracecat's non-root mount path to recover from stale ``/dev``."""
    payload = _loop_hotplug_payload()
    if payload.skipped:
        raise unittest.SkipTest(payload.skipped)

    assert payload.product_ran_as_apiuser is True
    assert payload.product_mount_succeeded is True, payload.product_error
    assert payload.product_created_device_node is True
    assert payload.product_module_readable is True
    assert payload.cleanup_unmounted is True


def _dev_filesystem_type() -> str | None:
    """Return the filesystem type mounted at ``/dev`` in this container."""
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[4] != "/dev":
            continue
        separator = fields.index("-")
        return fields[separator + 1]
    return None


def _remove_local_loop_block_nodes() -> None:
    """Remove loop block nodes only from the disposable container's ``/dev``."""
    for path in Path("/dev").iterdir():
        suffix = path.name.removeprefix("loop")
        if not path.name.startswith("loop") or not suffix.isdigit():
            continue
        path_stat = path.lstat()
        if not stat.S_ISBLK(path_stat.st_mode):
            raise RuntimeError(f"Refusing to remove non-block loop path: {path}")
        if os.major(path_stat.st_rdev) != _LOOP_BLOCK_MAJOR:
            raise RuntimeError(f"Refusing to remove unexpected loop device: {path}")
        path.unlink()


def _get_free_loop_minor() -> int:
    """Ask the shared kernel loop driver for an unbound loop minor."""
    control_fd = os.open("/dev/loop-control", os.O_RDWR | os.O_CLOEXEC)
    try:
        return fcntl.ioctl(control_fd, _LOOP_CTL_GET_FREE, 0)
    finally:
        os.close(control_fd)


def _create_local_loop_node(minor: int) -> Path:
    """Create exactly one loop block node in the container's private ``/dev``."""
    path = Path(f"/dev/loop{minor}")
    os.mknod(
        path,
        stat.S_IFBLK | 0o600,
        os.makedev(_LOOP_BLOCK_MAJOR, minor),
    )
    return path


def _build_squashfs_image(source_dir: Path, image_path: Path) -> None:
    """Build a tiny readable SquashFS image for a real mount."""
    source_dir.mkdir(parents=True)
    (source_dir / "probe.py").write_text("VALUE = 1\n")
    subprocess.run(
        ["mksquashfs", str(source_dir), str(image_path), "-noappend", "-quiet"],
        check=True,
        capture_output=True,
        text=True,
    )


def _legacy_mount(
    image_path: Path, target_dir: Path
) -> subprocess.CompletedProcess[str]:
    """Invoke the legacy auto-loop mount behavior under test."""
    target_dir.mkdir(exist_ok=True)
    return subprocess.run(
        [
            "mount",
            "-t",
            "squashfs",
            "-o",
            "loop,ro,nodev,nosuid",
            str(image_path),
            str(target_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _is_mounted(target_dir: Path) -> bool:
    """Return whether the exact target occurs in the process mount table."""
    target = str(target_dir)
    return any(
        len(fields := line.split()) >= 3 and fields[1] == target
        for line in Path("/proc/mounts").read_text().splitlines()
    )


def _unmount_if_mounted(target_dir: Path) -> None:
    """Best-effort cleanup for a mount owned by the disposable container."""
    if not _is_mounted(target_dir):
        return
    subprocess.run(
        ["umount", str(target_dir)],
        check=True,
        capture_output=True,
        text=True,
    )


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    """Make the completed fixture tree removable after dropping privileges."""
    os.chown(root, uid, gid)
    for path in root.rglob("*"):
        os.chown(path, uid, gid)


async def _run_loop_hotplug_child() -> None:
    """Reproduce the missing-node failure and probe Tracecat's mount path."""
    if _dev_filesystem_type() != "tmpfs":
        payload = _empty_payload(
            skipped="container /dev is not an isolated tmpfs; refusing to modify it"
        )
        print(f"{_LOOP_HOTPLUG_RESULT}{json.dumps(asdict(payload), sort_keys=True)}")
        return

    required_paths = (
        "/dev/loop-control",
        shutil.which("mount"),
        shutil.which("umount"),
    )
    if any(path is None or not os.path.exists(path) for path in required_paths):
        payload = _empty_payload(skipped="loop-control, mount, or umount unavailable")
        print(f"{_LOOP_HOTPLUG_RESULT}{json.dumps(asdict(payload), sort_keys=True)}")
        return
    if shutil.which("mksquashfs") is None:
        payload = _empty_payload(skipped="mksquashfs unavailable")
        print(f"{_LOOP_HOTPLUG_RESULT}{json.dumps(asdict(payload), sort_keys=True)}")
        return

    _remove_local_loop_block_nodes()
    holder_minor = _get_free_loop_minor()
    _create_local_loop_node(holder_minor)

    legacy_holder_mounted = False
    legacy_probe_failed = False
    legacy_error = ""
    kernel_device_exists_without_container_node = False
    product_ran_as_apiuser = False
    product_mount_succeeded = False
    product_error: str | None = None
    product_created_device_node = False
    product_module_readable = False
    cleanup_unmounted = False

    with tempfile.TemporaryDirectory(prefix="registry-loop-hotplug-") as tmp:
        root = Path(tmp)
        holder_image = root / "holder.squashfs"
        probe_image = root / "probe.squashfs"
        holder_target = root / "holder-mount"
        probe_target = root / "probe-mount"
        _build_squashfs_image(root / "holder-source", holder_image)
        _build_squashfs_image(root / "probe-source", probe_image)
        holder_target.mkdir()
        probe_target.mkdir()

        apiuser = pwd.getpwnam("apiuser")
        _chown_tree(root, apiuser.pw_uid, apiuser.pw_gid)

        try:
            holder_result = _legacy_mount(holder_image, holder_target)
            if holder_result.returncode != 0:
                raise RuntimeError(
                    "Could not establish the single visible loop holder: "
                    f"{holder_result.stderr or holder_result.stdout}"
                )
            legacy_holder_mounted = _is_mounted(holder_target)

            probe_minor = _get_free_loop_minor()
            probe_device = Path(f"/dev/loop{probe_minor}")
            probe_kernel_device = Path(f"/sys/block/loop{probe_minor}")

            legacy_result = _legacy_mount(probe_image, probe_target)
            legacy_probe_failed = legacy_result.returncode != 0
            legacy_error = (legacy_result.stderr or legacy_result.stdout).strip()
            kernel_device_exists_without_container_node = (
                probe_kernel_device.exists() and not probe_device.exists()
            )

            os.setgroups([])
            os.setgid(apiuser.pw_gid)
            os.setuid(apiuser.pw_uid)
            product_ran_as_apiuser = (
                os.getuid() == apiuser.pw_uid and os.geteuid() == apiuser.pw_uid
            )

            from tracecat.executor.registry_artifacts import SquashfsArtifact

            artifact = SquashfsArtifact(
                uri="s3://example/registry/site-packages.squashfs",
                cache_key="loop-hotplug-probe",
            )
            try:
                await artifact._mount_image(probe_image, probe_target)
            except Exception as error:
                product_error = f"{type(error).__name__}: {error}"
            else:
                product_mount_succeeded = _is_mounted(probe_target)

            if probe_device.exists():
                probe_stat = probe_device.lstat()
                product_created_device_node = (
                    stat.S_ISBLK(probe_stat.st_mode)
                    and os.major(probe_stat.st_rdev) == _LOOP_BLOCK_MAJOR
                    and os.minor(probe_stat.st_rdev) == probe_minor
                )
            product_module_readable = (
                product_mount_succeeded
                and (probe_target / "probe.py").read_text() == "VALUE = 1\n"
            )
        finally:
            _unmount_if_mounted(probe_target)
            _unmount_if_mounted(holder_target)
            cleanup_unmounted = not _is_mounted(probe_target) and not _is_mounted(
                holder_target
            )

    payload = LoopHotplugPayload(
        skipped=None,
        legacy_holder_mounted=legacy_holder_mounted,
        legacy_probe_failed=legacy_probe_failed,
        legacy_error=legacy_error,
        kernel_device_exists_without_container_node=(
            kernel_device_exists_without_container_node
        ),
        product_ran_as_apiuser=product_ran_as_apiuser,
        product_mount_succeeded=product_mount_succeeded,
        product_error=product_error,
        product_created_device_node=product_created_device_node,
        product_module_readable=product_module_readable,
        cleanup_unmounted=cleanup_unmounted,
    )
    print(f"{_LOOP_HOTPLUG_RESULT}{json.dumps(asdict(payload), sort_keys=True)}")


if __name__ == "__main__":
    if os.environ.get(_LOOP_HOTPLUG_CHILD_ENV) == "1" and sys.argv[1:] == [
        _LOOP_HOTPLUG_FLAG
    ]:
        asyncio.run(_run_loop_hotplug_child())
    else:
        raise SystemExit("Refusing to run loop hotplug child without its guard")
