"""Tests for registry sync tarball building utilities."""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest

from tracecat.registry.sync import tarball
from tracecat.registry.sync.tarball import upload_tarball_venv


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

    monkeypatch.setattr(tarball.sysconfig, "get_path", mock_get_path)

    paths = tarball.get_installed_site_packages_paths()

    assert paths == [purelib, platlib]


@pytest.mark.anyio
async def test_build_tarball_from_installed_environment_includes_platlib_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Include both purelib and platlib files in the produced tarballs."""
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
        tarball,
        "get_installed_site_packages_paths",
        lambda: [purelib, platlib],
    )

    def fake_create_squashfs(
        path: Path,
        _entries: object,
        *,
        preserve_symlinks: bool = True,  # noqa: ARG001
    ) -> bool:
        path.write_bytes(b"squashfs")
        return True

    monkeypatch.setattr(tarball, "_create_squashfs_image", fake_create_squashfs)

    result = await tarball.build_tarball_venv_from_installed_environment(
        package_name="tracecat_registry",
        package_dir=package_dir,
        output_dir=tmp_path / "out",
    )

    with tarfile.open(result.tarball_path, "r:gz") as archive:
        tar_names = set(archive.getnames())

    assert "pure_only.py" in tar_names
    assert "plat_only.so" in tar_names
    assert result.squashfs_path is not None
    assert result.squashfs_path.read_bytes() == b"squashfs"
    assert result.squashfs_size_bytes == len(b"squashfs")


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
        tarball.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(tarball.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

    def fake_subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        staging_dir = Path(cmd[1])
        assert staging_dir.stat().st_mode & 0o777 == 0o755
        image_path.write_bytes(b"squashfs")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(tarball, "subprocess_run", fake_subprocess_run)

    assert tarball._create_squashfs_image(
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
        tarball.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(tarball.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

    def fake_subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        staged_module = Path(cmd[1]) / "module.py"
        assert staged_module.stat().st_mode & 0o777 == 0o644
        assert module.stat().st_mode & 0o777 == 0o600
        image_path.write_bytes(b"squashfs")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(tarball, "subprocess_run", fake_subprocess_run)

    assert tarball._create_squashfs_image(
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
        tarball.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(tarball.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

    def fake_subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        staging_dir = Path(cmd[1])
        staged_link = staging_dir / "linked.py"
        assert staged_link.is_symlink()
        assert staged_link.readlink() == Path("target.py")
        image_path.write_bytes(b"squashfs")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(tarball, "subprocess_run", fake_subprocess_run)

    assert tarball._create_squashfs_image(
        image_path,
        [
            (source / "target.py", "target.py"),
            (source / "linked.py", "linked.py"),
        ],
    )


@pytest.mark.anyio
async def test_build_tarball_from_installed_environment_overlays_symlinked_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip symlink entry and archive real package source for editable installs."""
    purelib = tmp_path / "purelib"
    purelib.mkdir()
    (purelib / "dependency.py").write_text("DEP = True\n")

    package_dir = tmp_path / "src" / "tracecat_registry"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("__version__ = '0.0.0'\n")
    (purelib / "tracecat_registry").symlink_to(package_dir, target_is_directory=True)

    monkeypatch.setattr(
        tarball,
        "get_installed_site_packages_paths",
        lambda: [purelib],
    )
    monkeypatch.setattr(
        tarball.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(tarball.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

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

    monkeypatch.setattr(tarball, "subprocess_run", fake_subprocess_run)

    result = await tarball.build_tarball_venv_from_installed_environment(
        package_name="tracecat_registry",
        package_dir=package_dir,
        output_dir=output_dir,
    )

    assert result.squashfs_path == squashfs_path

    with tarfile.open(result.tarball_path, "r:gz") as archive:
        tracecat_members = [
            member
            for member in archive.getmembers()
            if member.name == "tracecat_registry"
        ]
        assert tracecat_members
        assert all(not member.issym() for member in tracecat_members)

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        archive.extractall(path=extract_dir, filter="data")

    extracted_package = extract_dir / "tracecat_registry" / "__init__.py"
    assert extracted_package.exists()
    assert not (extract_dir / "tracecat_registry").is_symlink()


@pytest.mark.anyio
async def test_build_tarball_from_installed_environment_skips_unrelated_symlink_entries(
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
        tarball,
        "get_installed_site_packages_paths",
        lambda: [purelib],
    )
    monkeypatch.setattr(
        tarball.config, "TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED", True
    )
    monkeypatch.setattr(tarball.shutil, "which", lambda _name: "/usr/bin/mksquashfs")

    output_dir = tmp_path / "out"
    squashfs_path = output_dir / "site-packages.squashfs"

    def fake_subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        staging_dir = Path(cmd[1])
        assert (staging_dir / "dependency.py").exists()
        assert (staging_dir / "tracecat_registry" / "__init__.py").exists()
        assert not (staging_dir / "linked_dependency").exists()
        squashfs_path.write_bytes(b"squashfs")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(tarball, "subprocess_run", fake_subprocess_run)

    result = await tarball.build_tarball_venv_from_installed_environment(
        package_name="tracecat_registry",
        package_dir=package_dir,
        output_dir=output_dir,
    )

    with tarfile.open(result.tarball_path, "r:gz") as archive:
        linked_members = [
            member
            for member in archive.getmembers()
            if member.name == "linked_dependency"
            or member.name.startswith("linked_dependency/")
        ]
        assert not linked_members

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        archive.extractall(path=extract_dir, filter="data")

    assert (extract_dir / "dependency.py").exists()
    assert (extract_dir / "tracecat_registry" / "__init__.py").exists()
    assert not (extract_dir / "linked_dependency").exists()
    assert result.squashfs_path == squashfs_path


@pytest.mark.anyio
async def test_upload_tarball_venv_uploads_squashfs_sidecar(
    tmp_path: Path,
    mocker,
) -> None:
    tarball_path = tmp_path / "site-packages.tar.gz"
    squashfs_path = tmp_path / "site-packages.squashfs"
    tarball_path.write_bytes(b"gzip")
    squashfs_path.write_bytes(b"squashfs")
    upload_file_from_path = mocker.patch(
        "tracecat.registry.sync.tarball.blob.upload_file_from_path",
        mocker.AsyncMock(),
    )

    uri = await upload_tarball_venv(
        tarball_path=tarball_path,
        squashfs_path=squashfs_path,
        key="org/tarball-venvs/tracecat_registry/v1/site-packages.tar.gz",
        bucket="tracecat-registry",
    )

    assert uri == (
        "s3://tracecat-registry/org/tarball-venvs/tracecat_registry/v1/"
        "site-packages.tar.gz"
    )
    assert upload_file_from_path.await_count == 2
    upload_file_from_path.assert_has_awaits(
        [
            mocker.call(
                path=tarball_path,
                key="org/tarball-venvs/tracecat_registry/v1/site-packages.tar.gz",
                bucket="tracecat-registry",
                content_type="application/gzip",
            ),
            mocker.call(
                path=squashfs_path,
                key="org/tarball-venvs/tracecat_registry/v1/site-packages.squashfs",
                bucket="tracecat-registry",
                content_type="application/vnd.squashfs",
            ),
        ]
    )
