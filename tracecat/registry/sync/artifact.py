"""SquashFS venv building utilities for registry packages.

Builds a SquashFS image of a registry package's site-packages and uploads it
to S3/MinIO so executors can mount (or extract via unsquashfs) it at runtime.
The module also retains a small set of legacy helpers used by the backfill
flow for registry versions originally uploaded as gzip tarballs.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import subprocess
import sysconfig
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles
import tracecat_registry

from tracecat import config
from tracecat.logger import logger
from tracecat.storage import blob

if TYPE_CHECKING:
    from tracecat.ssh import SshEnv


class RegistryArtifactBuildError(Exception):
    """Raised when registry venv (SquashFS) building fails."""


@dataclass
class RegistryArtifactBuildResult:
    """Result of building a SquashFS venv image."""

    squashfs_path: Path
    squashfs_name: str
    content_hash: str
    artifact_size_bytes: int


def _compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def _copy_squashfs_entry(
    path: Path,
    dest: Path,
    *,
    preserve_symlinks: bool,
) -> None:
    """Stage one artifact entry for SquashFS."""

    def _normalized_file_mode(mode: int) -> int:
        mode = stat.S_IMODE(mode)
        normalized = mode | 0o444
        if mode & 0o111:
            normalized |= 0o111
        return normalized

    def _normalized_dir_mode(mode: int) -> int:
        return stat.S_IMODE(mode) | 0o555

    def _remove_existing_entry() -> None:
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.is_dir():
            shutil.rmtree(dest)

    if path.is_symlink():
        if not preserve_symlinks:
            logger.debug(
                "Skipping link entry while staging SquashFS",
                path=str(path),
                link_target=os.readlink(path),
            )
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        _remove_existing_entry()
        dest.symlink_to(os.readlink(path))
        return

    if path.is_dir():
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        dest.mkdir(parents=True, exist_ok=True)
        for child in path.iterdir():
            _copy_squashfs_entry(
                child,
                dest / child.name,
                preserve_symlinks=preserve_symlinks,
            )
        dest.chmod(_normalized_dir_mode(path.stat().st_mode))
        return

    if path.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _remove_existing_entry()
        source_mode = stat.S_IMODE(path.stat().st_mode)
        normalized_mode = _normalized_file_mode(source_mode)
        if normalized_mode == source_mode:
            try:
                os.link(path, dest)
            except OSError:
                shutil.copy2(path, dest)
        else:
            shutil.copy2(path, dest)
            dest.chmod(normalized_mode)
        return

    logger.debug("Skipping non-file entry while staging SquashFS", path=str(path))


def _create_squashfs_image(
    squashfs_path: Path,
    entries: list[tuple[Path, str]],
    *,
    preserve_symlinks: bool = True,
) -> bool:
    """Create a SquashFS image from path entries when mksquashfs is available."""
    if not config.TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED:
        return False

    mksquashfs = shutil.which("mksquashfs")
    if mksquashfs is None:
        logger.warning("Skipping SquashFS image build; mksquashfs is not installed")
        return False

    with tempfile.TemporaryDirectory(prefix="tracecat_squashfs_root_") as staging:
        staging_dir = Path(staging)
        # TemporaryDirectory creates a 0700 root, which would make the mounted
        # SquashFS unreadable to non-root executor processes after -all-root.
        staging_dir.chmod(0o755)
        for path, arcname in entries:
            _copy_squashfs_entry(
                path,
                staging_dir / arcname,
                preserve_symlinks=preserve_symlinks,
            )

        cmd = [
            mksquashfs,
            str(staging_dir),
            str(squashfs_path),
            "-noappend",
            "-comp",
            "gzip",
            "-no-xattrs",
            "-all-root",
            "-processors",
            str(config.TRACECAT__REGISTRY_SYNC_SQUASHFS_PROCESSORS),
            "-mem",
            config.TRACECAT__REGISTRY_SYNC_SQUASHFS_MEM,
        ]
        result = subprocess_run(cmd)
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            raise RegistryArtifactBuildError(f"Failed to build SquashFS image: {error}")

    return True


def _build_required_squashfs_image(
    squashfs_path: Path,
    entries: list[tuple[Path, str]],
    *,
    preserve_symlinks: bool = True,
) -> None:
    """Create the primary SquashFS image; raise if disabled or unavailable."""
    if not _create_squashfs_image(
        squashfs_path,
        entries,
        preserve_symlinks=preserve_symlinks,
    ):
        squashfs_path.unlink(missing_ok=True)
        raise RegistryArtifactBuildError(
            "Cannot build registry venv: mksquashfs is unavailable or SquashFS "
            "build is disabled (TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED)."
        )


def subprocess_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command.

    Kept as a small wrapper so tests can monkeypatch SquashFS creation without
    shelling out.
    """
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def get_builtin_registry_source_path() -> Path:
    """Get the path to the builtin tracecat_registry package source.

    Checks in order:
    1. Config path (TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH)
    2. Relative to installed package (editable/source install)

    Returns:
        Path to the package directory containing pyproject.toml.

    Raises:
        RegistryArtifactBuildError: If package path cannot be determined.
    """
    # Check configured path first (Docker default: /app/packages/tracecat-registry)
    config_path = Path(config.TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH)
    if (config_path / "pyproject.toml").exists():
        return config_path

    # Fall back to development: look relative to installed package
    package_path = Path(tracecat_registry.__file__).parent.parent
    pyproject = package_path / "pyproject.toml"
    if not pyproject.exists():
        package_path = package_path.parent
        pyproject = package_path / "pyproject.toml"

    if not pyproject.exists():
        raise RegistryArtifactBuildError(
            "Cannot find pyproject.toml for tracecat_registry. "
            "Set TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH or use source installation."
        )

    return package_path


def get_installed_site_packages_paths() -> list[Path]:
    """Get site-packages directories for the current interpreter.

    Returns both `purelib` and `platlib` when they differ.
    """
    site_packages_paths: list[Path] = []
    seen_paths: set[Path] = set()

    for path_name in ("purelib", "platlib"):
        site_packages_str = sysconfig.get_path(path_name)
        if site_packages_str is None:
            raise RegistryArtifactBuildError(
                f"Could not resolve {path_name} path from current interpreter."
            )

        site_packages = Path(site_packages_str)
        if not site_packages.exists():
            raise RegistryArtifactBuildError(
                f"Resolved {path_name} path does not exist: {site_packages}"
            )

        resolved_site_packages = site_packages.resolve()
        if resolved_site_packages in seen_paths:
            continue

        seen_paths.add(resolved_site_packages)
        site_packages_paths.append(site_packages)

    return site_packages_paths


async def build_artifact_from_installed_environment(
    *,
    package_name: str,
    package_dir: Path,
    output_dir: Path | None = None,
) -> RegistryArtifactBuildResult:
    """Build a SquashFS venv image from the current interpreter's installed environment.

    This avoids creating a fresh venv and re-installing dependencies from indexes.
    It's primarily used for builtin registry sync where dependencies are already
    available in the running container image.
    """

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="tracecat_registry_venv_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    site_packages_paths = get_installed_site_packages_paths()
    package_dir = package_dir.resolve()
    package_in_site_packages = any(
        package_dir.parent == site_packages.resolve()
        for site_packages in site_packages_paths
    )
    existing_package_entries = [
        site_packages / package_name
        for site_packages in site_packages_paths
        if (site_packages / package_name).exists()
    ]
    package_site_entry = (
        existing_package_entries[0] if existing_package_entries else None
    )
    package_site_entry_is_symlink = (
        package_site_entry.is_symlink() if package_site_entry is not None else False
    )
    should_overlay_editable_package = not package_in_site_packages and (
        package_site_entry is None or package_site_entry_is_symlink
    )

    squashfs_name = "site-packages.squashfs"
    squashfs_path = output_dir / squashfs_name

    logger.info(
        "Building SquashFS venv from installed environment",
        site_packages_paths=[str(path) for path in site_packages_paths],
        package_name=package_name,
        package_dir=str(package_dir),
        package_in_site_packages=package_in_site_packages,
        package_site_entry=str(package_site_entry) if package_site_entry else None,
        package_site_entry_is_symlink=package_site_entry_is_symlink,
    )

    def _build_image() -> None:
        entries: list[tuple[Path, str]] = []
        for site_packages in site_packages_paths:
            entries.extend((item, item.name) for item in site_packages.iterdir())

        if not package_in_site_packages and should_overlay_editable_package:
            entries.append((package_dir, package_name))

        _build_required_squashfs_image(
            squashfs_path,
            entries,
            preserve_symlinks=False,
        )

        if not package_in_site_packages and not should_overlay_editable_package:
            logger.warning(
                "Skipping editable package overlay because package already exists in site-packages",
                package_name=package_name,
            )

    await asyncio.to_thread(_build_image)

    content_hash = await asyncio.to_thread(_compute_file_hash, squashfs_path)
    artifact_size = squashfs_path.stat().st_size

    logger.info(
        "Installed environment SquashFS venv built successfully",
        squashfs_name=squashfs_name,
        content_hash=content_hash[:16],
        artifact_size_bytes=artifact_size,
    )

    return RegistryArtifactBuildResult(
        squashfs_path=squashfs_path,
        squashfs_name=squashfs_name,
        content_hash=content_hash,
        artifact_size_bytes=artifact_size,
    )


async def build_artifact_from_path(
    package_path: Path,
    output_dir: Path | None = None,
    python_version: str = "3.12",
) -> RegistryArtifactBuildResult:
    """Build a complete venv with all dependencies as a SquashFS image.

    This creates a portable venv image that can be mounted (or extracted) and
    used directly without running pip install.

    Args:
        package_path: Path to the package directory (must contain pyproject.toml)
        output_dir: Directory to output the image (defaults to temp dir)
        python_version: Python version for the venv (default: 3.12)

    Returns:
        RegistryArtifactBuildResult with the image path and metadata

    Raises:
        RegistryArtifactBuildError: If the build fails
    """

    if not package_path.exists():
        raise RegistryArtifactBuildError(f"Package path does not exist: {package_path}")

    pyproject = package_path / "pyproject.toml"
    if not pyproject.exists():
        raise RegistryArtifactBuildError(f"No pyproject.toml found in {package_path}")

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="tracecat_registry_venv_"))

    output_dir.mkdir(parents=True, exist_ok=True)

    venv_dir = output_dir / "venv"
    venv_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Building SquashFS venv from package",
        package_path=str(package_path),
        output_dir=str(output_dir),
    )

    # Step 1: Create a minimal venv using uv
    venv_cmd = ["uv", "venv", str(venv_dir), "--python", python_version]
    process = await asyncio.create_subprocess_exec(
        *venv_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(package_path),
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode().strip()
        logger.error("Failed to create venv", error=error_msg)
        raise RegistryArtifactBuildError(f"Failed to create venv: {error_msg}")

    # Step 2: Install the package and all dependencies into the venv
    install_cmd = [
        "uv",
        "pip",
        "install",
        "--python",
        str(venv_dir / "bin" / "python"),
        str(package_path),
    ]
    process = await asyncio.create_subprocess_exec(
        *install_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(package_path),
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode().strip()
        logger.error("Failed to install package into venv", error=error_msg)
        raise RegistryArtifactBuildError(f"Failed to install package: {error_msg}")

    # Step 3: Locate the site-packages directory (PYTHONPATH entry)
    site_packages = venv_dir / "lib" / f"python{python_version}" / "site-packages"
    if not site_packages.exists():
        site_packages_candidates = list(
            (venv_dir / "lib").glob("python*/site-packages")
        )
        if site_packages_candidates:
            site_packages = site_packages_candidates[0]
        else:
            raise RegistryArtifactBuildError(
                f"Could not find site-packages in {venv_dir}"
            )

    # Step 4: Pre-compile Python bytecode for faster imports in sandbox
    logger.info("Pre-compiling Python bytecode", site_packages=str(site_packages))
    compile_cmd = [
        str(venv_dir / "bin" / "python"),
        "-m",
        "compileall",
        "-q",
        "-f",
        "-j",
        "0",
        str(site_packages),
    ]
    process = await asyncio.create_subprocess_exec(
        *compile_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    # Ignore errors - bytecode compilation is optional optimization

    # Step 5: Pack site-packages into a SquashFS image
    squashfs_name = "site-packages.squashfs"
    squashfs_path = output_dir / squashfs_name

    logger.info(
        "Packing site-packages into SquashFS image",
        site_packages=str(site_packages),
        squashfs_path=str(squashfs_path),
    )

    def _build_image() -> None:
        entries = [(item, item.name) for item in site_packages.iterdir()]
        _build_required_squashfs_image(squashfs_path, entries)

    await asyncio.to_thread(_build_image)

    # Step 6: Compute content hash
    content_hash = await asyncio.to_thread(_compute_file_hash, squashfs_path)
    artifact_size = squashfs_path.stat().st_size

    logger.info(
        "SquashFS venv built successfully",
        squashfs_name=squashfs_name,
        content_hash=content_hash[:16],
        artifact_size_bytes=artifact_size,
    )

    return RegistryArtifactBuildResult(
        squashfs_path=squashfs_path,
        squashfs_name=squashfs_name,
        content_hash=content_hash,
        artifact_size_bytes=artifact_size,
    )


async def build_artifact_from_git(
    git_url: str,
    commit_sha: str,
    env: SshEnv | None = None,
    output_dir: Path | None = None,
    python_version: str = "3.12",
) -> RegistryArtifactBuildResult:
    """Build a SquashFS venv image from a git repository.

    Args:
        git_url: Git SSH URL (git+ssh://...)
        commit_sha: Commit SHA to checkout
        env: SSH environment for git operations
        output_dir: Directory to output the image
        python_version: Python version for the venv (default: 3.12)

    Returns:
        RegistryArtifactBuildResult with the image path and metadata

    Raises:
        RegistryArtifactBuildError: If the build fails
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="tracecat_registry_venv_"))

    output_dir.mkdir(parents=True, exist_ok=True)

    async with aiofiles.tempfile.TemporaryDirectory(
        prefix="tracecat_git_"
    ) as clone_dir:
        clone_path = Path(clone_dir)

        clone_url = git_url.replace("git+ssh://", "ssh://")

        logger.info(
            "Cloning repository for SquashFS venv build",
            url=clone_url,
            commit_sha=commit_sha,
        )

        # Set up environment for git operations
        git_env = os.environ.copy()
        if env:
            git_env.update(env.to_dict())

        # Clone the repository
        clone_cmd = ["git", "clone", "--depth", "1", clone_url, str(clone_path)]
        process = await asyncio.create_subprocess_exec(
            *clone_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=git_env,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            raise RegistryArtifactBuildError(f"Failed to clone repository: {error_msg}")

        # Fetch the specific commit
        fetch_cmd = ["git", "fetch", "origin", commit_sha]
        process = await asyncio.create_subprocess_exec(
            *fetch_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(clone_path),
            env=git_env,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            raise RegistryArtifactBuildError(f"Failed to fetch commit: {error_msg}")

        # Checkout the specific commit
        checkout_cmd = ["git", "checkout", commit_sha]
        process = await asyncio.create_subprocess_exec(
            *checkout_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(clone_path),
            env=git_env,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            raise RegistryArtifactBuildError(f"Failed to checkout commit: {error_msg}")

        return await build_artifact_from_path(clone_path, output_dir, python_version)


async def build_builtin_registry_artifact(
    output_dir: Path | None = None,
) -> RegistryArtifactBuildResult:
    """Build a SquashFS venv image from the builtin tracecat_registry package.

    Args:
        output_dir: Directory to output the image

    Returns:
        RegistryArtifactBuildResult with the image path and metadata

    Raises:
        RegistryArtifactBuildError: If the build fails
    """
    if config.TRACECAT__REGISTRY_SYNC_BUILTIN_USE_INSTALLED_SITE_PACKAGES:
        return await build_artifact_from_installed_environment(
            package_name="tracecat_registry",
            package_dir=Path(tracecat_registry.__file__).resolve().parent,
            output_dir=output_dir,
        )

    package_path = get_builtin_registry_source_path()
    return await build_artifact_from_path(package_path, output_dir)


async def build_squashfs_sidecar_from_tarball(
    *,
    tarball_path: Path,
    squashfs_path: Path,
    work_dir: Path,
) -> bool:
    """Build a SquashFS sidecar from an existing site-packages tarball.

    Used by the backfill flow to retrofit SquashFS artifacts onto registry
    versions that were originally uploaded as gzip tarballs only.
    """

    def _build_sidecar() -> bool:
        if not tarball_path.exists():
            raise FileNotFoundError(f"Tarball file not found: {tarball_path}")

        extracted_dir = work_dir / "site-packages"
        extracted_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(path=extracted_dir, filter="data")

        entries = [(item, item.name) for item in extracted_dir.iterdir()]
        created = _create_squashfs_image(
            squashfs_path,
            entries,
            preserve_symlinks=True,
        )
        if not created:
            squashfs_path.unlink(missing_ok=True)
        return squashfs_path.exists()

    return await asyncio.to_thread(_build_sidecar)


async def upload_squashfs_venv(
    squashfs_path: Path,
    key: str,
    bucket: str,
) -> str:
    """Upload a SquashFS registry environment artifact to S3/MinIO."""
    if not squashfs_path.exists():
        raise FileNotFoundError(f"SquashFS file not found: {squashfs_path}")

    await blob.upload_file_from_path(
        path=squashfs_path,
        key=key,
        bucket=bucket,
        content_type="application/vnd.squashfs",
    )

    s3_uri = f"s3://{bucket}/{key}"
    logger.info(
        "SquashFS registry artifact uploaded successfully",
        key=key,
        bucket=bucket,
        s3_uri=s3_uri,
        size=squashfs_path.stat().st_size,
    )
    return s3_uri


async def download_tarball_venv(
    key: str,
    bucket: str,
    output_path: Path,
) -> Path:
    """Download a tarball venv file from S3/MinIO.

    Args:
        key: The S3 object key
        bucket: Bucket name
        output_path: Local path to save the tarball file

    Returns:
        The local path to the downloaded tarball
    """
    size_bytes = await blob.download_file_to_path(
        key=key,
        bucket=bucket,
        output_path=output_path,
    )

    logger.info(
        "Tarball venv downloaded successfully",
        key=key,
        bucket=bucket,
        output_path=str(output_path),
        size=size_bytes,
    )
    return output_path
