"""Tests for registry sync artifact building utilities."""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest

from tracecat.registry.sync import artifact
from tracecat.registry.sync.artifact import upload_squashfs_venv


@pytest.mark.anyio
async def test_upload_squashfs_venv_uploads_only_squashfs(
    tmp_path: Path,
    mocker,
) -> None:
    squashfs_path = tmp_path / "site-packages.squashfs"
    squashfs_path.write_bytes(b"squashfs")
    upload_file_from_path = mocker.patch(
        "tracecat.registry.sync.artifact.blob.upload_file_from_path",
        mocker.AsyncMock(),
    )

    uri = await artifact.upload_squashfs_venv(
        squashfs_path=squashfs_path,
        key="platform/tarball-venvs/tracecat_registry/v1/site-packages.squashfs",
        bucket="tracecat-registry",
    )

    assert uri == (
        "s3://tracecat-registry/platform/tarball-venvs/tracecat_registry/v1/"
        "site-packages.squashfs"
    )
    upload_file_from_path.assert_awaited_once_with(
        path=squashfs_path,
        key="platform/tarball-venvs/tracecat_registry/v1/site-packages.squashfs",
        bucket="tracecat-registry",
        content_type="application/vnd.squashfs",
    )


@pytest.mark.anyio
async def test_build_squashfs_sidecar_from_tarball_uses_existing_tarball_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "module.py").write_text("VALUE = 1\n")

    tarball_path = tmp_path / "site-packages.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as archive:
        archive.add(package_dir / "module.py", arcname="module.py")

    squashfs_path = tmp_path / "site-packages.squashfs"

    def fake_create_squashfs(
        path: Path,
        entries: list[tuple[Path, str]],
        *,
        preserve_symlinks: bool = True,
    ) -> bool:
        assert preserve_symlinks is True
        assert [(entry_path.name, arcname) for entry_path, arcname in entries] == [
            ("module.py", "module.py")
        ]
        path.write_bytes(b"squashfs")
        return True

    monkeypatch.setattr(artifact, "_create_squashfs_image", fake_create_squashfs)

    created = await artifact.build_squashfs_sidecar_from_tarball(
        tarball_path=tarball_path,
        squashfs_path=squashfs_path,
        work_dir=tmp_path / "extract",
    )

    assert created is True
    assert squashfs_path.read_bytes() == b"squashfs"


def test_get_installed_site_packages_paths_includes_platlib(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve both purelib and platlib when they differ."""
    purelib = tmp_path / "purelib"
    platlib = tmp_path / "platlib"
    purelib.mkdir()
    platlib.mkdir()

    def mock_get_path(name: str) -> str:
        mapping = {"purelib": purelib, "platlib": platlib}
        return str(mapping[name])

    monkeypatch.setattr(artifact.sysconfig, "get_path", mock_get_path)

    paths = artifact.get_installed_site_packages_paths()

    assert paths == [purelib, platlib]


@pytest.mark.anyio
async def test_build_artifact_from_installed_environment_includes_platlib_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Include both purelib and platlib files in the produced SquashFS image."""
    purelib = tmp_path / "purelib"
    platlib = tmp_path / "platlib"
    purelib.mkdir()
    platlib.mkdir()
    (purelib / "pure_only.py").write_text("PURE = True\n")
    (platlib / "plat_only.so").write_bytes(b"binary")

    package_dir = tmp_path / "src" / "tracecat_registry"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("__version__ = '0.0.0'\n")

    monkeypatch.setattr(
        artifact,
        "get_installed_site_packages_paths",
        lambda: [purelib, platlib],
    )

    captured_entries: list[tuple[Path, str]] = []

    def fake_create_squashfs(
        path: Path,
        entries: list[tuple[Path, str]],
        *,
        preserve_symlinks: bool = True,  # noqa: ARG001
    ) -> bool:
        captured_entries.extend(entries)
        path.write_bytes(b"squashfs")
        return True

    monkeypatch.setattr(artifact, "_create_squashfs_image", fake_create_squashfs)

    result = await artifact.build_artifact_from_installed_environment(
        package_name="tracecat_registry",
        package_dir=package_dir,
        output_dir=tmp_path / "out",
    )

    arcnames = {arcname for _, arcname in captured_entries}
    assert "pure_only.py" in arcnames
    assert "plat_only.so" in arcnames
    assert result.squashfs_path.read_bytes() == b"squashfs"
    assert result.artifact_size_bytes == len(b"squashfs")
    assert result.squashfs_name == "site-packages.squashfs"


def test_create_squashfs_image_makes_mount_root_traversable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make the mounted SquashFS root readable by non-root executors."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_text("VALUE = 1\n")
    image_path = tmp_path / "site-packages.squashfs"

    monkeypatch.setattr(
        artifact.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(
        artifact.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_PROCESSORS", 2
    )
    monkeypatch.setattr(artifact.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_MEM", "256M")
    monkeypatch.setattr(artifact.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

    def fake_subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        staging_dir = Path(cmd[1])
        assert staging_dir.stat().st_mode & 0o777 == 0o755
        assert cmd[-4:] == ["-processors", "2", "-mem", "256M"]
        image_path.write_bytes(b"squashfs")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(artifact, "subprocess_run", fake_subprocess_run)

    assert artifact._create_squashfs_image(
        image_path,
        [(source / "module.py", "module.py")],
    )


def test_create_squashfs_image_makes_restrictive_files_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make restrictive files readable after SquashFS -all-root ownership."""
    source = tmp_path / "source"
    source.mkdir()
    module = source / "module.py"
    module.write_text("VALUE = 1\n")
    module.chmod(0o600)
    image_path = tmp_path / "site-packages.squashfs"

    monkeypatch.setattr(
        artifact.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(artifact.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

    def fake_subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        staged_module = Path(cmd[1]) / "module.py"
        assert staged_module.stat().st_mode & 0o777 == 0o644
        assert module.stat().st_mode & 0o777 == 0o600
        image_path.write_bytes(b"squashfs")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(artifact, "subprocess_run", fake_subprocess_run)

    assert artifact._create_squashfs_image(
        image_path,
        [(module, "module.py")],
    )


def test_create_squashfs_image_preserves_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve symlink entries so SquashFS matches tarball semantics."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "target.py").write_text("VALUE = 1\n")
    (source / "linked.py").symlink_to("target.py")
    image_path = tmp_path / "site-packages.squashfs"

    monkeypatch.setattr(
        artifact.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(artifact.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

    def fake_subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        staging_dir = Path(cmd[1])
        staged_link = staging_dir / "linked.py"
        assert staged_link.is_symlink()
        assert staged_link.readlink() == Path("target.py")
        image_path.write_bytes(b"squashfs")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(artifact, "subprocess_run", fake_subprocess_run)

    assert artifact._create_squashfs_image(
        image_path,
        [
            (source / "target.py", "target.py"),
            (source / "linked.py", "linked.py"),
        ],
    )


@pytest.mark.anyio
async def test_build_artifact_from_installed_environment_overlays_symlinked_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip symlink entry and stage real package source for editable installs."""
    purelib = tmp_path / "purelib"
    purelib.mkdir()
    (purelib / "dependency.py").write_text("DEP = True\n")

    package_dir = tmp_path / "src" / "tracecat_registry"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("__version__ = '0.0.0'\n")
    (purelib / "tracecat_registry").symlink_to(package_dir, target_is_directory=True)

    monkeypatch.setattr(
        artifact,
        "get_installed_site_packages_paths",
        lambda: [purelib],
    )
    monkeypatch.setattr(
        artifact.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(artifact.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

    output_dir = tmp_path / "out"
    squashfs_path = output_dir / "site-packages.squashfs"

    def fake_subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        staging_dir = Path(cmd[1])
        staged_package = staging_dir / "tracecat_registry"
        assert staged_package.is_dir()
        assert not staged_package.is_symlink()
        assert (staged_package / "__init__.py").read_text() == "__version__ = '0.0.0'\n"
        squashfs_path.write_bytes(b"squashfs")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(artifact, "subprocess_run", fake_subprocess_run)

    result = await artifact.build_artifact_from_installed_environment(
        package_name="tracecat_registry",
        package_dir=package_dir,
        output_dir=output_dir,
    )

    assert result.squashfs_path == squashfs_path
    assert result.squashfs_path.exists()


@pytest.mark.anyio
async def test_build_artifact_from_installed_environment_skips_unrelated_symlink_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip unrelated symlink entries in installed-environment artifacts."""
    purelib = tmp_path / "purelib"
    purelib.mkdir()
    (purelib / "dependency.py").write_text("DEP = True\n")

    linked_target = tmp_path / "linked_target"
    linked_target.mkdir()
    (linked_target / "__init__.py").write_text("X = True\n")
    (purelib / "linked_dependency").symlink_to(linked_target, target_is_directory=True)

    package_dir = tmp_path / "src" / "tracecat_registry"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("__version__ = '0.0.0'\n")

    monkeypatch.setattr(
        artifact,
        "get_installed_site_packages_paths",
        lambda: [purelib],
    )
    monkeypatch.setattr(
        artifact.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(artifact.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

    output_dir = tmp_path / "out"
    squashfs_path = output_dir / "site-packages.squashfs"

    def fake_subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        staging_dir = Path(cmd[1])
        assert (staging_dir / "dependency.py").exists()
        assert (staging_dir / "tracecat_registry" / "__init__.py").exists()
        assert not (staging_dir / "linked_dependency").exists()
        squashfs_path.write_bytes(b"squashfs")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(artifact, "subprocess_run", fake_subprocess_run)

    result = await artifact.build_artifact_from_installed_environment(
        package_name="tracecat_registry",
        package_dir=package_dir,
        output_dir=output_dir,
    )

    assert result.squashfs_path == squashfs_path
    assert result.squashfs_path.exists()


@pytest.mark.anyio
async def test_upload_squashfs_venv_raises_if_image_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await upload_squashfs_venv(
            squashfs_path=tmp_path / "missing.squashfs",
            key="org/tarball-venvs/x/v1/site-packages.squashfs",
            bucket="tracecat-registry",
        )
