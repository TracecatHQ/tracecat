"""Run containers inside containers using Podman."""

from collections.abc import Iterator
from enum import StrEnum, auto

from loguru import logger
from pydantic import BaseModel, Field

import podman
from tracecat.config import (
    TRACECAT__PODMAN_URI,
    TRACECAT__TRUSTED_DOCKER_IMAGES,
)

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


def is_trusted_image(image: str, trusted_images: list[str] | None = None) -> bool:
    """Check if the image is in the trusted images list.

    Parameters
    ----------
    image : str
        Docker image to check.
    trusted_images : list of str, optional
        Override the default trusted images list.

    Returns
    -------
    bool
        True if the image is trusted, False otherwise.
    """
    trusted_images = trusted_images or TRACECAT__TRUSTED_DOCKER_IMAGES
    if not trusted_images:
        logger.warning("No trusted images defined, rejecting all images.")
        return False

    logger.info(
        "Checked Docker image against trusted images",
        image=image,
        trusted_images=trusted_images,
    )
    return image in trusted_images


def get_podman_client(base_url: str | None = None) -> podman.PodmanClient:
    """Get a Podman client.

    Parameters
    ----------
    base_url : str, optional
        Override the default Podman API URL for testing
    """
    base_url = base_url or TRACECAT__PODMAN_URI
    return podman.PodmanClient(base_url=base_url)


def get_podman_version(base_url: str | None = None) -> str:
    """Get Podman version from the remote podman service.

    Parameters
    ----------
    base_url : str, optional
        Override the default Podman API URL for testing

    Returns
    -------
    str
        Version string of Podman installation.

    Raises
    ------
    RuntimeError
        If the podman version check fails.
    """
    with get_podman_client(base_url=base_url) as client:
        version_info = client.version()
        logger.debug(
            "Podman version",
            version_info=version_info,
        )
        return version_info["Version"]


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
    base_url: str | None = None,
    trusted_images: list[str] | None = None,
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
    base_url : str, optional
        Override the default Podman API URL.
    trusted_images : list of str, optional
        Override the default trusted images list.

    Returns
    -------
    PodmanResult
        Object containing stdout, stderr, return code, and container ID.

    Raises
    ------
    ValueError
        If the image is not in the trusted images list.
    RuntimeError
        If podman service is not available.

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
        # Use the provided base_url or fall back to config
        version = get_podman_version(base_url=base_url)
        runtime_info["podman_version"] = version

        # Check trusted images
        if not is_trusted_image(image, trusted_images):
            runtime_info["logs"].append(f"Image not in trusted list: {image}")
            return PodmanResult(
                output="Image not in trusted list",
                exit_code=1,
                status="failed",
                runtime_info=runtime_info,
            )

        volume_mounts = {}
        if volume_name and volume_path:
            volume_mounts[volume_name] = {
                "bind": volume_path,
                "mode": "rw",  # Required for terraform state
                "options": SECURE_MOUNT_OPTIONS,  # Defense in depth with setup-podman.sh
            }

        # Connect to the podman service
        with get_podman_client(base_url=base_url) as client:
            # Pull image if needed
            if pull_policy == PullPolicy.ALWAYS or (
                pull_policy == PullPolicy.MISSING and not client.images.exists(image)
            ):
                logger.info("Pulling image", image=image)
                try:
                    client.images.pull(image)
                    runtime_info["logs"].append(f"Pulled image: {image}")
                except Exception as e:
                    logger.error("Failed to pull image", image=image, error=e)
                    runtime_info["logs"].append(f"Failed to pull image: {image}")
                    return PodmanResult(
                        output=f"Error: Failed to pull image: {e}",
                        exit_code=1,
                        status="failed",
                        runtime_info=runtime_info,
                    )

            # Prepare container configuration
            container_config = {
                "image": image,
                "command": command,
                "environment": env_vars or {},
                "network_mode": network.value.lower(),
                "remove": True,  # Auto-remove container after execution
                "detach": True,  # Run in background
                "volumes": volume_mounts,
            }

            # Create and start container
            logger.info("Creating container", image=image, command=command)
            container = client.containers.create(**container_config)
            container_id = container.id
            runtime_info["container_info"] = {"id": container_id}

            try:
                container.start()
                logs = container.logs(stream=True, follow=True)
                output = _process_container_logs(logs)

                # Wait for container to finish
                result = container.wait()
                exit_code = (
                    result["StatusCode"]
                    if isinstance(result, dict) and "StatusCode" in result
                    else -1
                )
                status = "exited"

                # Get final container info
                try:
                    if container_id:
                        container_info = client.containers.get(container_id).attrs
                        runtime_info["container_info"] = container_info
                except Exception as e:
                    logger.warning(
                        "Failed to get container info after execution", error=e
                    )

                # Return result
                result = PodmanResult(
                    output=output,
                    exit_code=exit_code,
                    container_id=container_id,
                    command=command
                    if isinstance(command, list)
                    else [command]
                    if command
                    else [],
                    status=status,
                    runtime_info=runtime_info,
                )

                if exit_code != 0 and raise_on_error:
                    raise RuntimeError(
                        f"Container exited with non-zero code: {exit_code}. Output: {output}"
                    )

                return result

            except Exception as e:
                logger.error("Error running container", error=e)
                # Try to get logs if possible
                try:
                    logs = container.logs()
                    output = _process_container_logs(logs)
                except Exception as log_e:
                    logger.warning("Failed to get container logs", error=log_e)
                    output = f"Error: {e}. Failed to get logs: {log_e}"

                # Try to remove the container
                try:
                    container.remove(force=True)
                except Exception as rm_e:
                    logger.warning("Failed to remove container", error=rm_e)

                result = PodmanResult(
                    output=output,
                    exit_code=1,
                    container_id=container_id,
                    command=command
                    if isinstance(command, list)
                    else [command]
                    if command
                    else [],
                    status="error",
                    runtime_info=runtime_info,
                )
                return result

    except Exception as e:
        logger.error("Failed to run podman container", error=e)
        runtime_info["logs"].append(f"Failed to run container: {e}")

        if raise_on_error:
            raise RuntimeError(f"Error running container: {e}") from e

        return PodmanResult(
            output=f"Error: {e}",
            exit_code=1,
            status="error",
            runtime_info=runtime_info,
        )
