"""Run containers inside containers using Podman."""

import os
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
import podman
from loguru import logger
from tracecat.config import (
    TRACECAT__PODMAN_BINARY_PATH,
    TRACECAT__TRUSTED_DOCKER_IMAGES,
    TRACECAT__PODMAN_URI,
)

# Constants
SECURE_NETWORK = "none"
DEFAULT_SECURITY_OPTS = ["no-new-privileges:true", "seccomp=default"]
DEFAULT_CAP_DROP = ["ALL"]


@dataclass
class PodmanResult:
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
    command: list[str] = field(default_factory=list)
    status: str | None = None
    runtime_info: dict = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Whether the container execution was successful.

        Returns
        -------
        bool
            True if the exit code was 0, False otherwise.
        """
        return self.exit_code == 0


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
    # If list is empty or contains just an empty string, all images are trusted (for testing)
    if not TRACECAT__TRUSTED_DOCKER_IMAGES or TRACECAT__TRUSTED_DOCKER_IMAGES == [""]:
        return True
    return image in TRACECAT__TRUSTED_DOCKER_IMAGES


def validate_podman_installation(podman_bin: str):
    """Check if Podman is installed and working.

    Parameters
    ----------
    podman_bin : str
        Path to the podman binary.

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

        logger.debug("Podman version", version=result.stdout.strip())
    except Exception as e:
        logger.error("Failed to run podman", error=e)
        raise


def run_podman_container(
    image: str,
    command: str | list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    volumes: dict[str, dict[str, str] | str] | None = None,
    network: str = SECURE_NETWORK,
    security_opts: list[str] | None = None,
    cap_drop: list[str] | None = None,
    cap_add: list[str] | None = None,
    pull_policy: str = "missing",
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
    volumes : dict of str to dict, optional
        Volume mappings for the container. Keys are host paths, values are dicts
        with 'bind' (container path) and 'mode' ('ro' or 'rw') keys.
    network : str, default 'none'
        Network mode for the container. Defaults to isolated.
    security_opts : list of str, optional
        Security options for the container. Defaults to ["no-new-privileges:true", "seccomp=default"].
    cap_drop : list of str, optional
        Linux capabilities to drop. Defaults to ["ALL"].
    cap_add : list of str, optional
        Linux capabilities to add. Empty by default for maximum security.
    pull_policy : {'always', 'never', 'missing'}, default 'missing'
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
    ...     network="none"
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

    # Initialize runtime info collection
    runtime_info = {
        "logs": [],
        "podman_version": None,
        "container_info": None,
    }

    try:
        # Validate podman
        result = subprocess.run(
            [TRACECAT__PODMAN_BINARY_PATH, "version"],
            capture_output=True,
            text=True,
            check=False,
        )
        runtime_info["podman_version"] = result.stdout.strip()
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
            trusted_images=os.environ.get("TRACECAT__TRUSTED_DOCKER_IMAGES", "").split(
                ","
            ),
        )

        # Set defaults more concisely
        security_opts = (
            DEFAULT_SECURITY_OPTS if security_opts is None else security_opts
        )
        cap_drop = DEFAULT_CAP_DROP if cap_drop is None else cap_drop
        cap_add = cap_add or []
        env_vars = env_vars or {}

        # Connect to the Podman API using a context manager
        with podman.PodmanClient(base_url=TRACECAT__PODMAN_URI) as client:
            # Pull the image if needed
            if pull_policy == "always" or (
                pull_policy == "missing" and not client.images.exists(image)
            ):
                logger.info("Pulling image", image=image)
                client.images.pull(image)

            # Prepare volume binds for podman-py
            volume_binds = {}
            if volumes:
                for host_path, container_config in volumes.items():
                    if isinstance(container_config, dict):
                        container_path = container_config.get("bind")
                        mode = container_config.get("mode", "rw")
                        volume_binds[host_path] = {"bind": container_path, "mode": mode}
                    else:
                        # If it's just a string, assume it's the container path with read-write mode
                        volume_binds[host_path] = {
                            "bind": container_config,
                            "mode": "rw",
                        }

            # Create and run the container
            container = client.containers.create(
                image=image,
                command=command,
                environment=env_vars,
                network_mode=network,
                security_opt=security_opts,
                cap_drop=cap_drop,
                cap_add=cap_add,
                volumes=volume_binds,
                remove=True,
                detach=False,
            )

            container_id = container.id

            # Start the container and get logs
            container.start()
            logs = container.logs(stdout=True, stderr=True, stream=False, follow=True)

            # Convert logs bytes to string (handle both bytes and generator cases)
            if isinstance(logs, bytes):
                logs_str = logs.decode("utf-8")
            elif hasattr(logs, "__iter__"):  # It's an iterator/generator
                # Consume the generator and join the bytes objects, filtering out non-bytes chunks
                logs_bytes = b"".join(
                    chunk for chunk in logs if isinstance(chunk, bytes)
                )
                logs_str = logs_bytes.decode("utf-8")
            else:
                logs_str = str(logs)

            status = "unknown"
            exit_code = 1

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
