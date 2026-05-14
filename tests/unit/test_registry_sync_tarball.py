"""Tests for registry sync tarball building utilities."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
import zstandard as zstd

from tracecat.registry.sync import tarball
from tracecat.registry.sync.tarball import upload_tarball_venv


def _zstd_tar_names(path: Path) -> set[str]:
    decompressor = zstd.ZstdDecompressor()
    with path.open("rb") as raw:
        with decompressor.stream_reader(raw, closefd=False) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                return {member.name for member in archive}


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

    result = await tarball.build_tarball_venv_from_installed_environment(
        package_name="tracecat_registry",
        package_dir=package_dir,
        output_dir=tmp_path / "out",
    )

    with tarfile.open(result.tarball_path, "r:gz") as archive:
        tar_names = set(archive.getnames())

    assert "pure_only.py" in tar_names
    assert "plat_only.so" in tar_names
    assert result.zstd_compressed_size_bytes > 0
    zstd_tar_names = _zstd_tar_names(result.zstd_tarball_path)
    assert "pure_only.py" in zstd_tar_names
    assert "plat_only.so" in zstd_tar_names


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

    result = await tarball.build_tarball_venv_from_installed_environment(
        package_name="tracecat_registry",
        package_dir=package_dir,
        output_dir=tmp_path / "out",
    )

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
    """Skip unrelated symlink entries to keep tarballs extractable and small."""
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

    result = await tarball.build_tarball_venv_from_installed_environment(
        package_name="tracecat_registry",
        package_dir=package_dir,
        output_dir=tmp_path / "out",
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


@pytest.mark.anyio
async def test_upload_tarball_venv_uploads_zstd_sidecar(
    tmp_path: Path,
    mocker,
) -> None:
    tarball_path = tmp_path / "site-packages.tar.gz"
    zstd_tarball_path = tmp_path / "site-packages.tar.zst"
    tarball_path.write_bytes(b"gzip")
    zstd_tarball_path.write_bytes(b"zstd")
    upload_file = mocker.patch(
        "tracecat.registry.sync.tarball.blob.upload_file",
        mocker.AsyncMock(),
    )

    uri = await upload_tarball_venv(
        tarball_path=tarball_path,
        zstd_tarball_path=zstd_tarball_path,
        key="org/tarball-venvs/tracecat_registry/v1/site-packages.tar.gz",
        bucket="tracecat-registry",
    )

    assert uri == (
        "s3://tracecat-registry/org/tarball-venvs/tracecat_registry/v1/"
        "site-packages.tar.gz"
    )
    assert upload_file.await_count == 2
    upload_file.assert_has_awaits(
        [
            mocker.call(
                content=b"gzip",
                key="org/tarball-venvs/tracecat_registry/v1/site-packages.tar.gz",
                bucket="tracecat-registry",
                content_type="application/gzip",
            ),
            mocker.call(
                content=b"zstd",
                key="org/tarball-venvs/tracecat_registry/v1/site-packages.tar.zst",
                bucket="tracecat-registry",
                content_type="application/zstd",
            ),
        ]
    )
