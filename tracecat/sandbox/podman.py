"""Run containers inside containers using Podman."""

from collections.abc import Iterator
from enum import StrEnum, auto

from loguru import logger
from pydantic import BaseModel

import podman
from podman.errors import APIError, ContainerError, ImageNotFound
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
    runtime_info : dict
        Runtime diagnostic information including logs, version info, and container details.
    """

    image: str
    logs: list[str]
    exit_code: int
    command: list[str]
    runtime_info: dict

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

    logger.info(
        "Checking Docker image against trusted images",
        image=image,
        trusted_images=trusted_images,
    )

    # Use the provided trusted images or the default list
    trusted_images = trusted_images or TRACECAT__TRUSTED_DOCKER_IMAGES
    if not trusted_images:
        logger.warning("No trusted images defined, rejecting all images.")
        return False

    return image in trusted_images


def get_podman_client(base_url: str | None = None) -> podman.PodmanClient:
    """Get a Podman client.

    Parameters
    ----------
    base_url : str, optional
        Override the default Podman API URL for testing
    """
    base_url = base_url or TRACECAT__PODMAN_URI
    if base_url.startswith("unix://"):
        return podman.PodmanClient(base_url=base_url, remote=True)
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


def _process_logs(logs: bytes | Iterator[bytes] | str | int) -> list[str]:
    """Process logs from a container."""
    if isinstance(logs, bytes):
        return [logs.decode()]
    if isinstance(logs, str):
        return [logs]
    if isinstance(logs, int):
        return [str(logs)]
    return [log.decode() if isinstance(log, bytes) else str(log) for log in logs]


def run_podman_container(
    image: str,
    command: str | list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    volume_name: str | None = None,  # Single named volume
    volume_path: str | None = None,  # Where to mount it
    network: PodmanNetwork = PodmanNetwork.NONE,
    pull_policy: PullPolicy = PullPolicy.MISSING,
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
    ContainerError
        If the container fails to start.
    ImageNotFound
        If the image does not exist.
    APIError
        If the Podman API returns an error.

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

    env_vars = env_vars or {}
    network_mode = network.value.lower()
    command = command or []
    if isinstance(command, str):
        command = [command]

    try:
        # Use the provided base_url or fall back to config
        version = get_podman_version(base_url=base_url)
        runtime_info["podman_version"] = version

        # Check trusted images
        if not is_trusted_image(image, trusted_images):
            raise ValueError(f"Image {image!r} not in trusted list: {trusted_images}")

        volume_mounts = {}
        if volume_name and volume_path:
            volume_mounts[volume_name] = {
                "bind": volume_path,
                "mode": "rw",
                "options": SECURE_MOUNT_OPTIONS,
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
                    raise RuntimeError(f"Failed to pull image: {e}") from e

            # Create container and get container ID
            container = client.containers.create(
                image=image,
                command=command,
                environment=env_vars,
                network_mode=network_mode,
                volumes=volume_mounts,
                timezone="local",
            )
            container_id = container.id

            # Start the container and wait for it to finish
            container.start()
            result = container.wait()
            logs = _process_logs(container.logs())

            if container_id:
                # Remove the container after use
                client.containers.remove(container_id)

            if isinstance(result, dict):
                exit_code = result.get("StatusCode")
            elif isinstance(result, int):
                exit_code = result
            else:
                raise ValueError(
                    f"Unexpected container wait result: {result}."
                    f" Expected dict or int, got {type(result)}"
                )

            if exit_code is None or exit_code != 0:
                logger.error(
                    "Container exited with non-zero exit code",
                    exit_code=exit_code,
                    container_id=container_id,
                    command=command,
                    logs=logs,
                )
                raise RuntimeError(
                    f"Container {container_id} exited with code {exit_code}."
                    f"\nCommand: {command}"
                    f"\nLogs: {logs}"
                )

            return PodmanResult(
                logs=logs,
                exit_code=exit_code,
                image=image,
                command=command,
                runtime_info=runtime_info,
            )

    except ContainerError as e:
        logger.error("Container error", error=e)
        raise

    except ImageNotFound as e:
        logger.error("Image not found", error=e)
        raise

    except APIError as e:
        logger.error("Podman API error", error=e)
        raise

    except Exception as e:
        logger.error("Unexpected error", error=e)
        raise


def remove_podman_volumes(
    volume_name: str | list[str] | None = None, base_url: str | None = None
):
    """Remove volumes from the podman service.

    Parameters
    ----------
    volume_name : str
        The name of the volume to remove. If not provided, all volumes will be removed.
    base_url : str, optional
        Override the default Podman API URL.
    """
    if isinstance(volume_name, str):
        volume_name = [volume_name]

    with get_podman_client(base_url=base_url) as client:
        if volume_name:
            for name in volume_name:
                logger.info("Removing volume", volume_name=name)
                client.volumes.remove(name)
        else:
            for volume in client.volumes.list():
                logger.info("Removing volume", volume_name=volume.name)
                name = volume.name
                if name:
                    client.volumes.remove(name)


def list_podman_volumes(base_url: str | None = None) -> list[str]:
    """List all volumes from the podman service.

    Parameters
    ----------
    base_url : str, optional
        Override the default Podman API URL.

    Returns
    -------
    list of str
        A list of volume names.
    """

    with get_podman_client(base_url=base_url) as client:
        return [volume.name for volume in client.volumes.list() if volume.name]
