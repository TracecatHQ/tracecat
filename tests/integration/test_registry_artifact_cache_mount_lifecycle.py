"""Dockerized lifecycle test for executor registry artifact SquashFS mounts.

The executor caches registry environments as SquashFS images and mounts them,
which consumes one loop device per mounted artifact. Unit tests stub the mount
and umount commands, so this test drives the real thing: it runs inside the
privileged executor image, builds tiny SquashFS images with ``mksquashfs``, and
asserts that materialization mounts them, that eviction unmounts them and
releases their loop devices, and that the startup sweep reclaims stale state.

Run it with the ``integration`` marker; it is skipped when Docker is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

_MOUNT_LIFECYCLE_CHILD_ENV = "TRACECAT__REGISTRY_MOUNT_LIFECYCLE_CHILD"
_MOUNT_LIFECYCLE_RESULT = "TRACECAT_REGISTRY_MOUNT_LIFECYCLE_RESULT:"
_MOUNT_LIFECYCLE_FLAG = "--run-registry-mount-lifecycle"


def _run_mount_lifecycle_in_docker_or_skip() -> dict[str, Any]:
    """Run the mount lifecycle child inside the privileged executor image.

    Returns:
        The JSON payload emitted by the in-container child run.
    """
    if os.environ.get(_MOUNT_LIFECYCLE_CHILD_ENV) == "1":
        pytest.skip("already inside registry mount lifecycle Docker child")
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable for registry mount lifecycle")
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
        pytest.skip("Docker daemon unavailable for registry mount lifecycle")

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
    compose_env["TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED"] = "true"
    compose_env[_MOUNT_LIFECYCLE_CHILD_ENV] = "1"
    compose_env["PYTHONDONTWRITEBYTECODE"] = "1"

    override_path = Path(
        tempfile.mkstemp(prefix="tracecat-registry-mount-lifecycle-", suffix=".yml")[1]
    )
    override_path.write_text(
        "\n".join(
            [
                "services:",
                "  executor:",
                "    build:",
                "      target: test",
                # Mounting SquashFS images needs both the capability and a uid
                # that holds it, so the child runs as root in a privileged
                # container. Nothing else in this test touches the host.
                "    privileged: true",
                '    user: "0:0"',
                "    security_opt:",
                "      - seccomp:unconfined",
                "      - systempaths=unconfined",
                "    environment:",
                f"      - {_MOUNT_LIFECYCLE_CHILD_ENV}",
                "      - TRACECAT__APP_ENV",
                "      - TRACECAT__SERVICE_KEY",
                "      - TRACECAT__LOCAL_REPOSITORY_ENABLED",
                "      - TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
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
                "--build",
                "-T",
                "--entrypoint",
                "/app/.venv/bin/python",
                "executor",
                "-m",
                "tests.integration.test_registry_artifact_cache_mount_lifecycle",
                _MOUNT_LIFECYCLE_FLAG,
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
        pytest.fail(
            "Dockerized registry mount lifecycle failed."
            f"\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )

    output = f"{result.stdout}\n{result.stderr}"
    for line in output.splitlines():
        if line.startswith(_MOUNT_LIFECYCLE_RESULT):
            return json.loads(line.removeprefix(_MOUNT_LIFECYCLE_RESULT))

    pytest.fail(
        "Dockerized registry mount lifecycle did not emit result sentinel."
        f"\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


@pytest.mark.integration
def test_registry_artifact_cache_mount_lifecycle() -> None:
    """Real mounts, evictions, and loop devices behave as the cache assumes."""
    payload = _run_mount_lifecycle_in_docker_or_skip()

    if skipped := payload.get("skipped"):
        pytest.skip(f"SquashFS mounts unsupported in this container: {skipped}")

    # Every materialized artifact is mounted and holds its own loop device.
    assert payload["mounted_targets"] == 3
    assert payload["mounted_loop_devices"] == 3
    assert payload["module_readable_through_mount"] is True

    # Eviction unmounts, frees the loop device, and deletes the entry.
    assert payload["evicted"] is True
    assert payload["evicted_target_unmounted"] is True
    assert payload["evicted_loop_device_released"] is True
    assert payload["evicted_paths_removed"] is True

    # Re-materializing the evicted key mounts again: no sticky disable flag.
    assert payload["remounted"] is True
    assert payload["squashfs_disabled_after_eviction"] is False

    # Releasing a lease converges an over-budget cache, unmounting as it goes.
    assert payload["converged_entry_unmounted"] is True
    assert payload["converged_loop_device_released"] is True
    assert payload["converged_entries_remaining"] == 3

    # The startup sweep trims to budget and removes stale mount directories.
    assert payload["startup_sweep_trimmed"] is True
    assert payload["startup_sweep_removed_stale_mount_dir"] is True


def _squashfs_mounts(cache_dir: Path) -> dict[str, str]:
    """Map mount target to backing device for SquashFS mounts under cache_dir.

    Args:
        cache_dir: Registry artifact cache directory.

    Returns:
        Mount target path to backing device (a loop device when mounted).
    """
    mounts: dict[str, str] = {}
    for line in Path("/proc/mounts").read_text().splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[2] != "squashfs":
            continue
        if fields[1].startswith(f"{cache_dir}/"):
            mounts[fields[1]] = fields[0]
    return mounts


def _build_squashfs_image(source_dir: Path, image_path: Path, module_name: str) -> None:
    """Build a tiny SquashFS image containing a single Python module.

    Args:
        source_dir: Scratch directory holding the module.
        image_path: Destination image path inside the cache directory.
        module_name: Module file name to place in the image.
    """
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / module_name).write_text("VALUE = 1\n")
    image_path.unlink(missing_ok=True)
    subprocess.run(
        ["mksquashfs", str(source_dir), str(image_path), "-noappend", "-quiet"],
        check=True,
        capture_output=True,
    )


async def _run_mount_lifecycle_child() -> None:
    """Exercise the real mount, eviction, and sweep lifecycle in a container."""
    from tracecat import config
    from tracecat.executor.registry_artifacts import (
        RegistryArtifactCache,
        compute_registry_artifact_cache_key,
    )

    config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED = True
    config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES = 8
    config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES = 0

    payload: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="registry-mount-lifecycle-") as tmp:
        root = Path(tmp)
        cache_dir = root / "registry-cache"
        cache_dir.mkdir()
        cache = RegistryArtifactCache(cache_dir)

        uris = [
            f"s3://bucket/lifecycle/{index}/site-packages.squashfs"
            for index in range(3)
        ]
        keys = [compute_registry_artifact_cache_key(uri) for uri in uris]
        for index, key in enumerate(keys):
            _build_squashfs_image(
                root / f"source-{index}",
                cache._paths_for(key).squashfs_image_path,
                f"module_{index}.py",
            )

        # (a) Materialize every artifact through the real cache.
        module_readable = True
        for index, uri in enumerate(uris):
            async with cache.lease([uri]) as registry_paths:
                mount_dir = cache._paths_for(keys[index]).squashfs_mount_dir
                if registry_paths != [mount_dir]:
                    if index == 0 and not mount_dir.is_mount():
                        # Kernels without SquashFS or loop support fall back to
                        # extraction; that is an environment limit, not a bug.
                        payload["skipped"] = (
                            f"materialized {registry_paths} instead of a mount"
                        )
                        print(
                            f"{_MOUNT_LIFECYCLE_RESULT}"
                            f"{json.dumps(payload, sort_keys=True)}"
                        )
                        return
                    raise AssertionError(
                        f"Expected mount for {uri}, got {registry_paths}"
                    )
                module_readable = (
                    module_readable
                    and (mount_dir / f"module_{index}.py").read_text() == "VALUE = 1\n"
                )

        mounts = _squashfs_mounts(cache_dir)
        payload["mounted_targets"] = len(mounts)
        payload["mounted_loop_devices"] = len(
            {device for device in mounts.values() if device.startswith("/dev/loop")}
        )
        payload["module_readable_through_mount"] = module_readable

        # (b) Evict one entry: it must unmount, free its loop device, and vanish.
        evicted_paths = cache._paths_for(keys[0])
        evicted_device = mounts[str(evicted_paths.squashfs_mount_dir)]
        payload["evicted"] = await cache._evict_entry(keys[0])
        mounts_after_eviction = _squashfs_mounts(cache_dir)
        payload["evicted_target_unmounted"] = (
            str(evicted_paths.squashfs_mount_dir) not in mounts_after_eviction
        )
        payload["evicted_loop_device_released"] = (
            evicted_device not in mounts_after_eviction.values()
        )
        payload["evicted_paths_removed"] = not (
            evicted_paths.squashfs_image_path.exists()
            or evicted_paths.squashfs_mount_dir.exists()
        )

        # (c) The evicted key must mount again: eviction is not a capability probe.
        _build_squashfs_image(
            root / "source-0",
            evicted_paths.squashfs_image_path,
            "module_0.py",
        )
        async with cache.lease([uris[0]]) as registry_paths:
            payload["remounted"] = registry_paths == [evicted_paths.squashfs_mount_dir]
        payload["squashfs_disabled_after_eviction"] = (
            cache._squashfs_mount_state.disabled
        )

        # (d) A released lease converges an over-budget cache, unmounting as it
        # goes. The fourth entry is materialized under the old budget, so only
        # the release-time check can bring the cache back within the new one.
        fourth_uri = "s3://bucket/lifecycle/3/site-packages.squashfs"
        fourth_key = compute_registry_artifact_cache_key(fourth_uri)
        _build_squashfs_image(
            root / "source-3",
            cache._paths_for(fourth_key).squashfs_image_path,
            "module_3.py",
        )
        # keys[2] is the least recently used idle entry: keys[0] was re-leased
        # above and keys[1] is leased again below.
        converged_paths = cache._paths_for(keys[2])
        async with cache.lease([fourth_uri]):
            mounts_before_converge = _squashfs_mounts(cache_dir)
            converged_device = mounts_before_converge[
                str(converged_paths.squashfs_mount_dir)
            ]
            config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES = 3
            async with cache.lease([uris[1]]):
                pass
            mounts_after_converge = _squashfs_mounts(cache_dir)
            payload["converged_entry_unmounted"] = (
                str(converged_paths.squashfs_mount_dir) not in mounts_after_converge
            )
            payload["converged_loop_device_released"] = (
                converged_device not in mounts_after_converge.values()
            )
            payload["converged_entries_remaining"] = len(cache._discover_cache_keys())

        # (e) The startup sweep trims to budget and drops stale mount directories.
        sweep_dir = root / "sweep-cache"
        sweep_dir.mkdir()
        sweep_keys = ["aaaa1111", "bbbb2222"]
        for index, sweep_key in enumerate(sweep_keys):
            image_path = sweep_dir / f"squashfs-{sweep_key}.squashfs"
            image_path.write_bytes(b"x" * 4096)
            os.utime(image_path, (100.0 + index, 100.0 + index))
        stale_mount_dir = sweep_dir / f"squashfs-{sweep_keys[0]}"
        stale_mount_dir.mkdir()

        config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES = 1
        sweep_cache = RegistryArtifactCache(sweep_dir)
        await sweep_cache.ensure_swept()
        payload["startup_sweep_trimmed"] = (
            not (sweep_dir / f"squashfs-{sweep_keys[0]}.squashfs").exists()
            and (sweep_dir / f"squashfs-{sweep_keys[1]}.squashfs").exists()
        )
        payload["startup_sweep_removed_stale_mount_dir"] = not stale_mount_dir.exists()

        # Leave no mounts behind for the container teardown.
        for cache_key in sorted(cache._discover_cache_keys()):
            await cache._evict_entry(cache_key)

    print(f"{_MOUNT_LIFECYCLE_RESULT}{json.dumps(payload, sort_keys=True)}")


if __name__ == "__main__":
    if os.environ.get(_MOUNT_LIFECYCLE_CHILD_ENV) == "1" and sys.argv[1:] == [
        _MOUNT_LIFECYCLE_FLAG
    ]:
        asyncio.run(_run_mount_lifecycle_child())
    else:
        raise SystemExit(
            "Usage: python -m tests.integration."
            f"test_registry_artifact_cache_mount_lifecycle {_MOUNT_LIFECYCLE_FLAG}"
        )
