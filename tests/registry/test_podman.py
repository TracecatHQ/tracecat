import os
import subprocess
import tempfile
from unittest import mock

import pytest
from loguru import logger

from registry.tracecat_registry.experimental.podman import (
    TRACECAT__PODMAN_URI,
    PodmanResult,
    is_trusted_image,
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
    with mock.patch("podman.PodmanClient") as mock_client_class:
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

        # Setup mock client instance
        mock_client = mock.MagicMock()
        mock_client.containers = mock_containers
        mock_client.images = mock_images

        # Setup mock client class
        mock_client_class.return_value = mock_client

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
    mock_client = mock_podman_client.return_value
    mock_container = mock_client.containers.create.return_value
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
    mock_client.containers.create.assert_called_once()
    create_args = mock_client.containers.create.call_args[1]
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

    # Register container for cleanup (even though it's mocked in this test)
    cleanup_containers(result.container_id)
    logger.debug("Echo container test completed successfully")


def test_run_stratus_red_team_list(
    podman_bin,
    mock_validate_podman,
    mock_trusted_image,
    mock_podman_client,
    cleanup_containers,
):
    """Test running the stratus-red-team list command."""
    # Configure the container logs mock to return stratus-red-team output
    mock_client = mock_podman_client.return_value
    mock_container = mock_client.containers.create.return_value
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
    mock_client.containers.create.assert_called_once()
    create_args = mock_client.containers.create.call_args[1]
    assert create_args["image"] == "ghcr.io/datadog/stratus-red-team:latest"
    assert create_args["command"] == ["list"]

    # Check the result
    assert result.success
    assert "ID" in result.output
    assert "TACTIC" in result.output
    assert "TECHNIQUE" in result.output
    assert result.exit_code == 0
    assert result.container_id == "test-container-id"

    # Register container for cleanup (even though it's mocked in this test)
    cleanup_containers(result.container_id)
    logger.debug("Stratus red team container test completed successfully")


def test_external_network_call(
    podman_bin,
    mock_validate_podman,
    mock_trusted_image,
    mock_podman_client,
    cleanup_containers,
):
    """Test making a secure external network call."""
    # Configure the container logs mock to return HTTP response
    mock_client = mock_podman_client.return_value
    mock_container = mock_client.containers.create.return_value
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
        mock_client.containers.create.assert_called_once()
        create_args = mock_client.containers.create.call_args[1]
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
    mock_client = mock_podman_client.return_value
    mock_container = mock_client.containers.create.return_value
    mock_container.logs.return_value = b"command not found: invalid_command"

    # Set the container status to have a non-zero exit code
    mock_container.inspect.return_value = {
        "State": {
            "ExitCode": 127,  # Command not found exit code
            "Status": "exited",
        }
    }

    logger.info("Testing container failure", image="alpine:latest")
    result = run_podman_container(image="alpine:latest", command=["invalid_command"])

    # Check the result
    assert not result.success
    assert "command not found" in result.output
    assert result.exit_code == 127
    assert result.container_id == "test-container-id"

    logger.debug("Container failure test completed successfully")


def test_string_command_and_env_vars(
    podman_bin, mock_validate_podman, mock_trusted_image, mock_podman_client
):
    """Test string command conversion and environment variables."""
    mock_client = mock_podman_client.return_value
    mock_container = mock_client.containers.create.return_value
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
    mock_client.containers.create.assert_called_once()
    create_args = mock_client.containers.create.call_args[1]

    # Verify string command was converted to list
    assert create_args["command"] == ["echo $HELLO"]

    # Verify environment variables were passed correctly
    assert create_args["environment"] == {"HELLO": "WORLD"}

    # Check the result
    assert result.success
    assert "HELLO=WORLD" in result.output
    assert result.exit_code == 0

    logger.debug("String command and environment variables test completed successfully")


def test_container_null_id(
    podman_bin, mock_validate_podman, mock_trusted_image, mock_podman_client
):
    """Test handling of null container ID."""
    mock_client = mock_podman_client.return_value
    mock_container = mock_client.containers.create.return_value

    # Set the container ID to None to test that branch
    mock_container.id = None
    mock_container.logs.return_value = b"test output"

    logger.info("Testing null container ID handling", image="alpine:latest")
    result = run_podman_container(image="alpine:latest", command=["echo", "test"])

    # Verify the result handles None container ID
    assert result.container_id is None

    # Should still have output but exit code would be set to 1
    assert "test output" in result.output

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


def test_nonexistent_podman_binary():
    """Test validate_podman_installation with a nonexistent binary path."""
    logger.info("Testing podman installation validation with nonexistent binary")
    with mock.patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(FileNotFoundError):
            validate_podman_installation("/nonexistent/path/to/podman")
    logger.debug("Nonexistent podman binary test completed successfully")


def test_is_trusted_image():
    """Test the is_trusted_image function with various inputs."""
    # Save original environment variable
    original_trusted_images = os.environ.get("TRACECAT__TRUSTED_DOCKER_IMAGES")

    try:
        # Test with a list of trusted images
        os.environ["TRACECAT__TRUSTED_DOCKER_IMAGES"] = (
            "alpine:latest,python:3.9-slim,nginx:1.21"
        )

        # Test trusted images
        assert is_trusted_image("alpine:latest") is True
        assert is_trusted_image("python:3.9-slim") is True
        assert is_trusted_image("nginx:1.21") is True

        # Test untrusted images
        assert is_trusted_image("malicious:latest") is False
        assert is_trusted_image("unknown:1.0") is False

        # Test with empty string (should allow all images - used for testing)
        os.environ["TRACECAT__TRUSTED_DOCKER_IMAGES"] = ""
        assert is_trusted_image("any-image:latest") is True

        # Test with a single image
        os.environ["TRACECAT__TRUSTED_DOCKER_IMAGES"] = "only-this:latest"
        assert is_trusted_image("only-this:latest") is True
        assert is_trusted_image("not-this:latest") is False

    finally:
        # Restore original environment variable
        if original_trusted_images is not None:
            os.environ["TRACECAT__TRUSTED_DOCKER_IMAGES"] = original_trusted_images
        else:
            os.environ.pop("TRACECAT__TRUSTED_DOCKER_IMAGES", None)


def test_podman_result_class():
    """Test the PodmanResult class functionality."""
    # Test successful result
    success_result = PodmanResult(
        output="Command executed successfully",
        exit_code=0,
        container_id="container123",
        command=["podman", "run", "alpine", "echo", "hello"],
        status="exited",
    )

    assert success_result.success is True
    assert success_result.output == "Command executed successfully"
    assert success_result.exit_code == 0
    assert success_result.container_id == "container123"
    assert len(success_result.command) == 5
    assert success_result.status == "exited"

    # Test failed result
    failed_result = PodmanResult(
        output="Command failed with error",
        exit_code=1,
        container_id="container456",
        command=["podman", "run", "alpine", "invalid_cmd"],
        status="error",
    )

    assert failed_result.success is False
    assert failed_result.output == "Command failed with error"
    assert failed_result.exit_code == 1
    assert failed_result.container_id == "container456"
    assert len(failed_result.command) == 4
    assert failed_result.status == "error"

    # Test with minimal arguments
    minimal_result = PodmanResult(output="", exit_code=0)

    assert minimal_result.success is True
    assert minimal_result.container_id is None
    assert minimal_result.command == []
    assert minimal_result.status is None
