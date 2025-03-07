import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from loguru import logger

from registry.tracecat_registry.experimental.podman import (
    TRACECAT__PODMAN_URI,
    run_podman_container,
    validate_podman_installation,
)


# Check if podman is installed on the host system
def is_podman_available():
    try:
        subprocess.run(["podman", "--version"], check=True, capture_output=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


# Skip all tests if podman is not available
pytestmark = pytest.mark.skipif(
    not is_podman_available(),
    reason="Podman is not installed or not working on this system",
)


# Fixture for podman binary path
@pytest.fixture
def podman_bin() -> str:
    """Get the path to the podman binary."""
    try:
        podman_path = subprocess.run(
            ["which", "podman"], check=True, capture_output=True, text=True
        ).stdout.strip()
        return podman_path
    except subprocess.SubprocessError:
        logger.warning("Podman binary not found, skipping tests")
        pytest.skip("Podman binary not found")


# Fixture to mock podman validation
@pytest.fixture
def mock_validate_podman():
    """Mock the validate_podman_installation function to avoid actual validation."""
    with mock.patch(
        "registry.tracecat_registry.experimental.podman.validate_podman_installation"
    ) as mock_validate:
        # Make the validation function do nothing
        mock_validate.return_value = None
        yield mock_validate


# Fixture to mock the is_trusted_image function to always return True
@pytest.fixture
def mock_trusted_image():
    """Mock the is_trusted_image function to always return True for testing."""
    with mock.patch(
        "registry.tracecat_registry.experimental.podman.is_trusted_image"
    ) as mock_trust:
        # Always consider images trusted for testing
        mock_trust.return_value = True
        yield mock_trust


# Mock PodmanClient for container operations
@pytest.fixture
def mock_podman_client():
    """Mock the podman.PodmanClient to avoid actual container operations."""
    # Create a context manager mock
    context_mock = mock.MagicMock()
    client_mock = mock.MagicMock()

    # Setup the context manager to return the client
    context_mock.__enter__.return_value = client_mock
    context_mock.__exit__.return_value = None

    # Setup mock container instance
    mock_container = mock.MagicMock()
    mock_container.id = "test-container-id"
    mock_container.logs.return_value = b"Container log output"

    # Setup container inspect
    container_info = {"State": {"ExitCode": 0, "Status": "exited"}}
    mock_container.inspect.return_value = container_info

    # Setup mock containers collection
    mock_containers = mock.MagicMock()
    mock_containers.create.return_value = mock_container
    mock_containers.get.return_value = mock_container

    # Setup mock images collection
    mock_images = mock.MagicMock()
    mock_images.exists.return_value = False
    mock_images.pull.return_value = None

    # Assign collections to client
    client_mock.containers = mock_containers
    client_mock.images = mock_images

    with mock.patch(
        "podman.PodmanClient", return_value=context_mock
    ) as mock_client_class:
        yield mock_client_class


# Fixture to set the environment variable for tests
@pytest.fixture(autouse=True)
def set_podman_env(podman_bin):
    """Set the TRACECAT__PODMAN_BINARY_PATH environment variable."""
    # Store the original values to restore later
    original_binary_path = os.environ.get("TRACECAT__PODMAN_BINARY_PATH")
    original_trusted_images = os.environ.get("TRACECAT__TRUSTED_DOCKER_IMAGES")
    original_podman_uri = os.environ.get("TRACECAT__PODMAN_URI")

    # Set the environment variables for testing
    os.environ["TRACECAT__PODMAN_BINARY_PATH"] = podman_bin
    os.environ["TRACECAT__TRUSTED_DOCKER_IMAGES"] = (
        "alpine:latest,python:3.9-slim,ghcr.io/datadog/stratus-red-team:latest"
    )
    os.environ["TRACECAT__PODMAN_URI"] = "unix:///tmp/podman.sock"

    logger.debug(
        "Setting up environment variables for testing",
        podman_path=podman_bin,
        podman_uri="unix:///tmp/podman.sock",
    )

    yield

    # Restore the original values or remove if they weren't set
    if original_binary_path:
        os.environ["TRACECAT__PODMAN_BINARY_PATH"] = original_binary_path
    else:
        os.environ.pop("TRACECAT__PODMAN_BINARY_PATH", None)

    if original_trusted_images:
        os.environ["TRACECAT__TRUSTED_DOCKER_IMAGES"] = original_trusted_images
    else:
        os.environ.pop("TRACECAT__TRUSTED_DOCKER_IMAGES", None)

    if original_podman_uri:
        os.environ["TRACECAT__PODMAN_URI"] = original_podman_uri
    else:
        os.environ.pop("TRACECAT__PODMAN_URI", None)

    logger.debug("Environment variables cleaned up")


@pytest.fixture
def cleanup_containers():
    """Fixture to clean up test containers after tests."""
    container_ids = []

    # This is a callback that tests can use to register containers for cleanup
    def _register_container(container_id):
        if container_id:
            container_ids.append(container_id)

    yield _register_container

    # Clean up all registered containers
    for container_id in container_ids:
        try:
            subprocess.run(
                ["podman", "rm", "-f", container_id], check=False, capture_output=True
            )
            logger.debug("Removed container", container_id=container_id)
        except Exception as e:
            logger.warning(
                "Failed to remove container", container_id=container_id, error=str(e)
            )


# Test Docker container image building for development purposes only (not part of automated tests)
@pytest.mark.skip(
    reason="These tests require privileged Docker access and are for manual execution only"
)
def test_podman_in_prod_image():
    """Test that podman is properly installed in the production Docker image."""
    # Build the image
    image_tag = "tracecat-test-prod:latest"
    build_cmd = ["docker", "build", "-f", "Dockerfile", "-t", image_tag, "."]

    try:
        logger.info("Building production Docker image", image_tag=image_tag)
        subprocess.run(build_cmd, check=True, capture_output=True)

        # Run podman inside the container with proper privileges
        result = subprocess.run(
            ["docker", "run", "--privileged", "--rm", image_tag, "podman", "--version"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "podman version" in result.stdout.lower()
        logger.info("Podman verified in production Docker image", stdout=result.stdout)

    finally:
        # Clean up
        logger.debug("Cleaning up Docker image", image_tag=image_tag)
        subprocess.run(["docker", "rmi", image_tag], check=False)


@pytest.mark.skip(
    reason="These tests require privileged Docker access and are for manual execution only"
)
def test_podman_in_dev_image():
    """Test that podman is properly installed in the development Docker image."""
    # Build the image
    image_tag = "tracecat-test-dev:latest"
    build_cmd = ["docker", "build", "-f", "Dockerfile.dev", "-t", image_tag, "."]

    try:
        logger.info("Building development Docker image", image_tag=image_tag)
        subprocess.run(build_cmd, check=True, capture_output=True)

        # Run podman inside the container with proper privileges
        result = subprocess.run(
            ["docker", "run", "--privileged", "--rm", image_tag, "podman", "--version"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "podman version" in result.stdout.lower()
        logger.info("Podman verified in development Docker image", stdout=result.stdout)

    finally:
        # Clean up
        logger.debug("Cleaning up Docker image", image_tag=image_tag)
        subprocess.run(["docker", "rmi", image_tag], check=False)


# Tests for run_podman_container function
def test_echo_hello_world(
    podman_bin,
    mock_validate_podman,
    mock_trusted_image,
    mock_podman_client,
    cleanup_containers,
):
    """Test running a simple echo command in a container."""
    # Configure the container logs mock to return hello world
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value
    mock_container.logs.return_value = b"hello world"

    # Set the container status
    mock_container.inspect.return_value = {"State": {"ExitCode": 0, "Status": "exited"}}

    logger.info("Testing echo hello world container", image="alpine:latest")
    result = run_podman_container(
        image="alpine:latest", command=["echo", "hello world"]
    )

    # Verify client was created with correct URI
    mock_podman_client.assert_called_once_with(base_url=TRACECAT__PODMAN_URI)

    # Verify container was created with correct parameters
    client_mock.containers.create.assert_called_once()
    create_args = client_mock.containers.create.call_args[1]
    assert create_args["image"] == "alpine:latest"
    assert create_args["command"] == ["echo", "hello world"]
    assert create_args["network_mode"] == "none"

    # Verify container was started
    mock_container.start.assert_called_once()

    # Verify logs were fetched
    mock_container.logs.assert_called_once()

    # Check the result
    assert result.success
    assert "hello world" in result.output
    assert result.exit_code == 0
    assert result.container_id == "test-container-id"
    assert result.status == "exited"

    # Register container for cleanup (even though it's mocked in this test)
    cleanup_containers(result.container_id)
    logger.debug("Echo container test completed successfully")


@pytest.mark.webtest
def test_run_stratus_red_team_list(
    podman_bin,
    mock_validate_podman,
    mock_trusted_image,
    mock_podman_client,
    cleanup_containers,
):
    """Test running the stratus-red-team list command."""
    # Configure the container logs mock to return stratus-red-team output
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value
    mock_container.logs.return_value = b"""
ID                           TACTIC         TECHNIQUE                          PLATFORM
aws.credential-access.ec2-get-password-data credential-access Retrieve EC2 Password Data aws
aws.credential-access.ec2-steal-instance-credentials credential-access Steal EC2 Instance Credentials aws
aws.credential-access.secretsmanager-batch-retrieve credential-access Retrieve a High Number of Secrets Manager secrets (Batch) aws
    """

    # Set the container status
    mock_container.inspect.return_value = {"State": {"ExitCode": 0, "Status": "exited"}}

    logger.info(
        "Testing stratus-red-team list container",
        image="ghcr.io/datadog/stratus-red-team:latest",
    )
    result = run_podman_container(
        image="ghcr.io/datadog/stratus-red-team:latest", command=["list"]
    )

    # Verify container was created with correct parameters
    client_mock.containers.create.assert_called_once()
    create_args = client_mock.containers.create.call_args[1]
    assert create_args["image"] == "ghcr.io/datadog/stratus-red-team:latest"
    assert create_args["command"] == ["list"]

    # Check the result
    assert result.success
    assert "ID" in result.output
    assert "TACTIC" in result.output
    assert "TECHNIQUE" in result.output
    assert result.exit_code == 0
    assert result.container_id == "test-container-id"
    assert result.status == "exited"

    # Register container for cleanup (even though it's mocked in this test)
    cleanup_containers(result.container_id)
    logger.debug("Stratus red team container test completed successfully")


@pytest.mark.webtest
def test_external_network_call(
    podman_bin,
    mock_validate_podman,
    mock_trusted_image,
    mock_podman_client,
    cleanup_containers,
):
    """Test making a secure external network call."""
    # Configure the container logs mock to return HTTP response
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value
    mock_container.logs.return_value = b"""
200
{
  "args": {},
  "headers": {
    "Accept": "*/*",
    "Host": "httpbin.org",
    "User-Agent": "python-requests/2.28.1",
    "X-Amzn-Trace-Id": "Root=1-abcdef123"
  },
  "origin": "192.168.1.1",
  "url": "https://httpbin.org/get"
}
    """

    # Set the container status
    mock_container.inspect.return_value = {"State": {"ExitCode": 0, "Status": "exited"}}

    logger.info("Testing external network call container", image="python:3.9-slim")
    # Create a simple Python script for demonstration
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as temp_file:
        temp_file.write("""
import requests
import sys

try:
    response = requests.get('https://httpbin.org/get', timeout=5)
    print(response.status_code)
    print(response.text)
    sys.exit(0)
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
        """)
        script_path = temp_file.name

    try:
        # Mount the script into the container and run it
        host_path = script_path
        container_path = "/script.py"

        result = run_podman_container(
            image="python:3.9-slim",
            command=["python", container_path],
            volumes={host_path: {"bind": container_path, "mode": "ro"}},
        )

        # Verify container was created with correct parameters
        client_mock.containers.create.assert_called_once()
        create_args = client_mock.containers.create.call_args[1]
        assert create_args["image"] == "python:3.9-slim"
        assert create_args["command"] == ["python", container_path]

        # Verify volumes were passed correctly
        assert host_path in create_args["volumes"]
        assert create_args["volumes"][host_path]["bind"] == container_path
        assert create_args["volumes"][host_path]["mode"] == "ro"

        # Check the result
        assert result.success
        assert "200" in result.output
        assert "httpbin.org" in result.output
        assert result.exit_code == 0
        assert result.container_id == "test-container-id"
        assert result.status == "exited"

        # Register container for cleanup (even though it's mocked in this test)
        cleanup_containers(result.container_id)
        logger.debug("External network call container test completed successfully")

    finally:
        # Clean up the temporary file
        try:
            os.unlink(script_path)
            logger.debug("Temporary script file cleaned up", path=script_path)
        except OSError as e:
            logger.warning(
                "Failed to remove temporary script file", path=script_path, error=str(e)
            )


def test_container_failure(
    podman_bin, mock_validate_podman, mock_trusted_image, mock_podman_client
):
    """Test container execution failure handling."""
    # Configure the container to have a non-zero exit code
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value
    mock_container.logs.return_value = b"command not found: invalid_command"

    # Set the container status to have a non-zero exit code
    mock_container.inspect.return_value = {
        "State": {
            "ExitCode": 127,  # Command not found exit code
            "Status": "error",
        }
    }

    logger.info("Testing container failure", image="alpine:latest")
    result = run_podman_container(image="alpine:latest", command=["invalid_command"])

    # Check the result
    assert not result.success
    assert "command not found" in result.output
    assert result.exit_code == 127
    assert result.container_id == "test-container-id"
    assert result.status == "error"

    logger.debug("Container failure test completed successfully")


def test_string_command_and_env_vars(
    podman_bin, mock_validate_podman, mock_trusted_image, mock_podman_client
):
    """Test string command conversion and environment variables."""
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value
    mock_container.logs.return_value = b"HELLO=WORLD"

    # Set the container status
    mock_container.inspect.return_value = {"State": {"ExitCode": 0, "Status": "exited"}}

    logger.info(
        "Testing string command and environment variables", image="alpine:latest"
    )
    result = run_podman_container(
        image="alpine:latest",
        command="echo $HELLO",  # Test string command (should be converted to list)
        env_vars={"HELLO": "WORLD"},  # Test environment variables
    )

    # Verify container was created with correct parameters
    client_mock.containers.create.assert_called_once()
    create_args = client_mock.containers.create.call_args[1]

    # Verify string command was converted to list
    assert create_args["command"] == ["echo $HELLO"]

    # Verify environment variables were passed correctly
    assert create_args["environment"] == {"HELLO": "WORLD"}

    # Check the result
    assert result.success
    assert "HELLO=WORLD" in result.output
    assert result.exit_code == 0
    assert result.status == "exited"

    logger.debug("String command and environment variables test completed successfully")


def test_container_null_id(
    podman_bin, mock_validate_podman, mock_trusted_image, mock_podman_client
):
    """Test handling of null container ID."""
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value

    # Set the container ID to None to test that branch
    mock_container.id = None
    mock_container.logs.return_value = b"test output"

    logger.info("Testing null container ID handling", image="alpine:latest")
    result = run_podman_container(image="alpine:latest", command=["echo", "test"])

    # Verify the result handles None container ID
    assert result.container_id is None
    assert result.status == "error"

    # Should still have output but exit code would be set to 1
    assert "test output" in result.output
    assert result.exit_code == 1

    logger.debug("Null container ID test completed successfully")


def test_validate_podman_installation_with_mocks():
    """Test the validate_podman_installation function using mocks."""
    with (
        mock.patch("subprocess.run") as mock_run,
        mock.patch("pathlib.Path.exists", return_value=True),
    ):
        # Simulate successful podman version check
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "podman version 4.3.1"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        logger.info("Testing podman installation validation with mocks")
        # Should not raise any exceptions
        validate_podman_installation("/path/to/podman")

        # Verify the right command was called
        mock_run.assert_called_once_with(
            ["/path/to/podman", "version"], capture_output=True, text=True, check=False
        )

        # Now test error cases
        mock_run.reset_mock()

        # Simulate podman version check failure
        mock_result.returncode = 1
        mock_result.stderr = "Some error occurred"

        logger.info("Testing podman installation validation failure case")
        # Should raise RuntimeError
        with pytest.raises(RuntimeError):
            validate_podman_installation("/path/to/podman")

        # Verify the command was called
        mock_run.assert_called_once()
        logger.debug("Podman installation validation tests completed successfully")


def test_untrusted_image_handling(podman_bin, mock_validate_podman, mock_podman_client):
    """Test that untrusted images are properly rejected."""
    # Configure the mock to specifically reject this image
    with mock.patch(
        "registry.tracecat_registry.experimental.podman.is_trusted_image"
    ) as mock_trust:
        mock_trust.return_value = False

        logger.info("Testing untrusted image rejection", image="untrusted:latest")
        result = run_podman_container(
            image="untrusted:latest", command=["echo", "This should not run"]
        )

        # Verify the untrusted image was rejected
        assert not result.success
        assert result.exit_code == 1
        assert "Error: Image not in trusted list" in result.output
        assert result.container_id is None
        assert result.status == "failed"

        # Verify create was never called since the image check failed
        client_mock = mock_podman_client.return_value.__enter__.return_value
        client_mock.containers.create.assert_not_called()

        logger.debug("Untrusted image rejection test completed successfully")


@pytest.mark.parametrize(
    "pull_policy,image_exists,should_pull",
    [
        ("always", True, True),
        ("always", False, True),
        ("never", True, False),
        ("never", False, False),
        ("missing", True, False),
        ("missing", False, True),
    ],
)
def test_image_pull_policies(
    pull_policy: str,
    image_exists: bool,
    should_pull: bool,
    podman_bin: str,
    mock_validate_podman: mock.MagicMock,
    mock_trusted_image: mock.MagicMock,
    mock_podman_client: mock.MagicMock,
    mock_container: mock.MagicMock,
):
    """Test different image pull policies."""
    # Reset mocks and set image existence
    mock_podman_client.reset_mock()
    mock_podman_client.images.exists.return_value = image_exists

    logger.info(
        f"Testing pull policy '{pull_policy}' with image_exists={image_exists}",
        image="alpine:latest",
    )

    result = run_podman_container(
        image="alpine:latest", command=["echo", "test"], pull_policy=pull_policy
    )

    # Verify pull behavior
    if should_pull:
        mock_podman_client.images.pull.assert_called_once_with("alpine:latest")
    else:
        mock_podman_client.images.pull.assert_not_called()

    assert result.success
    assert result.exit_code == 0


@pytest.fixture
def security_test_params() -> dict[str, list[str] | str]:
    """Fixture providing security test parameters."""
    return {
        "security_opts": ["seccomp=unconfined"],
        "cap_drop": ["NET_ADMIN"],
        "cap_add": ["SYS_ADMIN"],
        "network": "host",  # network is a string, not a list
    }


def test_security_options(
    podman_bin: str,
    mock_validate_podman: mock.MagicMock,
    mock_trusted_image: mock.MagicMock,
    mock_podman_client: mock.MagicMock,
    mock_container: mock.MagicMock,
    security_test_params: dict[str, list[str] | str],
):
    """Test security options handling."""
    mock_container.logs.return_value = b"security test"

    result = run_podman_container(
        image="alpine:latest",
        command=["echo", "security test"],
        security_opts=security_test_params["security_opts"],  # type: ignore
        cap_drop=security_test_params["cap_drop"],  # type: ignore
        cap_add=security_test_params["cap_add"],  # type: ignore
        network=security_test_params["network"],  # type: ignore
    )

    create_args = mock_podman_client.containers.create.call_args[1]
    assert create_args["security_opt"] == security_test_params["security_opts"]
    assert create_args["cap_drop"] == security_test_params["cap_drop"]
    assert create_args["cap_add"] == security_test_params["cap_add"]
    assert create_args["network_mode"] == security_test_params["network"]

    assert result.success
    assert "security test" in result.output


@pytest.mark.parametrize(
    "exception_msg,expected_msg",
    [
        ("Simulated Podman API error", "Error running Podman container"),
        ("Connection refused", "Error running Podman container"),
        ("Permission denied", "Error running Podman container"),
    ],
)
def test_podman_exception_handling(
    exception_msg: str,
    expected_msg: str,
    podman_bin: str,
    mock_validate_podman: mock.MagicMock,
    mock_trusted_image: mock.MagicMock,
    mock_podman_client: mock.MagicMock,
):
    """Test exception handling in the run_podman_container function."""
    mock_podman_client.containers.create.side_effect = Exception(exception_msg)

    with pytest.raises(RuntimeError, match=expected_msg) as excinfo:
        run_podman_container(
            image="alpine:latest", command=["echo", "This should fail"]
        )

    assert exception_msg in str(excinfo.value)


@pytest.mark.parametrize(
    "image",
    ["build-something:latest", "myrepo/dockerfile-image:1.0", "dockerfile:latest"],
)
def test_dockerfile_image_rejection(
    image: str,
    podman_bin: str,
    mock_validate_podman: mock.MagicMock,
    mock_trusted_image: mock.MagicMock,
):
    """Test that image names containing 'dockerfile' or starting with 'build' are rejected."""
    logger.info("Testing dockerfile/build image rejection", image=image)

    with pytest.raises(ValueError, match="Building images with Podman is not allowed"):
        run_podman_container(image=image, command=["echo", "This should not run"])

    logger.debug("Dockerfile image rejection test completed successfully")


@pytest.fixture
def mock_container() -> mock.MagicMock:
    """Fixture for a mock container with default successful execution state."""
    container = mock.MagicMock()
    container.logs.return_value = b"test output"
    container.inspect.return_value = {"State": {"ExitCode": 0, "Status": "exited"}}
    return container


@pytest.fixture
def mock_podman_setup(
    mock_podman_client: mock.MagicMock, mock_container: mock.MagicMock
) -> mock.MagicMock:
    """Fixture for setting up a mock Podman client with a container."""
    client_mock = mock_podman_client.return_value.__enter__.return_value
    client_mock.containers.create.return_value = mock_container
    return client_mock


@pytest.mark.parametrize(
    "volume_config",
    [
        {
            "/host/path1": {"bind": "/container/path1", "mode": "ro"},
            "/host/path2": "/container/path2",
        },
        {"/single/path": {"bind": "/container/path", "mode": "rw"}},
        {"/str/path": "/container/str/path"},
    ],
)
def test_volume_configurations(
    volume_config: dict[str, dict[str, str] | str],
    podman_bin: str,
    mock_validate_podman: mock.MagicMock,
    mock_trusted_image: mock.MagicMock,
    mock_podman_setup: mock.MagicMock,
    mock_container: mock.MagicMock,
):
    """Test various volume mounting configurations."""
    result = run_podman_container(
        image="alpine:latest", command=["ls", "-la"], volumes=volume_config
    )

    create_args = mock_podman_setup.containers.create.call_args[1]

    for host_path, container_config in volume_config.items():
        assert host_path in create_args["volumes"]
        if isinstance(container_config, dict):
            assert create_args["volumes"][host_path]["bind"] == container_config["bind"]
            assert create_args["volumes"][host_path]["mode"] == container_config["mode"]
        else:
            assert create_args["volumes"][host_path]["bind"] == container_config
            assert create_args["volumes"][host_path]["mode"] == "rw"

    assert result.success


@pytest.mark.parametrize(
    "podman_version,should_raise",
    [("podman version 4.3.1", False), ("podman version 3.0.0", False), ("", True)],
)
def test_validate_podman_installation(
    podman_version: str, should_raise: bool, tmp_path: Path
):
    """Test the validate_podman_installation function with different versions."""
    podman_path = tmp_path / "podman"
    podman_path.touch()

    with mock.patch("subprocess.run") as mock_run:
        mock_result = mock.MagicMock()
        mock_result.returncode = 0 if podman_version else 1
        mock_result.stdout = podman_version
        mock_result.stderr = "" if podman_version else "Command failed"
        mock_run.return_value = mock_result

        if should_raise:
            with pytest.raises(RuntimeError):
                validate_podman_installation(str(podman_path))
        else:
            validate_podman_installation(str(podman_path))
            mock_run.assert_called_once_with(
                [str(podman_path), "version"],
                capture_output=True,
                text=True,
                check=False,
            )
