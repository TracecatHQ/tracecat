"""Run containers inside containers using Podman."""

import subprocess
from pathlib import Path
import podman
from loguru import logger
from typing import Iterator
from pydantic import BaseModel, Field
from tracecat.config import (
    TRACECAT__PODMAN_BINARY_PATH,
    TRACECAT__TRUSTED_DOCKER_IMAGES,
    TRACECAT__PODMAN_URI,
)
from enum import StrEnum, auto

# Constants - keep hardcoded secure defaults
SECURE_NETWORK = "none"
SECURE_MOUNT_OPTIONS = ["nodev", "nosuid", "noexec"]


class PodmanResult(BaseModel):
    """Result from running a container with Podman.

    Parameters
    ----------
    output : str
        Combined output (stdout/stderr) from the container.
    exit_code : int
        Exit code from the container.
    container_id : str, optional
        ID of the container that was created.
    command : list of str
        The command that was executed.
    status : str, optional
        Final status of the container (e.g., "exited", "error").
    runtime_info : dict
        Runtime diagnostic information including logs, version info, and container details.
    """

    output: str
    exit_code: int
    container_id: str | None = None
    command: list[str] = Field(default_factory=list)
    status: str | None = None
    runtime_info: dict = Field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Whether the container execution was successful.

        Returns
        -------
        bool
            True if the exit code was 0, False otherwise.
        """
        return self.exit_code == 0


class PodmanNetwork(StrEnum):
    """Network modes for Podman containers with strict isolation."""

    NONE = auto()  # Most secure, no network
    BRIDGE = auto()  # Default podman network, required for external services


class PullPolicy(StrEnum):
    """Image pull policies with secure defaults."""

    NEVER = auto()  # Most secure, requires pre-pulled images
    MISSING = auto()  # Pull only if image missing
    ALWAYS = auto()  # Always pull


def is_trusted_image(image: str) -> bool:
    """Check if the image is in the trusted images list.

    Parameters
    ----------
    image : str
        Docker image to check.

    Returns
    -------
    bool
        True if the image is trusted, False otherwise.
    """
    if not TRACECAT__TRUSTED_DOCKER_IMAGES:
        logger.warning("No trusted images defined, rejecting all images")
        return False

    return image in TRACECAT__TRUSTED_DOCKER_IMAGES


def get_podman_version(podman_bin: str) -> str:
    """Get Podman version and verify installation.

    Parameters
    ----------
    podman_bin : str
        Path to the podman binary.

    Returns
    -------
    str
        Version string of Podman installation.

    Raises
    ------
    FileNotFoundError
        If the podman binary is not found.
    RuntimeError
        If the podman version check fails.
    """
    if not Path(podman_bin).exists():
        logger.error("Podman binary not found", path=podman_bin)
        raise FileNotFoundError(f"Podman binary not found at {podman_bin}.")

    try:
        result = subprocess.run(
            [podman_bin, "version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.error("Podman version check failed", stderr=result.stderr)
            raise RuntimeError(f"Podman version check failed: {result.stderr}")

        version = result.stdout.strip()
        logger.debug("Podman version", version=version)
        return version
    except Exception as e:
        logger.error("Failed to run podman", error=e)
        raise


def _process_container_logs(logs: bytes | Iterator[bytes] | str) -> str:
    if isinstance(logs, bytes):
        logs_str = logs.decode("utf-8")
    elif isinstance(logs, Iterator):
        logs_bytes = b"".join(chunk for chunk in logs if isinstance(chunk, bytes))
        logs_str = logs_bytes.decode("utf-8")
    else:
        logs_str = str(logs)
    return logs_str


def run_podman_container(
    image: str,
    command: str | list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    volume_name: str | None = None,  # Single named volume
    volume_path: str | None = None,  # Where to mount it
    network: PodmanNetwork = PodmanNetwork.NONE,
    pull_policy: PullPolicy = PullPolicy.MISSING,
    raise_on_error: bool = False,
) -> PodmanResult:
    """Run a container securely with Podman using functional approach.

    This function applies strict security defaults to minimize attack surface.

    Parameters
    ----------
    image : str
        The container image to run.
    command : str or list of str, optional
        The command to run in the container.
    env_vars : dict of str to str, optional
        Environment variables to set in the container.
    volume_name : str, optional
        Name of the volume to mount.
    volume_path : str, optional
        Path on the host to mount the volume.
    network : PodmanNetwork, default PodmanNetwork.NONE
        Network mode for the container. Defaults to isolated.
    pull_policy : PullPolicy, default PullPolicy.MISSING
        When to pull the image.
    raise_on_error : bool, default False
        If True, raises RuntimeError on container errors.
        If False, returns PodmanResult with error information.

    Returns
    -------
    PodmanResult
        Object containing stdout, stderr, return code, and container ID.

    Raises
    ------
    ValueError
        If the image is not in the trusted images list.
    FileNotFoundError
        If podman binary is not found.

    Examples
    --------
    >>> result = run_podman_container(
    ...     "alpine:latest",
    ...     ["echo", "Hello, World!"],
    ...     network=PodmanNetwork.NONE
    ... )
    >>> print(result.output)
    Hello, World!

    >>> # Example with environment variables
    >>> result = run_podman_container(
    ...     "python:3.10-slim",
    ...     ["python", "-c", "import os; print(os.environ['MY_VAR'])"],
    ...     env_vars={"MY_VAR": "Hello from env"}
    ... )
    >>> print(result.output)
    Hello from env
    """

    # Runtime info collection
    # Track volumes to use in future containers if needed
    runtime_info = {
        "logs": [],
        "podman_version": None,
        "container_info": None,
    }

    try:
        version = get_podman_version(TRACECAT__PODMAN_BINARY_PATH)
        runtime_info["podman_version"] = version
        runtime_info["logs"].append("Podman version validated")

        # Check trusted images
        if not is_trusted_image(image):
            runtime_info["logs"].append(f"Image not in trusted list: {image}")
            return PodmanResult(
                output="Error: Image not in trusted list",
                exit_code=1,
                status="failed",
                runtime_info=runtime_info,
            )

        logger.info(
            "Checked Docker image against trusted images",
            image=image,
            trusted_images=TRACECAT__TRUSTED_DOCKER_IMAGES,
        )

        volume_mounts = {}
        if volume_name and volume_path:
            volume_mounts[volume_name] = {
                "bind": volume_path,
                "mode": "rw",  # Required for terraform state
                "options": SECURE_MOUNT_OPTIONS,  # Defense in depth with setup-podman.sh
            }

        # Connect to the Podman API using a context manager
        with podman.PodmanClient(base_url=TRACECAT__PODMAN_URI) as client:
            # Pull the image if needed
            if pull_policy == PullPolicy.ALWAYS or (
                pull_policy == PullPolicy.MISSING and not client.images.exists(image)
            ):
                logger.info("Pulling image", image=image)
                client.images.pull(image)

            # Create and run the container
            container = client.containers.create(
                image=image,
                command=command,
                environment=env_vars or {},
                network_mode=network,
                volumes=volume_mounts,
                remove=True,
                detach=False,
                user="1000:1000",
                read_only=True,
            )

            # Start the container and get logs
            container.start()
            logs = container.logs(stdout=True, stderr=True, stream=False, follow=True)
            logs_str = _process_container_logs(logs)

            status = "unknown"
            exit_code = 1
            container_id = container.id

            if container_id is None:
                logger.error("Container ID is None, cannot inspect container")
                exit_code = 1
                status = "error"
            else:
                container_info = client.containers.get(container_id).inspect()
                exit_code = container_info.get("State", {}).get("ExitCode", 0)
                status = container_info.get("State", {}).get("Status", "unknown")

            # Add podman command to the result
            executed_cmd = ["podman", "run", "--network", network, f"--image={image}"]
            if command:
                executed_cmd.extend(command)

            # Store container info in runtime_info
            if container_id:
                runtime_info["container_info"] = container.inspect()
                runtime_info["logs"].append(f"Container {container_id} executed")

            if raise_on_error and exit_code != 0:
                error_context = {
                    "status": status,
                    "exit_code": exit_code,
                    "container_id": container_id,
                    "last_log_lines": logs_str.strip()[-500:]
                    if logs_str
                    else "No logs available",
                }

                # Log full debug information
                logger.error(
                    "Container execution failed",
                    **error_context,
                    runtime_info=runtime_info,
                )

                # Raise with enough context to debug but without exposing internals
                error_msg = (
                    f"Container execution failed:\n"
                    f"Status: {status}\n"
                    f"Exit code: {exit_code}\n"
                    f"Container ID: {container_id}\n"
                    f"Last logs:\n{error_context['last_log_lines']}"
                )
                raise RuntimeError(error_msg)

            return PodmanResult(
                output=logs_str,
                exit_code=exit_code,
                container_id=container_id,
                command=executed_cmd,
                status=status,
                runtime_info=runtime_info,
            )

    except Exception as e:
        runtime_info["logs"].append(f"Error: {str(e)}")
        if raise_on_error:
            logger.error(
                "Container execution failed", error=str(e), runtime_info=runtime_info
            )
            raise RuntimeError(f"Error running Podman container: {str(e)}") from e

        return PodmanResult(
            output=f"Error running Podman container: {str(e)}",
            exit_code=1,
            status="error",
            runtime_info=runtime_info,
        )
