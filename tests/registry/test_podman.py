import os
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from unittest import mock

import pytest
from loguru import logger

from registry.tracecat_registry.experimental.podman import (
    TRACECAT__PODMAN_URI,
    PodmanResult,
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


# === Fixtures === #
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


@pytest.fixture
def mock_validate_podman():
    """Mock the validate_podman_installation function to avoid actual validation."""
    with mock.patch(
        "registry.tracecat_registry.experimental.podman.validate_podman_installation"
    ) as mock_validate:
        # Make the validation function do nothing
        mock_validate.return_value = None
        yield mock_validate


@pytest.fixture
def mock_trusted_image():
    """Mock the is_trusted_image function to always return True for testing."""
    with mock.patch(
        "registry.tracecat_registry.experimental.podman.is_trusted_image"
    ) as mock_trust:
        # Always consider images trusted for testing
        mock_trust.return_value = True
        yield mock_trust


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


@contextmanager
def temp_env_vars(env_updates: dict[str, str | None]) -> Generator[None, None, None]:
    """Temporarily set environment variables and restore them after."""
    original_values = {key: os.environ.get(key) for key in env_updates}

    for key, value in env_updates.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    try:
        yield
    finally:
        for key, value in original_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def set_podman_env(podman_bin):
    """Set the Podman environment variables for testing."""
    env_updates = {
        "TRACECAT__PODMAN_BINARY_PATH": podman_bin,
        "TRACECAT__TRUSTED_DOCKER_IMAGES": (
            "alpine:latest,python:3.9-slim,ghcr.io/datadog/stratus-red-team:latest"
        ),
        "TRACECAT__PODMAN_URI": "unix:///tmp/podman.sock",
    }

    logger.debug(
        "Setting up environment variables for testing",
        podman_path=podman_bin,
        podman_uri="unix:///tmp/podman.sock",
    )

    with temp_env_vars(env_updates):
        yield

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

    # Set up the images collection with proper mocking
    mock_images = mock.MagicMock()
    mock_images.exists.return_value = False
    mock_images.pull.return_value = None
    client_mock.images = mock_images

    return client_mock


@pytest.fixture
def security_test_params() -> dict[str, list[str] | str]:
    """Fixture providing security test parameters."""
    return {
        "security_opts": ["seccomp=unconfined"],
        "cap_drop": ["NET_ADMIN"],
        "cap_add": ["SYS_ADMIN"],
        "network": "host",  # network is a string, not a list
    }


# === Tests === #


@dataclass
class ContainerAssertions:
    """Expected container state for testing."""

    container_id: str = "test-container-id"
    status: str = "exited"
    expected_output: str | None = None
    exit_code: int = 0


def assert_container_success(
    result: PodmanResult, expected: ContainerAssertions = ContainerAssertions()
) -> None:
    """
    Assert container executed successfully with expected state.

    Args:
        result: The container execution result
        expected: Expected container state and output

    Raises:
        AssertionError: If container state doesn't match expectations
    """
    assert result.success, f"Container failed with exit code {result.exit_code}"
    assert result.container_id == expected.container_id
    assert result.status == expected.status
    if expected.expected_output:
        assert expected.expected_output in result.output


def assert_container_failure(
    result: PodmanResult, expected: ContainerAssertions
) -> None:
    """
    Assert container failed with expected state.

    Args:
        result: The container execution result
        expected: Expected container state and output

    Raises:
        AssertionError: If container state doesn't match expectations
    """
    assert not result.success, "Container succeeded when failure was expected"
    assert result.container_id == expected.container_id
    assert result.status == expected.status
    assert result.exit_code == expected.exit_code
    if expected.expected_output:
        assert expected.expected_output in result.output


def test_echo_hello_world(
    podman_bin,
    mock_validate_podman,
    mock_trusted_image,
    mock_podman_client,
    cleanup_containers,
):
    """Test running a simple echo command in a container."""
    # Configure the container logs mock
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value
    mock_container.logs.return_value = b"hello world"
    mock_container.inspect.return_value = {"State": {"ExitCode": 0, "Status": "exited"}}

    result = run_podman_container(
        image="alpine:latest", command=["echo", "hello world"]
    )

    # Verify container success with expected output
    assert_container_success(result, ContainerAssertions(expected_output="hello world"))

    # Verify specific test requirements
    mock_podman_client.assert_called_once_with(base_url=TRACECAT__PODMAN_URI)
    create_args = client_mock.containers.create.call_args[1]
    assert create_args["image"] == "alpine:latest"
    assert create_args["command"] == ["echo", "hello world"]
    assert create_args["network_mode"] == "none"

    # Register container for cleanup
    cleanup_containers(result.container_id)
    logger.debug("Echo container test completed successfully")


@pytest.mark.integration
def test_run_stratus_red_team_list_live(
    podman_bin,
    cleanup_containers,
):
    """Integration test running the actual stratus-red-team list command."""
    logger.info(
        "Testing live stratus-red-team list container",
        image="ghcr.io/datadog/stratus-red-team:latest",
    )

    result = run_podman_container(
        image="ghcr.io/datadog/stratus-red-team:latest",
        command=["list"],
        security_opts=[],  # Override default security options that cause issues
    )

    # Basic success checks
    assert result.success, f"Container failed with: {result.output}"
    assert result.container_id is not None

    # Verify expected output structure
    output_lines = result.output.splitlines()
    # Find header line
    header = next(line for line in output_lines if "ID" in line and "TACTIC" in line)

    # Verify header structure
    assert all(col in header for col in ["ID", "TACTIC", "TECHNIQUE", "PLATFORM"])

    # Verify we have at least one attack technique listed
    techniques = [line for line in output_lines if "aws." in line]
    assert len(techniques) > 0, "No attack techniques found in output"

    # Register for cleanup
    cleanup_containers(result.container_id)

    logger.info(
        "Live stratus-red-team list successful",
        container_id=result.container_id,
        technique_count=len(techniques),
    )


@pytest.mark.integration
def test_external_network_call_live(
    podman_bin,
    cleanup_containers,
):
    """Integration test making real HTTP calls from a Podman container."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as temp_file:
        temp_file.write("""
import requests
import sys
import json

try:
    response = requests.get('https://httpbin.org/get', timeout=5)
    print(response.status_code)
    print(json.dumps(response.json(), indent=2))
    sys.exit(0)
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
        """)
        script_path = temp_file.name

    try:
        # Install requests and run the test in the same container
        result = run_podman_container(
            image="python:3.9-slim",
            command=["sh", "-c", "pip install requests && python /script.py"],
            volumes={script_path: {"bind": "/script.py", "mode": "ro"}},
            env_vars={"PYTHONUNBUFFERED": "1"},  # Ensure Python output isn't buffered
            security_opts=[],  # Override default security options that cause issues
            network="bridge",  # Allow network access for package installation and test
        )

        # Basic success checks
        assert result.success, f"Container failed with: {result.output}"
        assert "200" in result.output, (
            f"Expected HTTP 200 status code, got: {result.output}"
        )
    finally:
        # Clean up the temporary file
        os.unlink(script_path)


@pytest.mark.integration
def test_network_timeout_live(
    podman_bin,
    cleanup_containers,
):
    """Test handling of network timeouts with real containers."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as temp_file:
        temp_file.write("""
import requests
import sys

try:
    # Very short timeout to force failure
    response = requests.get('https://httpbin.org/delay/5', timeout=0.001)
    print(response.status_code)
    sys.exit(0)
except requests.Timeout as e:
    print(f"Timeout error: {e}", file=sys.stderr)
    sys.exit(1)
        """)
        script_path = temp_file.name

    try:
        # Install requests and run the test in the same container
        result = run_podman_container(
            image="python:3.9-slim",
            command=["sh", "-c", "pip install requests && python /script.py"],
            volumes={script_path: {"bind": "/script.py", "mode": "ro"}},
            security_opts=[],  # Override default security options that cause issues
            network="bridge",  # Allow network access for package installation and test
        )

        assert not result.success
        assert "Timeout error" in result.output
    finally:
        # Clean up the temporary file
        os.unlink(script_path)


def test_container_failure(
    podman_bin, mock_validate_podman, mock_trusted_image, mock_podman_client
):
    """Test container execution failure handling."""
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value
    mock_container.logs.return_value = b"command not found: invalid_command"
    mock_container.inspect.return_value = {
        "State": {
            "ExitCode": 127,
            "Status": "error",
        }
    }

    result = run_podman_container(image="alpine:latest", command=["invalid_command"])

    # Use custom assertions for failure case
    assert_container_failure(
        result,
        ContainerAssertions(
            status="error", expected_output="command not found", exit_code=127
        ),
    )


def test_string_command_and_env_vars(
    podman_bin, mock_validate_podman, mock_trusted_image, mock_podman_client
):
    """Test string command conversion and environment variables."""
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value
    mock_container.logs.return_value = b"HELLO=WORLD"

    # Set the container status
    mock_container.inspect.return_value = {"State": {"ExitCode": 0, "Status": "exited"}}

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


def test_container_null_id(
    podman_bin, mock_validate_podman, mock_trusted_image, mock_podman_client
):
    """Test handling of null container ID."""
    client_mock = mock_podman_client.return_value.__enter__.return_value
    mock_container = client_mock.containers.create.return_value

    # Set the container ID to None to test that branch
    mock_container.id = None
    mock_container.logs.return_value = b"test output"

    result = run_podman_container(image="alpine:latest", command=["echo", "test"])

    # Verify the result handles None container ID
    assert result.container_id is None
    assert result.status == "error"

    # Should still have output but exit code would be set to 1
    assert "test output" in result.output
    assert result.exit_code == 1


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

        # Should raise RuntimeError
        with pytest.raises(RuntimeError):
            validate_podman_installation("/path/to/podman")

        # Verify the command was called
        mock_run.assert_called_once()


def test_untrusted_image_handling(podman_bin, mock_validate_podman, mock_podman_client):
    """Test that untrusted images are properly rejected."""
    # Configure the mock to specifically reject this image
    with mock.patch(
        "registry.tracecat_registry.experimental.podman.is_trusted_image"
    ) as mock_trust:
        mock_trust.return_value = False

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
    # Configure the client to raise an exception during container creation
    client_mock = mock_podman_client.return_value.__enter__.return_value
    client_mock.containers.create.side_effect = Exception(exception_msg)

    with pytest.raises(RuntimeError) as excinfo:
        run_podman_container(
            image="alpine:latest", command=["echo", "This should fail"]
        )

    assert expected_msg in str(excinfo.value)
    assert exception_msg in str(excinfo.value)
