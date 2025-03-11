"""Run containers inside containers using Podman."""

from collections.abc import Iterator
from enum import StrEnum, auto

from loguru import logger
from pydantic import BaseModel, Field

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
    status : str, optional
        Final status of the container (e.g., "exited", "error").
    runtime_info : dict
        Runtime diagnostic information including logs, version info, and container details.
    """

    image: str
    logs: str | list[str]
    exit_code: int
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

            # Run container and get output directly
            # Returns logs as an iterator after container exits
            logger.info("Running container", image=image, command=command)
            logs = client.containers.run(
                image=image,
                command=command,
                environment=env_vars,
                network_mode=network_mode,
                remove=True,
                detach=False,
                stdout=True,
                stderr=True,
                stream=False,
                volumes=volume_mounts,
            )

            if not isinstance(logs, Iterator):
                raise ValueError(f"Expected output to be an iterator, got {type(logs)}")

            return PodmanResult(
                logs=list(logs),
                exit_code=0,
                image=image,
                command=command,
                status="exited",
                runtime_info=runtime_info,
            )

    except ContainerError as e:
        logger.error("Container error", error=e)
        if raise_on_error:
            raise

        stderr: Iterator[bytes] = e.stderr  # type: ignore

        return PodmanResult(
            logs=[s.decode() for s in stderr],
            exit_code=e.exit_status,
            image=e.image,
            command=command,
            status="ContainerError",
            runtime_info=runtime_info,
        )

    except ImageNotFound as e:
        logger.error("Image not found", error=e)
        if raise_on_error:
            raise

        return PodmanResult(
            logs=str(e),
            exit_code=1,
            image=image,
            command=command,
            status="ImageNotFound",
            runtime_info=runtime_info,
        )

    except APIError as e:
        logger.error("Podman API error", error=e)
        if raise_on_error:
            raise

        return PodmanResult(
            logs=str(e),
            exit_code=1,
            image=image,
            command=command,
            status="APIError",
            runtime_info=runtime_info,
        )

    except Exception as e:
        logger.error("Unexpected error", error=e)
        raise
