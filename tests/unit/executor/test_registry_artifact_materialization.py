"""Artifact selection, download, and materialization tests."""

from __future__ import annotations

import asyncio
import io
import shutil
import signal
import tarfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.executor.registry_artifacts import (
    SQUASHFS_MOUNT_OPTIONS,
    RegistryArtifactCache,
    RegistryArtifactMaterializationContext,
    SquashfsArtifact,
    SquashfsMountCommandError,
    TarballArtifact,
    _squashfs_listing_size,
    _tarball_extracted_size,
    compute_registry_artifact_cache_key,
)

from .registry_artifact_test_helpers import (
    SQUASHFS_ENABLED_CONFIG,
    BlockingSubprocess,
    CapturedSubprocess,
    SquashfsMountHarness,
    lease_paths,
    tarball_payload,
)


class TestRegistryArtifactMaterialization:
    """Materialize and reuse executor-local artifact formats."""

    @pytest.mark.anyio
    async def test_same_key_cold_fan_in_materializes_and_enforces_once(
        self, temp_cache_dir
    ):
        """Same-key waiters share candidate lookup, materialization, and enforcement."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        materialization_started = asyncio.Event()
        finish_materialization = asyncio.Event()

        async def mock_materialize(
            self: TarballArtifact,
            ctx: RegistryArtifactMaterializationContext,
        ) -> list[Path]:
            materialization_started.set()
            await finish_materialization.wait()
            ctx.paths.tarball_target_dir.mkdir(parents=True)
            return [ctx.paths.tarball_target_dir]

        async def take_lease() -> list[Path]:
            async with cache.lease([artifact_uri]) as paths:
                return paths

        with (
            patch.object(
                cache,
                "_sidecar_exists",
                new_callable=AsyncMock,
                return_value=False,
            ) as sidecar_exists,
            patch.object(
                cache,
                "_enforce_cache_budget",
                new_callable=AsyncMock,
                return_value=True,
            ) as enforce_cache_budget,
            patch.object(cache, "_converge_cache_budget", new_callable=AsyncMock),
            patch.object(TarballArtifact, "materialize", mock_materialize),
        ):
            leases = [asyncio.create_task(take_lease()) for _ in range(5)]
            await materialization_started.wait()
            await asyncio.sleep(0)
            finish_materialization.set()
            results = await asyncio.gather(*leases)

        expected_paths = [cache._paths_for(cache_key).tarball_target_dir]
        assert results == [expected_paths] * 5
        sidecar_exists.assert_awaited_once()
        enforce_cache_budget.assert_awaited_once_with(protected_key=cache_key)

    @pytest.mark.anyio
    async def test_different_cold_keys_serialize_materialization(
        self, temp_cache_dir: Path
    ) -> None:
        """Distinct cold keys cannot multiply staging and extraction peaks."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        uris = ["s3://bucket/first.tar.gz", "s3://bucket/second.tar.gz"]
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        started_keys: list[str] = []

        async def controlled_materialize(
            self: TarballArtifact,
            ctx: RegistryArtifactMaterializationContext,
        ) -> list[Path]:
            del self
            started_keys.append(ctx.cache_key)
            if len(started_keys) == 1:
                first_started.set()
                await release_first.wait()
            else:
                second_started.set()
            ctx.paths.tarball_target_dir.mkdir(parents=True)
            return [ctx.paths.tarball_target_dir]

        async def take_lease(uri: str) -> None:
            async with cache.lease([uri]):
                pass

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "materialize", controlled_materialize),
            patch.object(cache, "_enforce_cache_budget", new_callable=AsyncMock),
            patch.object(cache, "_converge_cache_budget", new_callable=AsyncMock),
        ):
            first = asyncio.create_task(take_lease(uris[0]))
            await first_started.wait()
            second = asyncio.create_task(take_lease(uris[1]))
            await asyncio.sleep(0)
            assert not second_started.is_set()

            release_first.set()
            await second_started.wait()
            await asyncio.gather(first, second)

        assert started_keys == [
            compute_registry_artifact_cache_key(uri) for uri in uris
        ]

    def test_squashfs_listing_size_bounds_each_inode_allocation(self) -> None:
        listing = b"\n".join(
            [
                b"drwxr-xr-x 0/0                      64 2026-01-01 00:00 squashfs-root",
                b"-rw-r--r-- 0/0                     123 2026-01-01 00:00 squashfs-root/module.py",
                b"lrwxrwxrwx 0/0                       9 2026-01-01 00:00 squashfs-root/current -> module.py",
            ]
        )

        assert _squashfs_listing_size(listing, allocation_unit=4096) == 12_288

    def test_squashfs_listing_size_accepts_non_utf8_filenames(self) -> None:
        listing = b"-rw-r--r-- 0/0 123 2026-01-01 00:00 squashfs-root/module-\xff.py"

        assert _squashfs_listing_size(listing, allocation_unit=4096) == 4096

    def test_tarball_size_bounds_each_member_allocation(
        self,
        temp_cache_dir: Path,
    ) -> None:
        tarball_path = temp_cache_dir / "many-small-files.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            for index in range(3):
                member = tarfile.TarInfo(f"module-{index}.py")
                member.size = 1
                tar.addfile(member, io.BytesIO(b"x"))

        assert _tarball_extracted_size(tarball_path, allocation_unit=4096) == 16_384

    def test_tarball_size_includes_extraction_root(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """Extraction reserves its root even when the manifest omits it."""
        tarball_path = temp_cache_dir / "implicit-root.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            member = tarfile.TarInfo("module.py")
            member.size = 0
            tar.addfile(member, io.BytesIO())

        assert _tarball_extracted_size(tarball_path, allocation_unit=4096) == 8192

    def test_tarball_size_does_not_duplicate_explicit_root(
        self,
        temp_cache_dir: Path,
    ) -> None:
        tarball_path = temp_cache_dir / "explicit-root.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            root = tarfile.TarInfo(".")
            root.type = tarfile.DIRTYPE
            tar.addfile(root)

        assert _tarball_extracted_size(tarball_path, allocation_unit=4096) == 4096

    def test_tarball_size_includes_implicit_parent_directories(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """Extraction reserves directories omitted from the tar manifest."""
        tarball_path = temp_cache_dir / "implicit-directories.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            member = tarfile.TarInfo("one/two/three/module.py")
            member.size = 0
            tar.addfile(member, io.BytesIO())

        assert _tarball_extracted_size(tarball_path, allocation_unit=4096) == 20_480

    def test_squashfs_listing_size_rejects_unparseable_files(self) -> None:
        with pytest.raises(ValueError, match="Could not parse SquashFS listing"):
            _squashfs_listing_size(b"-rw-r--r-- malformed")

    @pytest.mark.anyio
    async def test_materialize_mounts_squashfs_sidecar(self, temp_cache_dir):
        """Test that a SquashFS sidecar is mounted instead of extracting tarballs."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def mock_mount(self, ctx, image_path):
            assert image_path.name.endswith(".squashfs")
            target_dir = ctx.paths.squashfs_mount_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            return target_dir

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(
                TarballArtifact,
                "materialize",
                new_callable=AsyncMock,
            ) as tarball_materialize,
        ):
            result = await lease_paths(
                cache,
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        tarball_materialize.assert_not_awaited()

    @pytest.mark.anyio
    async def test_mount_squashfs_uses_hardened_read_only_options(
        self,
        temp_cache_dir,
    ):
        """Test that SquashFS images are mounted read-only without device/setuid bits."""
        cache_key = "cache-key"
        cache = RegistryArtifactCache(temp_cache_dir)
        ctx = cache._context_for(cache_key)
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key=cache_key,
        )
        image_path = ctx.paths.squashfs_image_path
        target_dir = ctx.paths.squashfs_mount_dir
        ctx.paths.entry_dir.mkdir(parents=True)
        image_path.write_bytes(b"squashfs")
        target_dir.mkdir()
        process = AsyncMock()
        process.pid = 1234
        process.communicate.return_value = (b"", b"")
        process.returncode = 0

        with (
            patch(
                "tracecat.executor.registry_artifact_materialization.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ) as create_subprocess_exec,
            patch(
                "tracecat.sandbox.utils.terminate_process_group",
                new_callable=AsyncMock,
            ) as terminate_process_group,
        ):
            await artifact.mount(ctx, image_path)

        create_subprocess_exec.assert_awaited_once_with(
            "mount",
            "-t",
            "squashfs",
            "-o",
            SQUASHFS_MOUNT_OPTIONS,
            str(image_path),
            str(target_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        terminate_process_group.assert_awaited_once_with(process)

    @pytest.mark.anyio
    async def test_cancelled_mount_kills_and_reaps_subprocess(self, temp_cache_dir):
        """Cancellation cannot leave an orphan mount process after lock release."""
        cache_key = "cancelled-mount"
        cache = RegistryArtifactCache(temp_cache_dir)
        ctx = cache._context_for(cache_key)
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key=cache_key,
        )
        image_path = ctx.paths.squashfs_image_path
        target_dir = ctx.paths.squashfs_mount_dir
        ctx.paths.entry_dir.mkdir(parents=True)
        image_path.write_bytes(b"squashfs")
        target_dir.mkdir()
        process = BlockingSubprocess()

        with (
            patch(
                "tracecat.executor.registry_artifact_materialization.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ),
            patch("tracecat.sandbox.utils.os.killpg") as kill_group,
        ):
            mounting = asyncio.create_task(
                artifact._mount_image(image_path, target_dir)
            )
            await process.communicate_started.wait()
            mounting.cancel()

            with pytest.raises(asyncio.CancelledError):
                await mounting

        assert process.cleanup_calls == ["kill", "wait"]
        kill_group.assert_called_once_with(process.pid, signal.SIGKILL)
        assert target_dir.is_dir()
        assert not target_dir.is_mount()

    @pytest.mark.anyio
    async def test_cancelled_squashfs_extract_kills_and_reaps_subprocess(
        self, temp_cache_dir
    ):
        """Cancellation cannot leave unsquashfs writing into scratch."""
        cache_key = "cancelled-extract"
        cache = RegistryArtifactCache(temp_cache_dir)
        ctx = cache._context_for(cache_key)
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key=cache_key,
        )
        image_path = ctx.paths.squashfs_image_path
        target_dir = ctx.paths.squashfs_extract_dir
        ctx.paths.entry_dir.mkdir(parents=True)
        image_path.write_bytes(b"squashfs")
        target_dir.mkdir()
        real_create_subprocess_exec = asyncio.create_subprocess_exec
        process_started = asyncio.Event()
        captured_processes: list[CapturedSubprocess] = []

        async def create_sleep_subprocess(
            *args: object, **kwargs: object
        ) -> CapturedSubprocess:
            del args, kwargs
            process = await real_create_subprocess_exec(
                "/bin/sleep",
                "30",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            captured = CapturedSubprocess(process)
            captured_processes.append(captured)
            process_started.set()
            return captured

        with (
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/usr/bin/unsquashfs",
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.asyncio.create_subprocess_exec",
                side_effect=create_sleep_subprocess,
            ),
        ):
            extracting = asyncio.create_task(
                artifact._extract_image(image_path, target_dir)
            )
            await process_started.wait()
            captured = captured_processes[0]
            extracting.cancel()

            production_killed = False
            production_reaped = False
            try:
                with pytest.raises(asyncio.CancelledError):
                    await extracting
                production_killed = captured.killed
                production_reaped = captured.reaped
            finally:
                if captured.returncode is None:
                    captured.process.kill()
                    await captured.process.wait()

        assert production_killed is True
        assert production_reaped is True
        assert captured.returncode is not None

    @pytest.mark.parametrize("operation", ["mount", "extract", "size"])
    @pytest.mark.anyio
    async def test_repeated_cancellation_reaps_squashfs_subprocess(
        self,
        temp_cache_dir: Path,
        operation: str,
    ) -> None:
        """A second cancellation cannot abandon a killed SquashFS child."""
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key="repeated-subprocess-cancellation",
        )
        image_path = temp_cache_dir / "image.squashfs"
        image_path.write_bytes(b"squashfs")
        target_dir = temp_cache_dir / "target"
        target_dir.mkdir()
        process = BlockingSubprocess(block_wait=True)

        with (
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/usr/bin/unsquashfs",
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ) as create_subprocess_exec,
            patch("tracecat.sandbox.utils.os.killpg") as kill_group,
        ):
            if operation == "mount":
                running = asyncio.create_task(
                    artifact._mount_image(image_path, target_dir)
                )
            elif operation == "extract":
                running = asyncio.create_task(
                    artifact._extract_image(image_path, target_dir)
                )
            else:
                running = asyncio.create_task(
                    artifact._squashfs_extracted_size(image_path)
                )

            await process.communicate_started.wait()
            running.cancel()
            await process.wait_started.wait()

            running.cancel()
            done, _ = await asyncio.wait({running}, timeout=0.05)
            second_cancellation_propagated_early = bool(done)
            process.release_wait.set()

            with pytest.raises(asyncio.CancelledError):
                await running

        assert second_cancellation_propagated_early is False
        assert process.cleanup_calls == ["kill", "wait"]
        await_args = create_subprocess_exec.await_args
        assert await_args is not None
        assert await_args.kwargs["start_new_session"] is True
        kill_group.assert_called_once_with(process.pid, signal.SIGKILL)

    @pytest.mark.anyio
    async def test_repeatedly_cancelled_tarball_extract_rejoins_thread(
        self, temp_cache_dir
    ):
        """Repeated cancellation waits until the tar extractor stops writing."""
        artifact = TarballArtifact(
            uri="s3://bucket/path/site-packages.tar.gz",
            cache_key="cancelled-tarball-extract",
        )
        tarball_path = temp_cache_dir / "artifact.tar.gz"
        target_dir = temp_cache_dir / "target"
        target_dir.mkdir()
        with tarfile.open(tarball_path, "w:gz"):
            pass

        extraction_started = threading.Event()
        extraction_release = threading.Event()
        extraction_finished = threading.Event()

        def blocking_extractall(*args: object, **kwargs: object) -> None:
            del args, kwargs
            extraction_started.set()
            extraction_release.wait()
            extraction_finished.set()

        with patch.object(
            tarfile.TarFile,
            "extractall",
            new=blocking_extractall,
        ):
            extracting = asyncio.create_task(artifact.extract(tarball_path, target_dir))
            assert await asyncio.to_thread(extraction_started.wait, 1)
            extracting.cancel()
            done, _ = await asyncio.wait({extracting}, timeout=0.05)
            first_cancellation_propagated_early = bool(done)

            extracting.cancel()
            done, _ = await asyncio.wait({extracting}, timeout=0.05)
            second_cancellation_propagated_early = bool(done)
            extraction_release.set()

            with pytest.raises(asyncio.CancelledError):
                await extracting

        assert extraction_finished.is_set()
        assert first_cancellation_propagated_early is False
        assert second_cancellation_propagated_early is False

    @pytest.mark.anyio
    async def test_repeatedly_cancelled_tarball_size_scan_rejoins_thread(
        self, temp_cache_dir
    ):
        """Cancellation cannot unlink a tarball while its size scan is running."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/slow-size-scan.tar.gz"
        downloaded_paths: list[Path] = []
        scan_started = threading.Event()
        scan_release = threading.Event()
        scan_finished = threading.Event()
        input_present_at_finish: list[bool] = []

        async def mock_download(self, ctx, path):
            del self, ctx
            path.write_bytes(tarball_payload(size=1))
            downloaded_paths.append(path)

        def blocking_size_scan(path: Path, *, allocation_unit: int) -> int:
            assert allocation_unit == 1
            scan_started.set()
            scan_release.wait()
            input_present_at_finish.append(path.exists())
            scan_finished.set()
            return 1

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "download", mock_download),
            patch(
                "tracecat.executor.registry_artifact_materialization._tarball_extracted_size",
                side_effect=blocking_size_scan,
            ),
            patch.object(
                TarballArtifact,
                "extract",
                new_callable=AsyncMock,
            ) as extract,
        ):
            materializing = asyncio.create_task(lease_paths(cache, artifact_uri))
            assert await asyncio.to_thread(scan_started.wait, 1)
            materializing.cancel()
            done, _ = await asyncio.wait({materializing}, timeout=0.05)
            first_cancellation_propagated_early = bool(done)

            materializing.cancel()
            done, _ = await asyncio.wait({materializing}, timeout=0.05)
            second_cancellation_propagated_early = bool(done)
            assert downloaded_paths[0].exists()
            scan_release.set()

            with pytest.raises(asyncio.CancelledError):
                await materializing

        assert scan_finished.is_set()
        assert input_present_at_finish == [True]
        assert not downloaded_paths[0].exists()
        assert first_cancellation_propagated_early is False
        assert second_cancellation_propagated_early is False
        extract.assert_not_awaited()

    @pytest.mark.anyio
    async def test_failed_partial_cleanup_is_deferred_for_capacity_retry(
        self, temp_cache_dir
    ):
        """A failed staging-tree deletion remains discoverable and retryable."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/failed-cleanup.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = TarballArtifact(uri=artifact_uri, cache_key=cache_key)
        ctx = cache._context_for(cache_key)
        cleanup_attempts: list[Path] = []

        async def download(self, ctx, path):
            del self, ctx
            path.write_bytes(b"archive")

        async def fail_extract(self, tarball_path, target_dir):
            del self, tarball_path
            (target_dir / "partial.py").write_text("partial")
            raise RuntimeError("extraction failed")

        def fail_cleanup(path: Path) -> None:
            cleanup_attempts.append(path)
            raise PermissionError("cleanup denied")

        with (
            patch.object(TarballArtifact, "download", download),
            patch.object(TarballArtifact, "extract", fail_extract),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.rmtree",
                side_effect=fail_cleanup,
            ),
        ):
            with pytest.raises(RuntimeError, match="extraction failed"):
                await artifact.materialize(ctx)

        assert len(cleanup_attempts) == 1
        assert set(cleanup_attempts) == cache._deferred_staging_cleanup
        deferred_path = cleanup_attempts[0]
        assert deferred_path.is_dir()

        assert cache._retry_deferred_staging_cleanup() is True
        assert cache._deferred_staging_cleanup == set()
        assert not deferred_path.exists()

    @pytest.mark.anyio
    async def test_failed_tarball_unlink_is_deferred_without_masking_success(
        self, temp_cache_dir
    ):
        """A failed tarball unlink preserves success and remains retryable."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/failed-tarball-cleanup.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = TarballArtifact(uri=artifact_uri, cache_key=cache_key)
        ctx = cache._context_for(cache_key)
        downloaded_paths: list[Path] = []
        real_unlink = Path.unlink

        async def download(self, ctx, path):
            del self, ctx
            path.write_bytes(b"archive")
            downloaded_paths.append(path)

        async def extract(self, tarball_path, target_dir):
            del self, tarball_path
            (target_dir / "module.py").write_text("VALUE = 1")

        def fail_download_unlink(
            path: Path,
            missing_ok: bool = False,
        ) -> None:
            if path in downloaded_paths:
                raise PermissionError("cleanup denied")
            real_unlink(path, missing_ok=missing_ok)

        with (
            patch.object(TarballArtifact, "download", download),
            patch.object(TarballArtifact, "extract", extract),
            patch.object(Path, "unlink", fail_download_unlink),
        ):
            result = await artifact.materialize(ctx)

        assert result == [ctx.paths.tarball_target_dir]
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert len(downloaded_paths) == 1
        deferred_path = downloaded_paths[0]
        assert cache._deferred_staging_cleanup == {deferred_path}
        assert deferred_path.exists()

        assert cache._retry_deferred_staging_cleanup() is True
        assert cache._deferred_staging_cleanup == set()
        assert not deferred_path.exists()

    @pytest.mark.anyio
    async def test_failed_squashfs_unlink_is_deferred_after_concurrent_publish(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """A losing SquashFS staging file remains retryable without masking success."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/concurrent.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = SquashfsArtifact(uri=artifact_uri, cache_key=cache_key)
        ctx = cache._context_for(cache_key)
        image_path = ctx.paths.squashfs_image_path
        staging_paths: list[Path] = []
        real_rename = Path.rename
        real_unlink = Path.unlink

        async def download(
            artifact_uri: str,
            output_path: Path,
            *,
            admission: object,
            defer_cleanup: object,
        ) -> None:
            del artifact_uri, admission, defer_cleanup
            output_path.write_bytes(b"loser")
            staging_paths.append(output_path)

        def publish_concurrently(path: Path, target: Path) -> Path:
            if path in staging_paths:
                target.write_bytes(b"winner")
                raise FileExistsError("published by another process")
            return real_rename(path, target)

        def fail_staging_unlink(path: Path, missing_ok: bool = False) -> None:
            if path in staging_paths:
                raise PermissionError("cleanup denied")
            real_unlink(path, missing_ok=missing_ok)

        with (
            patch(
                "tracecat.executor.registry_artifact_materialization."
                "_download_s3_artifact",
                side_effect=download,
            ),
            patch.object(Path, "rename", publish_concurrently),
            patch.object(Path, "unlink", fail_staging_unlink),
        ):
            await artifact.download(ctx, image_path)

        assert image_path.read_bytes() == b"winner"
        assert len(staging_paths) == 1
        deferred_path = staging_paths[0]
        assert deferred_path.exists()
        assert cache._deferred_staging_cleanup == {deferred_path}

        assert cache._retry_deferred_staging_cleanup() is True
        assert cache._deferred_staging_cleanup == set()
        assert not deferred_path.exists()

    def test_failed_unusable_squashfs_unlink_is_deferred(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """A failed canonical-image cleanup remains retryable after fallback."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/unusable.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = SquashfsArtifact(uri=artifact_uri, cache_key=cache_key)
        ctx = cache._context_for(cache_key)
        image_path = ctx.paths.squashfs_image_path
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"unusable")
        real_unlink = Path.unlink

        def fail_image_unlink(path: Path, missing_ok: bool = False) -> None:
            if path == image_path:
                raise PermissionError("cleanup denied")
            real_unlink(path, missing_ok=missing_ok)

        with patch.object(Path, "unlink", fail_image_unlink):
            artifact.discard_failed_materialization(ctx)

        assert cache._deferred_staging_cleanup == {image_path}
        assert image_path.is_file()
        assert cache._retry_deferred_staging_cleanup() is True
        assert cache._deferred_staging_cleanup == set()
        assert not image_path.exists()

    @pytest.mark.parametrize("artifact_format", ["squashfs", "tarball"])
    @pytest.mark.anyio
    async def test_repeatedly_cancelled_partial_cleanup_rejoins_thread(
        self,
        temp_cache_dir,
        artifact_format: str,
    ):
        """Large partial environments are deleted off-loop before cancellation."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cleanup_started = threading.Event()
        cleanup_release = threading.Event()
        cleanup_finished = threading.Event()
        real_rmtree = shutil.rmtree

        def blocking_rmtree(path: Path, *, ignore_errors: bool = False) -> None:
            cleanup_started.set()
            cleanup_release.wait()
            real_rmtree(path, ignore_errors=ignore_errors)
            cleanup_finished.set()

        async def assert_cleanup(task: asyncio.Task[list[Path]]) -> None:
            assert await asyncio.to_thread(cleanup_started.wait, 1)
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=0.05)
            first_cancellation_propagated_early = bool(done)

            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=0.05)
            second_cancellation_propagated_early = bool(done)
            cleanup_release.set()

            with pytest.raises(asyncio.CancelledError):
                await task

            assert cleanup_finished.is_set()
            assert first_cancellation_propagated_early is False
            assert second_cancellation_propagated_early is False
            assert not cache.staging_dir.exists() or not any(
                cache.staging_dir.iterdir()
            )

        async def fail_tarball_extract(self, tarball_path, target_dir):
            del self, tarball_path
            target_dir.mkdir(parents=True)
            (target_dir / "partial.py").write_text("partial")
            raise RuntimeError("tarball extraction failed")

        async def download_tarball(self, ctx, path):
            del self, ctx
            path.write_bytes(tarball_payload(size=1))

        async def download_squashfs(self, ctx, image_path):
            del self, ctx
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"image")
            return 0.0

        async def fail_squashfs_extract(self, image_path, target_dir):
            del self, image_path
            target_dir.mkdir(parents=True)
            (target_dir / "partial.py").write_text("partial")
            raise RuntimeError("SquashFS extraction failed")

        with patch(
            "tracecat.executor.registry_artifact_materialization.shutil.rmtree",
            side_effect=blocking_rmtree,
        ):
            if artifact_format == "tarball":
                with (
                    patch(SQUASHFS_ENABLED_CONFIG, False),
                    patch.object(TarballArtifact, "download", download_tarball),
                    patch.object(TarballArtifact, "extract", fail_tarball_extract),
                ):
                    task = asyncio.create_task(
                        lease_paths(cache, "s3://bucket/partial.tar.gz")
                    )
                    await assert_cleanup(task)
            else:
                with (
                    patch.object(
                        RegistryArtifactMaterializationContext,
                        "can_mount_squashfs",
                        return_value=False,
                    ),
                    patch.object(SquashfsArtifact, "download", download_squashfs),
                    patch.object(
                        SquashfsArtifact,
                        "_squashfs_extracted_size",
                        new_callable=AsyncMock,
                        return_value=1,
                    ),
                    patch.object(
                        SquashfsArtifact,
                        "_extract_image",
                        fail_squashfs_extract,
                    ),
                ):
                    task = asyncio.create_task(
                        lease_paths(cache, "s3://bucket/partial.squashfs")
                    )
                    await assert_cleanup(task)

    @pytest.mark.anyio
    async def test_materialize_extracts_squashfs_when_mount_fails(self, temp_cache_dir):
        """Test that SquashFS mount failures fall back to unsquashfs extraction."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def mock_mount(self, ctx, image_path):
            raise SquashfsMountCommandError("operation not permitted")

        async def mock_extract(self, ctx, image_path):
            target_dir = ctx.paths.squashfs_extract_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            return target_dir

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(SquashfsArtifact, "extract", mock_extract),
            patch.object(
                TarballArtifact,
                "materialize",
                new_callable=AsyncMock,
            ) as tarball_materialize,
        ):
            result = await lease_paths(
                cache,
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert result[0].name == "extracted"
        tarball_materialize.assert_not_awaited()

    @pytest.mark.anyio
    async def test_materialize_extracts_squashfs_without_mount_binary(
        self, temp_cache_dir
    ):
        """Test that SquashFS is still preferred when only unsquashfs is available."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def mock_extract(self, ctx, image_path):
            target_dir = ctx.paths.squashfs_extract_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            return target_dir

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value=None,
            ),
            patch.object(SquashfsArtifact, "extract", mock_extract),
        ):
            result = await lease_paths(
                cache,
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert result[0].name == "extracted"

    @pytest.mark.anyio
    async def test_materialize_falls_back_to_gzip_when_squashfs_extract_fails(
        self, temp_cache_dir
    ):
        """Test that legacy gzip remains the final compatibility fallback."""
        cache = RegistryArtifactCache(temp_cache_dir)
        source = temp_cache_dir / "source"
        source.mkdir()
        (source / "module.py").write_text("VALUE = 1")

        async def mock_tarball_download(self, ctx, path):
            with tarfile.open(path, "w:gz") as tar:
                tar.add(source / "module.py", arcname="module.py")

        async def mock_mount(self, ctx, image_path):
            raise SquashfsMountCommandError("operation not permitted")

        async def mock_extract(self, ctx, image_path):
            raise RuntimeError("unsquashfs unavailable")

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                side_effect=[True, False],
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(SquashfsArtifact, "extract", mock_extract),
            patch.object(TarballArtifact, "download", mock_tarball_download),
        ):
            result = await lease_paths(
                cache,
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert result[0].name == "tarball"

    @pytest.mark.anyio
    async def test_materialize_treats_unknown_suffix_as_gzip(self, temp_cache_dir):
        """Test that existing gzip artifacts can use arbitrary S3 key suffixes."""
        cache = RegistryArtifactCache(temp_cache_dir)
        source = temp_cache_dir / "source"
        source.mkdir()
        (source / "module.py").write_text("VALUE = 1")

        async def mock_download(self, ctx, path):
            assert path.name.endswith(".tar.gz")
            with tarfile.open(path, "w:gz") as tar:
                tar.add(source / "module.py", arcname="module.py")

        with patch.object(TarballArtifact, "download", mock_download):
            result = await lease_paths(
                cache,
                "s3://bucket/path/custom-key",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"

    @pytest.mark.anyio
    async def test_materialize_caches_result(self, temp_cache_dir):
        """Test that tarball extraction is cached."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/test.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = cache._paths_for(cache_key).tarball_target_dir
        target_dir.mkdir(parents=True)

        result = await lease_paths(cache, artifact_uri)

        assert result == [target_dir]

    @pytest.mark.anyio
    async def test_materialize_concurrent_requests(self, temp_cache_dir):
        """Test that concurrent requests for same artifact do not race."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/test.tar.gz"
        download_count = 0

        async def mock_download(self, ctx, path):
            nonlocal download_count
            download_count += 1
            await asyncio.sleep(0.1)
            path.write_bytes(tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "extracted.txt").write_text("extracted")

        with (
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            results = await asyncio.gather(
                lease_paths(cache, artifact_uri),
                lease_paths(cache, artifact_uri),
                lease_paths(cache, artifact_uri),
            )

        assert all(r == results[0] for r in results)
        assert download_count == 1


class TestSquashfsMountPolicy:
    """Tests for per-artifact SquashFS fallback."""

    @pytest.mark.anyio
    async def test_loop_device_exhaustion_isolated_sticky_extraction_fallback(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect the cache's fail-open policy when loop devices are saturated.

        A mount-command failure must extract only the affected cold artifact,
        preserve already-leased mounts, reuse that extraction on later leases,
        and still let unrelated artifacts attempt mounting. This models loop
        exhaustion deterministically without consuming host-global devices.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        held_uri = "s3://bucket/already-mounted.squashfs"
        saturated_uri = "s3://bucket/no-loop-available.squashfs"
        later_uri = "s3://bucket/later-artifact.squashfs"
        held_key = compute_registry_artifact_cache_key(held_uri)
        saturated_key = compute_registry_artifact_cache_key(saturated_uri)
        later_key = compute_registry_artifact_cache_key(later_uri)
        harness = SquashfsMountHarness(
            cache,
            failed_mount_keys={saturated_key},
        )

        with (
            patch(
                "tracecat.executor.registry_artifact_mounts.is_mount",
                lambda path: path in harness.mounted,
            ),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", harness.mount),
            patch.object(SquashfsArtifact, "extract", harness.extract),
            patch.object(cache, "_unmount", harness.unmount),
        ):
            async with cache.lease([held_uri]) as held_paths:
                held_mount = cache._paths_for(held_key).squashfs_mount_dir
                assert held_paths == [held_mount]

                async with cache.lease([saturated_uri]) as saturated_paths:
                    extracted = cache._paths_for(saturated_key).squashfs_extract_dir
                    assert saturated_paths == [extracted]
                    assert held_mount in harness.mounted
                    assert cache._refcount(held_key) == 1
                    assert harness.unmounts == []

                # The extracted directory is the cache entry's sticky path;
                # freeing a loop elsewhere does not trigger a remount attempt.
                async with cache.lease([saturated_uri]) as cached_paths:
                    assert cached_paths == [extracted]

                async with cache.lease([later_uri]) as later_paths:
                    later_mount = cache._paths_for(later_key).squashfs_mount_dir
                    assert later_paths == [later_mount]
                    assert held_mount in harness.mounted

                assert held_mount in harness.mounted

        assert harness.mount_attempts == [held_key, saturated_key, later_key]
        assert harness.extraction_attempts == [saturated_key]
        assert harness.unmounts == [later_mount, held_mount]
        assert cache._paths_for(saturated_key).squashfs_image_path.is_file()
        assert cache._paths_for(saturated_key).squashfs_extract_dir.is_dir()
        assert cache._refcount(held_key) == 0
        assert cache._refcount(saturated_key) == 0
        assert cache._refcount(later_key) == 0

    @pytest.mark.anyio
    async def test_mount_failure_does_not_disable_later_artifacts(
        self, temp_cache_dir
    ) -> None:
        """One failed mount falls back without changing later mount attempts."""
        cache = RegistryArtifactCache(temp_cache_dir)
        first_ctx = cache._context_for("first")
        second_ctx = cache._context_for("second")
        first = SquashfsArtifact(
            uri="s3://bucket/first.squashfs",
            cache_key="first",
        )
        second = SquashfsArtifact(
            uri="s3://bucket/second.squashfs",
            cache_key="second",
        )
        mount_attempts: list[str] = []

        async def mock_mount(self, ctx, image_path):
            del image_path
            mount_attempts.append(ctx.cache_key)
            if ctx.cache_key == "first":
                raise SquashfsMountCommandError("operation not permitted")
            ctx.paths.squashfs_mount_dir.mkdir(parents=True)
            return ctx.paths.squashfs_mount_dir

        async def mock_extract(self, ctx, image_path):
            del image_path
            ctx.paths.squashfs_extract_dir.mkdir(parents=True)
            return ctx.paths.squashfs_extract_dir

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(SquashfsArtifact, "extract", mock_extract),
        ):
            assert await first.materialize(first_ctx) == [
                first_ctx.paths.squashfs_extract_dir
            ]
            assert await second.materialize(second_ctx) == [
                second_ctx.paths.squashfs_mount_dir
            ]

        assert mount_attempts == ["first", "second"]
