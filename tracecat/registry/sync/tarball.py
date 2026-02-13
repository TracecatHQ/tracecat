"""Tarball venv building utilities for registry packages.

This module provides functionality to build compressed tarball venvs from
registry packages for upload to S3/MinIO. Tarballs are used by executors
to install and execute registry actions.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
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


class TarballBuildError(Exception):
    """Raised when tarball building fails."""


@dataclass
class TarballVenvBuildResult:
    """Result of building a compressed tarball venv."""

    tarball_path: Path
    tarball_name: str
    content_hash: str
    compressed_size_bytes: int


def _compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def _slugify_origin(origin: str) -> str:
    """Convert a repository origin to a safe slug for S3 keys."""
    # Remove protocol prefix
    slug = (
        origin.replace("git+ssh://", "").replace("https://", "").replace("http://", "")
    )
    # Replace non-alphanumeric characters with underscores
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug)
    # Remove consecutive underscores
    slug = re.sub(r"_+", "_", slug)
    # Remove leading/trailing underscores
    slug = slug.strip("_")
    return slug[:100]  # Limit length


def get_builtin_registry_source_path() -> Path:
    """Get the path to the builtin tracecat_registry package source.

    Checks in order:
    1. Config path (TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH)
    2. Relative to installed package (editable/source install)

    Returns:
        Path to the package directory containing pyproject.toml.

    Raises:
        TarballBuildError: If package path cannot be determined.
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
        raise TarballBuildError(
            "Cannot find pyproject.toml for tracecat_registry. "
            "Set TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH or use source installation."
        )

    return package_path


def get_installed_site_packages_path() -> Path:
    """Get the site-packages directory for the current interpreter."""
    site_packages_str = sysconfig.get_path("purelib")
    if site_packages_str is None:
        raise TarballBuildError(
            "Could not resolve site-packages path from current interpreter."
        )

    site_packages = Path(site_packages_str)
    if not site_packages.exists():
        raise TarballBuildError(
            f"Resolved site-packages path does not exist: {site_packages}"
        )
    return site_packages


async def build_tarball_venv_from_installed_environment(
    *,
    package_name: str,
    package_dir: Path,
    output_dir: Path | None = None,
) -> TarballVenvBuildResult:
    """Build a tarball from the current interpreter's installed environment.

    This avoids creating a fresh venv and re-installing dependencies from indexes.
    It's primarily used for builtin registry sync where dependencies are already
    available in the running container image.
    """

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="tracecat_tarball_venv_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    site_packages = get_installed_site_packages_path()
    package_dir = package_dir.resolve()
    package_in_site_packages = package_dir.parent == site_packages.resolve()

    tarball_name = "site-packages.tar.gz"
    tarball_path = output_dir / tarball_name

    logger.info(
        "Building tarball from installed environment",
        site_packages=str(site_packages),
        package_name=package_name,
        package_dir=str(package_dir),
        package_in_site_packages=package_in_site_packages,
    )

    def _create_tarball() -> None:
        with tarfile.open(tarball_path, "w:gz", compresslevel=6) as tar:
            for item in site_packages.iterdir():
                tar.add(item, arcname=item.name)

            # Editable installs put package source outside site-packages.
            if not package_in_site_packages:
                if (site_packages / package_name).exists():
                    logger.warning(
                        "Skipping editable package overlay because package already exists in site-packages",
                        package_name=package_name,
                        site_packages=str(site_packages),
                    )
                else:
                    tar.add(package_dir, arcname=package_name)

    await asyncio.to_thread(_create_tarball)

    content_hash = await asyncio.to_thread(_compute_file_hash, tarball_path)
    compressed_size = tarball_path.stat().st_size

    logger.info(
        "Installed environment tarball built successfully",
        tarball_name=tarball_name,
        content_hash=content_hash[:16],
        compressed_size_bytes=compressed_size,
    )

    return TarballVenvBuildResult(
        tarball_path=tarball_path,
        tarball_name=tarball_name,
        content_hash=content_hash,
        compressed_size_bytes=compressed_size,
    )


async def build_tarball_venv_from_path(
    package_path: Path,
    output_dir: Path | None = None,
    python_version: str = "3.12",
) -> TarballVenvBuildResult:
    """Build a complete venv with all dependencies and compress as tarball.

    This creates a portable venv tarball that can be extracted and used
    directly without running pip install. Faster for deployment since
    extraction is quicker than package installation.

    Args:
        package_path: Path to the package directory (must contain pyproject.toml)
        output_dir: Directory to output the tarball (defaults to temp dir)
        python_version: Python version for the venv (default: 3.12)

    Returns:
        TarballVenvBuildResult with the tarball path and metadata

    Raises:
        TarballBuildError: If the build fails
    """

    if not package_path.exists():
        raise TarballBuildError(f"Package path does not exist: {package_path}")

    pyproject = package_path / "pyproject.toml"
    if not pyproject.exists():
        raise TarballBuildError(f"No pyproject.toml found in {package_path}")

    # Use temp dir if no output dir specified
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="tracecat_tarball_venv_"))

    output_dir.mkdir(parents=True, exist_ok=True)

    # Create venv directory
    venv_dir = output_dir / "venv"
    venv_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Building tarball venv from package",
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
        raise TarballBuildError(f"Failed to create venv: {error_msg}")

    # Step 2: Install the package and all dependencies into the venv
    # Use --frozen to respect the lock file if present
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
        raise TarballBuildError(f"Failed to install package: {error_msg}")

    # Step 3: Extract just the site-packages directory (what we need for PYTHONPATH)
    site_packages = venv_dir / "lib" / f"python{python_version}" / "site-packages"
    if not site_packages.exists():
        # Try without minor version
        site_packages_candidates = list(
            (venv_dir / "lib").glob("python*/site-packages")
        )
        if site_packages_candidates:
            site_packages = site_packages_candidates[0]
        else:
            raise TarballBuildError(f"Could not find site-packages in {venv_dir}")

    # Step 4: Pre-compile Python bytecode for faster imports in sandbox
    # This avoids runtime compilation overhead when the tarball is extracted
    logger.info("Pre-compiling Python bytecode", site_packages=str(site_packages))
    compile_cmd = [
        str(venv_dir / "bin" / "python"),
        "-m",
        "compileall",
        "-q",  # Quiet mode
        "-f",  # Force recompile
        "-j",
        "0",  # Use all CPU cores
        str(site_packages),
    ]
    process = await asyncio.create_subprocess_exec(
        *compile_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    # Ignore errors - bytecode compilation is optional optimization

    # Step 5: Create compressed tarball of site-packages
    # Use gzip compression (zstd would be faster but less portable)
    tarball_name = "site-packages.tar.gz"
    tarball_path = output_dir / tarball_name

    logger.info(
        "Compressing site-packages to tarball",
        site_packages=str(site_packages),
        tarball_path=str(tarball_path),
    )

    # Run tar compression in a thread to not block async loop
    def _create_tarball() -> None:
        with tarfile.open(tarball_path, "w:gz", compresslevel=6) as tar:
            # Add site-packages contents with relative paths
            for item in site_packages.iterdir():
                tar.add(item, arcname=item.name)

    await asyncio.to_thread(_create_tarball)

    # Step 6: Compute content hash
    content_hash = await asyncio.to_thread(_compute_file_hash, tarball_path)
    compressed_size = tarball_path.stat().st_size

    logger.info(
        "Tarball venv built successfully",
        tarball_name=tarball_name,
        content_hash=content_hash[:16],
        compressed_size_bytes=compressed_size,
    )

    return TarballVenvBuildResult(
        tarball_path=tarball_path,
        tarball_name=tarball_name,
        content_hash=content_hash,
        compressed_size_bytes=compressed_size,
    )


async def build_tarball_venv_from_git(
    git_url: str,
    commit_sha: str,
    env: SshEnv | None = None,
    output_dir: Path | None = None,
    python_version: str = "3.12",
) -> TarballVenvBuildResult:
    """Build a tarball venv from a git repository.

    Args:
        git_url: Git SSH URL (git+ssh://...)
        commit_sha: Commit SHA to checkout
        env: SSH environment for git operations
        output_dir: Directory to output the tarball
        python_version: Python version for the venv (default: 3.12)

    Returns:
        TarballVenvBuildResult with the tarball path and metadata

    Raises:
        TarballBuildError: If the build fails
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="tracecat_tarball_venv_"))

    output_dir.mkdir(parents=True, exist_ok=True)

    # Clone the repository to a temp directory
    async with aiofiles.tempfile.TemporaryDirectory(
        prefix="tracecat_git_"
    ) as clone_dir:
        clone_path = Path(clone_dir)

        # Strip git+ssh:// prefix for git clone
        clone_url = git_url.replace("git+ssh://", "ssh://")

        logger.info(
            "Cloning repository for tarball venv build",
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
            raise TarballBuildError(f"Failed to clone repository: {error_msg}")

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
            raise TarballBuildError(f"Failed to fetch commit: {error_msg}")

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
            raise TarballBuildError(f"Failed to checkout commit: {error_msg}")

        # Build the tarball venv from the cloned repository
        return await build_tarball_venv_from_path(
            clone_path, output_dir, python_version
        )


async def build_builtin_registry_tarball_venv(
    output_dir: Path | None = None,
) -> TarballVenvBuildResult:
    """Build a tarball venv from the builtin tracecat_registry package.

    Args:
        output_dir: Directory to output the tarball

    Returns:
        TarballVenvBuildResult with the tarball path and metadata

    Raises:
        TarballBuildError: If the build fails
    """
    if config.TRACECAT__REGISTRY_SYNC_BUILTIN_USE_INSTALLED_SITE_PACKAGES:
        return await build_tarball_venv_from_installed_environment(
            package_name="tracecat_registry",
            package_dir=Path(tracecat_registry.__file__).resolve().parent,
            output_dir=output_dir,
        )

    package_path = get_builtin_registry_source_path()
    return await build_tarball_venv_from_path(package_path, output_dir)


def get_tarball_venv_s3_key(
    organization_id: str,
    repository_origin: str,
    version: str,
) -> str:
    """Generate the S3 key for a tarball venv file.

    Format: {org_id}/tarball-venvs/{origin_slug}/{version}/site-packages.tar.gz

    Args:
        organization_id: Organization UUID
        repository_origin: Repository origin (e.g., "tracecat_registry", "git+ssh://...")
        version: Version string

    Returns:
        S3 key string
    """
    origin_slug = _slugify_origin(repository_origin)
    return (
        f"{organization_id}/tarball-venvs/{origin_slug}/{version}/site-packages.tar.gz"
    )


async def upload_tarball_venv(
    tarball_path: Path,
    key: str,
    bucket: str,
) -> str:
    """Upload a tarball venv file to S3/MinIO.

    Args:
        tarball_path: Local path to the tarball file
        key: The S3 object key
        bucket: Bucket name

    Returns:
        The S3 URI of the uploaded tarball (s3://{bucket}/{key})

    Raises:
        FileNotFoundError: If the tarball file doesn't exist
    """
    if not tarball_path.exists():
        raise FileNotFoundError(f"Tarball file not found: {tarball_path}")

    # Use asyncio.to_thread to avoid blocking the event loop for large files
    content = await asyncio.to_thread(tarball_path.read_bytes)

    await blob.upload_file(
        content=content,
        key=key,
        bucket=bucket,
        content_type="application/gzip",
    )

    s3_uri = f"s3://{bucket}/{key}"
    logger.info(
        "Tarball venv uploaded successfully",
        key=key,
        bucket=bucket,
        s3_uri=s3_uri,
        size=len(content),
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
