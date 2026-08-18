"""Dockerized lifecycle test for executor registry artifact SquashFS mounts.

The executor retains SquashFS images but only keeps them mounted while leased.
Unit tests stub the mount and umount commands, so this test drives the real
mount lifecycle inside the privileged executor image.

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
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

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


@pytest.fixture(scope="module")
def mount_lifecycle_payload() -> dict[str, Any]:
    """Run the privileged Docker scenario once for this integration module."""
    return _run_mount_lifecycle_in_docker_or_skip()


@pytest.mark.integration
def test_registry_artifact_cache_mount_lifecycle(
    mount_lifecycle_payload: dict[str, Any],
) -> None:
    """Protect lease invariants against the real kernel mount lifecycle.

    Unit mocks cannot prove that overlapping holders share one loop device or
    that final release actually returns it to the kernel. This privileged test
    guards those assumptions alongside retained-image remount and eviction.
    """
    payload = mount_lifecycle_payload

    if skipped := payload.get("skipped"):
        pytest.skip(f"SquashFS mounts unsupported in this container: {skipped}")

    # Each lease mounts its image and its final release frees the loop device.
    assert payload["module_readable_through_mount"] is True
    assert payload["lease_mounts_observed"] == 3
    assert payload["all_targets_unmounted_after_release"] is True
    assert payload["all_loop_devices_released"] is True
    assert payload["images_retained_after_release"] is True

    # A later lease remounts from the retained image without downloading again.
    assert payload["remounted_from_retained_image"] is True
    assert payload["remount_released"] is True

    # Overlapping holders share one real mount and only the last one releases it.
    assert payload["concurrent_paths_shared"] is True
    assert payload["concurrent_peak_refcount"] == 3
    assert payload["concurrent_single_mount"] is True
    assert payload["concurrent_intermediate_release_preserved_mount"] is True
    assert payload["concurrent_final_release_unmounted"] is True
    assert payload["concurrent_loop_device_released"] is True

    # Eviction deletes an already-idle entry.
    assert payload["evicted"] is True
    assert payload["evicted_paths_removed"] is True

    # Releasing a lease unmounts first, then converges the disk cache.
    assert payload["converged_lease_unmounted"] is True
    assert payload["converged_loop_device_released"] is True
    assert payload["converged_entries_remaining"] == 2

    # The startup sweep trims to budget and removes stale mount directories.
    assert payload["startup_sweep_trimmed"] is True
    assert payload["startup_sweep_removed_stale_mount_dir"] is True


@pytest.mark.integration
def test_registry_artifact_cache_retries_after_held_lease_releases(
    mount_lifecycle_payload: dict[str, Any],
) -> None:
    """Reproduce byte-budget admission failure under concurrent leased load.

    Several concurrent executions hold distinct real SquashFS mounts, leaving
    no eviction candidate for one more cold artifact. Releasing one execution
    makes exactly one entry idle, so the same admission request can evict it.
    """
    payload = mount_lifecycle_payload

    if skipped := payload.get("skipped"):
        pytest.skip(f"SquashFS mounts unsupported in this container: {skipped}")

    assert (
        payload["capacity_error_current_bytes"]
        == payload["capacity_snapshot_total_bytes"]
    )
    assert (
        payload["capacity_error_additional_bytes"]
        >= payload["capacity_cold_image_bytes"]
    )
    assert (
        payload["capacity_error_additional_bytes"] % payload["capacity_allocation_unit"]
        == 0
    )
    assert payload["capacity_error_max_bytes"] == payload["capacity_expected_max_bytes"]
    assert (
        payload["capacity_error_current_bytes"]
        + payload["capacity_error_additional_bytes"]
        > payload["capacity_error_max_bytes"]
    )
    assert payload["capacity_concurrent_holders"] == 3
    assert payload["capacity_all_refcounts_pinned"] is True
    assert payload["capacity_partial_pins_released_before_retry"] is True
    assert payload["capacity_all_entries_preserved"] is True
    assert payload["capacity_all_mounts_preserved"] is True
    assert payload["capacity_public_lease_retry_delays"] == 1
    assert payload["capacity_retry_succeeded_after_one_release"] is True
    assert payload["capacity_cold_mount_released"] is True
    assert payload["capacity_released_entry_evicted"] is True
    assert payload["capacity_still_held_entries_preserved"] is True
    assert payload["capacity_still_held_mounts_preserved"] is True


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
    image_path.parent.mkdir(parents=True, exist_ok=True)
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
        RegistryArtifactCacheLeaseContentionError,
        allocated_size_bound,
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

        # (a) Every lease mounts its image; final release unmounts it.
        module_readable = True
        lease_mounts_observed = 0
        all_targets_unmounted = True
        all_loop_devices_released = True
        for index, uri in enumerate(uris):
            mount_dir = cache._paths_for(keys[index]).squashfs_mount_dir
            async with cache.lease([uri]) as registry_paths:
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
                device = mounts[str(mount_dir)]
                lease_mounts_observed += 1

            mounts_after_release = _squashfs_mounts(cache_dir)
            all_targets_unmounted = (
                all_targets_unmounted and str(mount_dir) not in mounts_after_release
            )
            all_loop_devices_released = (
                all_loop_devices_released
                and device not in mounts_after_release.values()
            )

        payload["module_readable_through_mount"] = module_readable
        payload["lease_mounts_observed"] = lease_mounts_observed
        payload["all_targets_unmounted_after_release"] = all_targets_unmounted
        payload["all_loop_devices_released"] = all_loop_devices_released
        payload["images_retained_after_release"] = all(
            cache._paths_for(cache_key).squashfs_image_path.exists()
            for cache_key in keys
        )

        # (b) A later lease remounts from the retained image.
        retained_paths = cache._paths_for(keys[0])
        async with cache.lease([uris[0]]) as registry_paths:
            payload["remounted_from_retained_image"] = registry_paths == [
                retained_paths.squashfs_mount_dir
            ]
        payload["remount_released"] = not retained_paths.squashfs_mount_dir.is_mount()

        # (c) Overlapping leases share one mount until final release.
        concurrent_holders = 3
        concurrent_entered = 0
        all_concurrent_entered = asyncio.Event()
        concurrent_releases = [asyncio.Event() for _ in range(concurrent_holders)]
        concurrent_paths_shared: list[bool] = []

        async def hold_concurrent_lease(index: int) -> None:
            nonlocal concurrent_entered
            async with cache.lease([uris[0]]) as registry_paths:
                concurrent_paths_shared.append(
                    registry_paths == [retained_paths.squashfs_mount_dir]
                    and (registry_paths[0] / "module_0.py").read_text() == "VALUE = 1\n"
                )
                concurrent_entered += 1
                if concurrent_entered == concurrent_holders:
                    all_concurrent_entered.set()
                await concurrent_releases[index].wait()

        holders = [
            asyncio.create_task(hold_concurrent_lease(index))
            for index in range(concurrent_holders)
        ]
        await asyncio.wait_for(all_concurrent_entered.wait(), timeout=10)
        concurrent_mounts = _squashfs_mounts(cache_dir)
        concurrent_device = concurrent_mounts[str(retained_paths.squashfs_mount_dir)]
        payload["concurrent_paths_shared"] = all(concurrent_paths_shared)
        payload["concurrent_peak_refcount"] = cache._refcount(keys[0])
        payload["concurrent_single_mount"] = (
            list(concurrent_mounts).count(str(retained_paths.squashfs_mount_dir)) == 1
        )

        for release in concurrent_releases[:-1]:
            release.set()
        await asyncio.gather(*holders[:-1])
        mounts_after_intermediate_releases = _squashfs_mounts(cache_dir)
        payload["concurrent_intermediate_release_preserved_mount"] = (
            cache._refcount(keys[0]) == 1
            and str(retained_paths.squashfs_mount_dir)
            in mounts_after_intermediate_releases
        )

        concurrent_releases[-1].set()
        await holders[-1]
        mounts_after_concurrent_release = _squashfs_mounts(cache_dir)
        payload["concurrent_final_release_unmounted"] = (
            str(retained_paths.squashfs_mount_dir)
            not in mounts_after_concurrent_release
        )
        payload["concurrent_loop_device_released"] = (
            concurrent_device not in mounts_after_concurrent_release.values()
        )

        # (d) Evict one already-idle entry.
        evicted_paths = cache._paths_for(keys[0])
        eviction = await cache._evict_entry(keys[0])
        payload["evicted"] = eviction.retired and eviction.reclaimed
        payload["evicted_paths_removed"] = not (
            evicted_paths.squashfs_image_path.exists()
            or evicted_paths.squashfs_mount_dir.exists()
        )

        # (e) Final release unmounts, then converges an over-budget cache.
        fourth_uri = "s3://bucket/lifecycle/3/site-packages.squashfs"
        fourth_key = compute_registry_artifact_cache_key(fourth_uri)
        fourth_paths = cache._paths_for(fourth_key)
        _build_squashfs_image(
            root / "source-3",
            fourth_paths.squashfs_image_path,
            "module_3.py",
        )
        async with cache.lease([fourth_uri]):
            mounts_before_converge = _squashfs_mounts(cache_dir)
            converged_device = mounts_before_converge[
                str(fourth_paths.squashfs_mount_dir)
            ]
            config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES = 2
        mounts_after_converge = _squashfs_mounts(cache_dir)
        payload["converged_lease_unmounted"] = (
            str(fourth_paths.squashfs_mount_dir) not in mounts_after_converge
        )
        payload["converged_loop_device_released"] = (
            converged_device not in mounts_after_converge.values()
        )
        payload["converged_entries_remaining"] = len(cache._discover_cache_keys())

        # (f) The startup sweep trims to budget and drops stale mount directories.
        sweep_dir = root / "sweep-cache"
        sweep_dir.mkdir()
        sweep_cache = RegistryArtifactCache(sweep_dir)
        sweep_keys = ["aaaa1111", "bbbb2222"]
        for index, sweep_key in enumerate(sweep_keys):
            sweep_paths = sweep_cache._paths_for(sweep_key)
            sweep_paths.entry_dir.mkdir(parents=True)
            image_path = sweep_paths.squashfs_image_path
            image_path.write_bytes(b"x" * 4096)
            os.utime(sweep_paths.entry_dir, (100.0 + index, 100.0 + index))
        stale_mount_dir = sweep_cache._paths_for(sweep_keys[0]).squashfs_mount_dir
        stale_mount_dir.mkdir()
        os.utime(
            sweep_cache._paths_for(sweep_keys[0]).entry_dir,
            (100.0, 100.0),
        )

        config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES = 1
        await sweep_cache.ensure_swept()
        evicted_paths = sweep_cache._paths_for(sweep_keys[0])
        retained_paths = sweep_cache._paths_for(sweep_keys[1])
        payload["startup_sweep_trimmed"] = (
            not evicted_paths.squashfs_image_path.exists()
            and retained_paths.squashfs_image_path.exists()
        )
        payload["startup_sweep_removed_stale_mount_dir"] = not stale_mount_dir.exists()

        # (g) Concurrent leased entries cannot be evicted for a new execution.
        capacity_dir = root / "capacity-cache"
        capacity_dir.mkdir()
        capacity_cache = RegistryArtifactCache(capacity_dir)
        capacity_holder_count = 3
        capacity_uris = [
            f"s3://bucket/capacity/held-{index}/site-packages.squashfs"
            for index in range(capacity_holder_count)
        ]
        capacity_keys = [
            compute_registry_artifact_cache_key(uri) for uri in capacity_uris
        ]
        capacity_paths = [
            capacity_cache._paths_for(cache_key) for cache_key in capacity_keys
        ]
        cold_uri = "s3://bucket/capacity/cold/site-packages.squashfs"
        cold_key = compute_registry_artifact_cache_key(cold_uri)
        cold_image_source = root / "capacity-cold-image.squashfs"
        _build_squashfs_image(
            root / "capacity-cold-source",
            cold_image_source,
            "capacity_cold_module.py",
        )
        for index, paths in enumerate(capacity_paths):
            _build_squashfs_image(
                root / f"capacity-source-{index}",
                paths.squashfs_image_path,
                f"capacity_module_{index}.py",
            )

        config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES = 0
        config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES = 0
        capacity_entered = 0
        all_capacity_holders_entered = asyncio.Event()
        capacity_releases = [asyncio.Event() for _ in range(capacity_holder_count)]

        async def hold_capacity_lease(index: int) -> None:
            nonlocal capacity_entered
            async with capacity_cache.lease([capacity_uris[index]]) as paths:
                if paths != [capacity_paths[index].squashfs_mount_dir]:
                    raise AssertionError(
                        f"Expected mounted capacity artifact {index}, got {paths}"
                    )
                capacity_entered += 1
                if capacity_entered == capacity_holder_count:
                    all_capacity_holders_entered.set()
                await capacity_releases[index].wait()

        capacity_holders = [
            asyncio.create_task(hold_capacity_lease(index))
            for index in range(capacity_holder_count)
        ]
        try:
            await asyncio.wait_for(all_capacity_holders_entered.wait(), timeout=30)
            cold_paths = capacity_cache._paths_for(cold_key)
            cold_paths.entry_dir.mkdir(parents=True)
            cold_paths.squashfs_mount_dir.mkdir()
            capacity_with_cold_shell = capacity_cache._scan_cache_snapshot()
            cold_paths.squashfs_mount_dir.rmdir()
            cold_paths.entry_dir.rmdir()
            capacity_snapshot = capacity_cache._scan_cache_snapshot()
            cold_shell_bytes = (
                capacity_with_cold_shell.total_bytes - capacity_snapshot.total_bytes
            )
            config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES = 1
            probe_admission = capacity_cache._admission_for(cold_key)
            if probe_admission is None:
                raise AssertionError("Expected byte-bound registry cache admission")
            allocation_unit = probe_admission.allocation_unit
            cold_image_bytes = cold_image_source.stat().st_size
            cold_download_bytes = allocated_size_bound(
                cold_image_bytes + 2 * allocation_unit,
                allocation_unit=allocation_unit,
            )
            released_entry_bytes = capacity_snapshot.entries[
                capacity_keys[0]
            ].size_bytes
            max_bytes = (
                capacity_snapshot.total_bytes
                - released_entry_bytes
                + cold_shell_bytes
                + cold_download_bytes
            )
            config.TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES = max_bytes
            payload["capacity_allocation_unit"] = allocation_unit
            payload["capacity_expected_max_bytes"] = max_bytes
            payload["capacity_cold_image_bytes"] = cold_image_bytes
            retry_delays: list[float] = []

            async def download_capacity_artifact(
                *,
                key: str,
                bucket: str,
                output_path: Path,
                max_bytes: int | None,
                ensure_capacity: Callable[[int], Awaitable[None]] | None,
                defer_cleanup: Callable[[Path], None] | None,
                redact_log_identifiers: bool,
            ) -> None:
                del key, bucket, max_bytes, defer_cleanup, redact_log_identifiers
                if ensure_capacity is None:
                    raise AssertionError("Expected capacity-aware artifact download")
                attempt_snapshot = capacity_cache._scan_cache_snapshot()
                try:
                    await ensure_capacity(cold_image_source.stat().st_size)
                except RegistryArtifactCacheLeaseContentionError as error:
                    payload["capacity_snapshot_total_bytes"] = (
                        attempt_snapshot.total_bytes
                    )
                    payload["capacity_error_current_bytes"] = error.current_bytes
                    payload["capacity_error_additional_bytes"] = error.additional_bytes
                    payload["capacity_error_max_bytes"] = error.max_bytes
                    raise
                shutil.copyfile(cold_image_source, output_path)

            async def release_one_holder_during_backoff(delay: float) -> None:
                retry_delays.append(delay)
                payload["capacity_concurrent_holders"] = capacity_holder_count
                payload["capacity_all_refcounts_pinned"] = all(
                    capacity_cache._refcount(cache_key) == 1
                    for cache_key in capacity_keys
                )
                payload["capacity_partial_pins_released_before_retry"] = (
                    capacity_cache._refcount(cold_key) == 0
                )
                payload["capacity_all_entries_preserved"] = all(
                    paths.squashfs_image_path.exists() for paths in capacity_paths
                )
                payload["capacity_all_mounts_preserved"] = all(
                    paths.squashfs_mount_dir.is_mount() for paths in capacity_paths
                )
                capacity_releases[0].set()
                await capacity_holders[0]
                await asyncio.sleep(0)

            with (
                patch(
                    "tracecat.executor.registry_artifacts.blob.download_file_to_path",
                    new=download_capacity_artifact,
                ),
                patch(
                    "tracecat.executor.registry_artifacts._sleep_registry_artifact_capacity_retry",
                    new=release_one_holder_during_backoff,
                ),
            ):
                async with capacity_cache.lease([cold_uri]) as cold_paths:
                    cold_mount_dir = capacity_cache._paths_for(
                        cold_key
                    ).squashfs_mount_dir
                    payload["capacity_retry_succeeded_after_one_release"] = (
                        cold_paths == [cold_mount_dir] and cold_mount_dir.is_mount()
                    )
                    payload["capacity_released_entry_evicted"] = not capacity_paths[
                        0
                    ].entry_dir.exists()
                    payload["capacity_still_held_entries_preserved"] = all(
                        paths.squashfs_image_path.exists()
                        for paths in capacity_paths[1:]
                    )
                    payload["capacity_still_held_mounts_preserved"] = all(
                        paths.squashfs_mount_dir.is_mount()
                        for paths in capacity_paths[1:]
                    )

            payload["capacity_public_lease_retry_delays"] = len(retry_delays)
            payload["capacity_cold_mount_released"] = not cold_mount_dir.is_mount()
        finally:
            for release in capacity_releases:
                release.set()
            await asyncio.gather(*capacity_holders)

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
