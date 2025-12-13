"""Wheel building utilities for registry packages.

This module provides functionality to build Python wheels from registry
packages for upload to S3/MinIO. Wheels are used by Lambda functions
to install and execute registry actions.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles
import tracecat_registry

from tracecat.logger import logger
from tracecat.storage import blob

if TYPE_CHECKING:
    from tracecat.ssh import SshEnv

# Wheel filename pattern per PEP 427
# Format: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl
# See: https://peps.python.org/pep-0427/#file-name-convention
_WHEEL_FILENAME_PATTERN = re.compile(
    r"^(?P<distribution>[A-Za-z0-9](?:[A-Za-z0-9._]*[A-Za-z0-9])?)"
    r"-(?P<version>[A-Za-z0-9_.!+]+)"
    r"(?:-\d+)?"  # Optional build tag
    r"-(?P<python>[a-z0-9]+)"
    r"-(?P<abi>[a-z0-9]+)"
    r"-(?P<platform>[a-z0-9_]+)"
    r"\.whl$",
    re.IGNORECASE,
)


class WheelBuildError(Exception):
    """Raised when wheel building fails."""


@dataclass
class WheelBuildResult:
    """Result of building a wheel."""

    wheel_path: Path
    wheel_name: str
    content_hash: str
    package_name: str
    version: str


@dataclass
class WheelhouseBuildResult:
    """Result of building a dependency wheelhouse."""

    wheelhouse_dir: Path
    wheel_files: list[Path]
    requirements_hash: str

    @property
    def num_wheels(self) -> int:
        return len(self.wheel_files)


async def build_wheel_from_path(
    package_path: Path,
    output_dir: Path | None = None,
) -> WheelBuildResult:
    """Build a wheel from a local package directory.

    Args:
        package_path: Path to the package directory (must contain pyproject.toml)
        output_dir: Directory to output the wheel (defaults to temp dir)

    Returns:
        WheelBuildResult with the wheel path and metadata

    Raises:
        WheelBuildError: If the build fails
    """
    if not package_path.exists():
        raise WheelBuildError(f"Package path does not exist: {package_path}")

    pyproject = package_path / "pyproject.toml"
    if not pyproject.exists():
        raise WheelBuildError(f"No pyproject.toml found in {package_path}")

    # Use temp dir if no output dir specified
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="tracecat_wheel_"))

    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Building wheel from local path",
        package_path=str(package_path),
        output_dir=str(output_dir),
    )

    # Build the wheel using uv build
    cmd = ["uv", "build", "--wheel", "--out-dir", str(output_dir)]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(package_path),
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode().strip()
        logger.error("Wheel build failed", error=error_msg)
        raise WheelBuildError(f"Failed to build wheel: {error_msg}")

    # Find the built wheel
    wheel_files = list(output_dir.glob("*.whl"))
    if not wheel_files:
        raise WheelBuildError(f"No wheel file found in {output_dir}")

    wheel_path = wheel_files[0]
    wheel_name = wheel_path.name

    # Parse wheel name to extract package info
    if match := _WHEEL_FILENAME_PATTERN.match(wheel_name):
        package_name = match.group("distribution")
        version = match.group("version")
    else:
        package_name = wheel_name.removesuffix(".whl")
        version = "unknown"

    # Compute content hash for deduplication
    content_hash = _compute_file_hash(wheel_path)

    logger.info(
        "Wheel built successfully",
        wheel_name=wheel_name,
        package=package_name,
        version=version,
        content_hash=content_hash[:16],
    )

    return WheelBuildResult(
        wheel_path=wheel_path,
        wheel_name=wheel_name,
        content_hash=content_hash,
        package_name=package_name,
        version=version,
    )


async def build_wheel_from_git(
    git_url: str,
    commit_sha: str,
    env: SshEnv | None = None,
    output_dir: Path | None = None,
) -> WheelBuildResult:
    """Build a wheel from a git repository.

    Args:
        git_url: Git SSH URL (git+ssh://...)
        commit_sha: Commit SHA to checkout
        env: SSH environment for git operations
        output_dir: Directory to output the wheel

    Returns:
        WheelBuildResult with the wheel path and metadata

    Raises:
        WheelBuildError: If the build fails
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="tracecat_wheel_"))

    output_dir.mkdir(parents=True, exist_ok=True)

    # Clone the repository to a temp directory
    async with aiofiles.tempfile.TemporaryDirectory(
        prefix="tracecat_git_"
    ) as clone_dir:
        clone_path = Path(clone_dir)

        # Strip git+ssh:// prefix for git clone
        clone_url = git_url.replace("git+ssh://", "ssh://")

        logger.info(
            "Cloning repository for wheel build",
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
            raise WheelBuildError(f"Failed to clone repository: {error_msg}")

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
            raise WheelBuildError(f"Failed to fetch commit: {error_msg}")

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
            raise WheelBuildError(f"Failed to checkout commit: {error_msg}")

        # Build the wheel from the cloned repository
        return await build_wheel_from_path(clone_path, output_dir)


async def build_builtin_registry_wheel(
    output_dir: Path | None = None,
) -> WheelBuildResult:
    """Build a wheel from the builtin tracecat_registry package.

    This builds the wheel from the installed tracecat_registry package
    source code.

    Args:
        output_dir: Directory to output the wheel

    Returns:
        WheelBuildResult with the wheel path and metadata

    Raises:
        WheelBuildError: If the build fails
    """
    # Get the package path from the installed module
    package_path = Path(tracecat_registry.__file__).parent.parent

    # Check if we have pyproject.toml (we're in the source tree)
    pyproject = package_path / "pyproject.toml"
    if not pyproject.exists():
        # We might be in an installed package, look up one more level
        package_path = package_path.parent
        pyproject = package_path / "pyproject.toml"

    if not pyproject.exists():
        raise WheelBuildError(
            "Cannot find pyproject.toml for tracecat_registry. "
            "Builtin wheel building requires source installation."
        )

    return await build_wheel_from_path(package_path, output_dir)


def _compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def get_wheel_s3_key(
    organization_id: str,
    repository_origin: str,
    version: str,
    wheel_name: str,
) -> str:
    """Generate the S3 key for a wheel file.

    Format: {org_id}/wheels/{origin_slug}/{version}/{wheel_name}

    Args:
        organization_id: Organization UUID
        repository_origin: Repository origin (e.g., "tracecat_registry", "git+ssh://...")
        version: Version string
        wheel_name: Name of the wheel file

    Returns:
        S3 key string
    """
    # Slugify the origin for use in S3 key
    origin_slug = _slugify_origin(repository_origin)
    return f"{organization_id}/wheels/{origin_slug}/{version}/{wheel_name}"


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


async def build_wheelhouse_from_path(
    package_path: Path,
    output_dir: Path | None = None,
) -> WheelhouseBuildResult:
    """Build a wheelhouse of all dependencies for a package.

    Uses `uv pip compile` to get exact dependency versions from the lock file,
    then downloads wheels for all dependencies.

    Args:
        package_path: Path to the package directory (must contain pyproject.toml)
        output_dir: Directory to output the wheels (defaults to temp dir)

    Returns:
        WheelhouseBuildResult with paths to all dependency wheels

    Raises:
        WheelBuildError: If the build fails
    """
    if not package_path.exists():
        raise WheelBuildError(f"Package path does not exist: {package_path}")

    pyproject = package_path / "pyproject.toml"
    if not pyproject.exists():
        raise WheelBuildError(f"No pyproject.toml found in {package_path}")

    # Use temp dir if no output dir specified
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="tracecat_wheelhouse_"))

    output_dir.mkdir(parents=True, exist_ok=True)
    wheelhouse_dir = output_dir / "wheelhouse"
    wheelhouse_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Building wheelhouse from package",
        package_path=str(package_path),
        output_dir=str(wheelhouse_dir),
    )

    # Step 1: Export requirements from lock file using uv
    # This gives us the exact pinned versions
    requirements_file = output_dir / "requirements.txt"
    export_cmd = [
        "uv",
        "export",
        "--frozen",
        "--no-hashes",
        "--no-emit-project",  # Exclude the project itself
        "--no-dev",  # Exclude dev dependencies (type stubs, pytest, etc.)
        "--format",
        "requirements-txt",
        "--output-file",
        str(requirements_file),
    ]
    process = await asyncio.create_subprocess_exec(
        *export_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(package_path),
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode().strip()
        logger.error("Failed to export requirements", error=error_msg)
        raise WheelBuildError(f"Failed to export requirements: {error_msg}")

    if not requirements_file.exists():
        raise WheelBuildError("Requirements file was not created")

    # Compute hash of requirements for caching/deduplication
    requirements_content = requirements_file.read_text()
    requirements_hash = hashlib.sha256(requirements_content.encode()).hexdigest()

    logger.info(
        "Exported requirements",
        requirements_file=str(requirements_file),
        requirements_hash=requirements_hash[:16],
    )

    # Step 2: Download wheels for all dependencies.
    # uv's pip-compatible interface does not implement `download`.
    # Use pip itself via `uv tool run` to fetch wheels without installing.
    wheel_cmd = [
        "uv",
        "tool",
        "run",
        "--from",
        "pip",
        "pip",
        "download",
        "--requirement",
        str(requirements_file),
        "--dest",
        str(wheelhouse_dir),
        "--only-binary",
        ":all:",  # Only download pre-built wheels, no source builds
    ]
    process = await asyncio.create_subprocess_exec(
        *wheel_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(package_path),
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode().strip()
        # Log but don't fail - some packages might not have wheels
        logger.warning(
            "Some wheels could not be downloaded (falling back to any available)",
            error=error_msg,
        )
        # Retry without --only-binary to get source distributions if needed
        wheel_cmd_fallback = [
            "uv",
            "tool",
            "run",
            "--from",
            "pip",
            "pip",
            "download",
            "--requirement",
            str(requirements_file),
            "--dest",
            str(wheelhouse_dir),
        ]
        process = await asyncio.create_subprocess_exec(
            *wheel_cmd_fallback,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(package_path),
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error("Failed to download dependencies", error=error_msg)
            raise WheelBuildError(f"Failed to download dependencies: {error_msg}")

    # Collect all wheel files
    wheel_files = list(wheelhouse_dir.glob("*.whl"))

    logger.info(
        "Wheelhouse built successfully",
        num_wheels=len(wheel_files),
        requirements_hash=requirements_hash[:16],
    )

    return WheelhouseBuildResult(
        wheelhouse_dir=wheelhouse_dir,
        wheel_files=wheel_files,
        requirements_hash=requirements_hash,
    )


def get_wheelhouse_s3_prefix(
    organization_id: str,
    repository_origin: str,
    version: str,
) -> str:
    """Generate the S3 prefix for a wheelhouse directory.

    Format: {org_id}/wheelhouse/{origin_slug}/{version}/

    Args:
        organization_id: Organization UUID
        repository_origin: Repository origin (e.g., "tracecat_registry", "git+ssh://...")
        version: Version string

    Returns:
        S3 key prefix string (ending with /)
    """
    origin_slug = _slugify_origin(repository_origin)
    return f"{organization_id}/wheelhouse/{origin_slug}/{version}/"


async def upload_wheelhouse(
    wheelhouse_result: WheelhouseBuildResult,
    prefix: str,
    bucket: str,
    max_concurrency: int = 10,
) -> str:
    """Upload all wheels in a wheelhouse to S3/MinIO.

    Args:
        wheelhouse_result: Result from build_wheelhouse_from_path
        prefix: S3 key prefix for the wheelhouse
        bucket: Bucket name
        max_concurrency: Maximum number of concurrent uploads

    Returns:
        The S3 URI prefix of the uploaded wheelhouse (s3://{bucket}/{prefix})
    """
    sem = asyncio.Semaphore(max_concurrency)

    async def _upload_single_wheel(wheel_path: Path) -> None:
        async with sem:
            key = f"{prefix}{wheel_path.name}"
            content = wheel_path.read_bytes()
            await blob.upload_file(
                content=content,
                key=key,
                bucket=bucket,
                content_type="application/zip",
            )
            logger.debug(
                "Uploaded dependency wheel",
                wheel_name=wheel_path.name,
                key=key,
            )

    # Upload all wheels concurrently (limited by semaphore)
    await asyncio.gather(
        *[
            _upload_single_wheel(wheel_path)
            for wheel_path in wheelhouse_result.wheel_files
        ]
    )

    s3_uri = f"s3://{bucket}/{prefix}"
    logger.info(
        "Wheelhouse uploaded successfully",
        prefix=prefix,
        bucket=bucket,
        s3_uri=s3_uri,
        num_wheels=wheelhouse_result.num_wheels,
    )
    return s3_uri


async def upload_wheel(
    wheel_path: Path,
    key: str,
    bucket: str,
) -> str:
    """Upload a wheel file to S3/MinIO.

    Args:
        wheel_path: Local path to the wheel file
        key: The S3 object key
        bucket: Bucket name

    Returns:
        The S3 URI of the uploaded wheel (s3://{bucket}/{key})

    Raises:
        FileNotFoundError: If the wheel file doesn't exist
    """
    if not wheel_path.exists():
        raise FileNotFoundError(f"Wheel file not found: {wheel_path}")

    content = wheel_path.read_bytes()

    await blob.upload_file(
        content=content,
        key=key,
        bucket=bucket,
        content_type="application/zip",  # Wheels are zip files
    )

    s3_uri = f"s3://{bucket}/{key}"
    logger.info(
        "Wheel uploaded successfully",
        key=key,
        bucket=bucket,
        s3_uri=s3_uri,
        size=len(content),
    )
    return s3_uri


async def download_wheel(
    key: str,
    bucket: str,
    output_path: Path,
) -> Path:
    """Download a wheel file from S3/MinIO.

    Args:
        key: The S3 object key
        bucket: Bucket name
        output_path: Local path to save the wheel file

    Returns:
        The local path to the downloaded wheel
    """
    content = await blob.download_file(key=key, bucket=bucket)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)

    logger.info(
        "Wheel downloaded successfully",
        key=key,
        bucket=bucket,
        output_path=str(output_path),
        size=len(content),
    )
    return output_path
