"""Tests for registry sync artifact building utilities."""

from __future__ import annotations

import asyncio
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import pytest
from tracecat_registry import RegistrySecret

from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionOptions,
    RegistryActionUDFImpl,
)
from tracecat.registry.artifact_keys import parse_s3_uri
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.sync import artifact
from tracecat.registry.sync.artifact import upload_squashfs_venv
from tracecat.registry.sync.schemas import RegistryActionsAdapter


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


@pytest.fixture
def registry_actions() -> list[RegistryActionCreate]:
    """Return a nonempty synthetic discovery result for storage round trips."""
    return [
        RegistryActionCreate(
            repository_id=uuid4(),
            name="reshape",
            description="Reshape test payload",
            namespace="core.transform",
            type="udf",
            origin=DEFAULT_REGISTRY_ORIGIN,
            interface={"expects": {}, "returns": {}},
            implementation=RegistryActionUDFImpl(
                type="udf",
                url=DEFAULT_REGISTRY_ORIGIN,
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            secrets=[RegistrySecret(name="example", keys=["EXAMPLE_TOKEN"])],
            default_title="Reshape",
            display_group=None,
            doc_url=None,
            author=None,
            deprecated=None,
            options=RegistryActionOptions(),
        )
    ]


@pytest.mark.anyio
async def test_registry_actions_download_unchanged_from_beside_their_artifact(
    mocker,
    registry_actions: list[RegistryActionCreate],
) -> None:
    """Actions uploaded beside an artifact download back equal to the originals."""
    store: dict[tuple[str, str], bytes] = {}

    async def upload_file(
        *,
        content: bytes,
        key: str,
        bucket: str,
        content_type: str | None = None,
    ) -> None:
        assert content_type == "application/json"
        store[(bucket, key)] = content

    async def download_file(*, key: str, bucket: str) -> bytes:
        return store[(bucket, key)]

    mocker.patch(
        "tracecat.registry.sync.artifact.blob.upload_file",
        side_effect=upload_file,
    )
    mocker.patch(
        "tracecat.registry.sync.artifact.blob.download_file",
        side_effect=download_file,
    )
    actions = registry_actions

    actions_uri = await artifact.upload_registry_actions(
        actions,
        artifact_uri=(
            "s3://tracecat-registry/platform/tarball-venvs/tracecat_registry/v1/"
            "site-packages.squashfs"
        ),
    )

    bucket, key = parse_s3_uri(actions_uri)
    actions_key = PurePosixPath(key)
    assert bucket == "tracecat-registry"
    assert actions_key.parent.parent == PurePosixPath(
        "platform/tarball-venvs/tracecat_registry/v1/actions"
    )
    assert UUID(actions_key.parent.name).version == 4
    assert actions_key.name == "actions.json"
    assert await artifact.download_registry_actions(actions_uri) == actions


@pytest.mark.anyio
@pytest.mark.parametrize("version", ["abc123", "2026.01.01.000000"])
async def test_concurrent_actions_uploads_preserve_each_syncs_discovery(
    mocker,
    registry_actions: list[RegistryActionCreate],
    version: str,
) -> None:
    """Overlapping syncs sharing an artifact must read their own discovered actions."""
    store: dict[tuple[str, str], bytes] = {}
    both_uploaded = asyncio.Event()
    upload_count = 0

    async def upload_file(
        *,
        content: bytes,
        key: str,
        bucket: str,
        content_type: str | None = None,
    ) -> None:
        nonlocal upload_count
        assert content_type == "application/json"
        store[(bucket, key)] = content
        upload_count += 1
        if upload_count == 2:
            both_uploaded.set()
        await both_uploaded.wait()

    async def download_file(*, key: str, bucket: str) -> bytes:
        return store[(bucket, key)]

    mocker.patch.object(artifact.blob, "upload_file", side_effect=upload_file)
    mocker.patch.object(artifact.blob, "download_file", side_effect=download_file)
    first_actions = registry_actions
    second_actions = [
        action.model_copy(
            update={"name": "alternate", "default_title": "Second discovery"}
        )
        for action in registry_actions
    ]
    artifact_uri = (
        f"s3://tracecat-registry/org/tarball-venvs/example/{version}/"
        "site-packages.squashfs"
    )
    async with asyncio.timeout(5):
        first_uri, second_uri = await asyncio.gather(
            artifact.upload_registry_actions(first_actions, artifact_uri=artifact_uri),
            artifact.upload_registry_actions(second_actions, artifact_uri=artifact_uri),
        )

    # Both writes have completed before either API-side read; a shared key
    # would now return the later publisher's actions for the earlier result.
    assert await artifact.download_registry_actions(first_uri) == first_actions
    assert await artifact.download_registry_actions(second_uri) == second_actions
    assert first_uri != second_uri
    assert len(store) == 2


@pytest.mark.anyio
async def test_registry_actions_download_accepts_legacy_shared_uri(
    mocker,
    registry_actions: list[RegistryActionCreate],
) -> None:
    """Existing shared-sidecar result URIs remain readable after key isolation."""
    download_file = mocker.patch.object(
        artifact.blob,
        "download_file",
        return_value=RegistryActionsAdapter.dump_json(registry_actions),
    )
    legacy_uri = "s3://tracecat-registry/platform/tarball-venvs/tracecat_registry/v1/actions.json"
    assert await artifact.download_registry_actions(legacy_uri) == registry_actions
    download_file.assert_awaited_once_with(
        key="platform/tarball-venvs/tracecat_registry/v1/actions.json",
        bucket="tracecat-registry",
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
