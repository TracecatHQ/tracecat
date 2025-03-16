import os
import subprocess

import pytest

from tracecat.ee.sandbox.podman import (
    PodmanNetwork,
    PullPolicy,
    get_podman_version,
    list_podman_volumes,
    remove_podman_volumes,
    run_podman_container,
)

TEST_PODMAN_URI = os.environ.get("TEST_PODMAN_URI", "tcp://localhost:8080")
TEST_TRUSTED_IMAGES = [
    "alpine:latest",
    "ghcr.io/datadog/stratus-red-team:latest",
]


def is_container_running(container_name):
    """Check if a Docker container is running."""
    try:
        # Run docker ps command to check if container is running
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"name={container_name}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        # If the container name is in the output, it's running
        return container_name in result.stdout
    except Exception:
        return False


# Skip all tests if Podman container runner is not running
skipif_no_container_runner = pytest.mark.skipif(
    not is_container_running("container-runner"),
    reason="No container-runner Docker container found",
)


@skipif_no_container_runner
def test_podman_version():
    """Smoke test to verify Podman API connectivity."""
    version = get_podman_version(base_url=TEST_PODMAN_URI)
    assert version == "5.4.0"


@skipif_no_container_runner
def test_hello_world():
    """Test basic container operation with Alpine Linux."""
    result = run_podman_container(
        image="alpine:latest",
        command=["/bin/sh", "-c", "echo Hello, World!"],
        network=PodmanNetwork.NONE,
        pull_policy=PullPolicy.MISSING,
        base_url=TEST_PODMAN_URI,
        trusted_images=TEST_TRUSTED_IMAGES,
    )

    assert result.exit_code == 0
    assert any("Hello, World!" in line for line in result.stdout)


@skipif_no_container_runner
def test_environment_variables_are_set():
    """Test that environment variables are set."""
    result = run_podman_container(
        image="alpine:latest",
        command=["/bin/sh", "-c", "echo $ENV_VAR $ENV_VAR2 $ENV_VAR3"],
        environment={"ENV_VAR": "hello!", "ENV_VAR2": "world!", "ENV_VAR3": "foo!"},
        base_url=TEST_PODMAN_URI,
        trusted_images=TEST_TRUSTED_IMAGES,
    )
    assert result.exit_code == 0
    assert result.stdout[0].startswith("hello! world! foo!")


@skipif_no_container_runner
def test_stratus_red_team_list():
    """Test running list command with Stratus Red Team image."""
    result = run_podman_container(
        image="ghcr.io/datadog/stratus-red-team:latest",
        command="list",
        network=PodmanNetwork.NONE,
        pull_policy=PullPolicy.MISSING,
        base_url=TEST_PODMAN_URI,
        trusted_images=TEST_TRUSTED_IMAGES,
    )

    assert result.exit_code == 0
    assert any(
        "View the list of all available attack techniques" in line
        for line in result.stdout
    )


@skipif_no_container_runner
def test_volumes_persist_across_runs():
    """Test that volumes persist across container runs."""
    volume_name = "test-persist-volume"
    volume_path = "/data"
    test_file = "test.txt"
    test_content = "Hello from container 1"

    # First container: Write data to volume
    first_run = run_podman_container(
        image="docker.io/library/alpine:latest",
        command=["/bin/sh", "-c", f"echo '{test_content}' > {volume_path}/{test_file}"],
        volume_name=volume_name,
        volume_path=volume_path,
        base_url=TEST_PODMAN_URI,
        trusted_images=["docker.io/library/alpine:latest"],
    )
    assert first_run.exit_code == 0

    # Verify the volume was created
    volumes = list_podman_volumes(base_url=TEST_PODMAN_URI)
    assert volume_name in volumes, (
        f"Volume {volume_name} was not created. Found volumes: {volumes}"
    )

    # Second container: Read data from volume
    second_run = run_podman_container(
        image="docker.io/library/alpine:latest",
        command=["/bin/sh", "-c", f"cat {volume_path}/{test_file}"],
        volume_name=volume_name,
        volume_path=volume_path,
        base_url=TEST_PODMAN_URI,
        trusted_images=["docker.io/library/alpine:latest"],
    )
    assert second_run.exit_code == 0
    # Verify the content persisted
    assert test_content in second_run.stdout[0], "Content did not persist in volume"

    # Remove the volume
    remove_podman_volumes(volume_name=volume_name, base_url=TEST_PODMAN_URI)

    # Verify the volume is removed
    volumes = list_podman_volumes(base_url=TEST_PODMAN_URI)
    assert volume_name not in volumes, (
        f"Volume {volume_name} was not removed. Found volumes: {volumes}"
    )


@skipif_no_container_runner
def test_untrusted_image():
    """Test that non-allowlisted images are rejected."""
    with pytest.raises(ValueError):
        run_podman_container(
            image="docker.io/library/nginx:latest",
            command=["nginx", "-v"],
            network=PodmanNetwork.NONE,
            base_url=TEST_PODMAN_URI,
            trusted_images=TEST_TRUSTED_IMAGES,
        )


@skipif_no_container_runner
def test_http_request_with_bridge_network():
    """Test that HTTP request to google.com works with bridge network."""
    script = """
import sys
import urllib.request
try:
    with urllib.request.urlopen('http://google.com', timeout=5) as response:
        print(response.status)
    sys.exit(0)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
"""
    result = run_podman_container(
        image="docker.io/library/python:3.11-slim",
        command=["python", "-c", script],
        network=PodmanNetwork.HOST,
        pull_policy=PullPolicy.MISSING,
        base_url=TEST_PODMAN_URI,
        trusted_images=["docker.io/library/python:3.11-slim"],
    )
    assert result.exit_code == 0
    assert "200" in " ".join(result.stdout)


@skipif_no_container_runner
def test_http_request_with_none_network():
    """Test that HTTP request to google.com fails with no network."""
    script = """
import sys
import urllib.request
try:
    with urllib.request.urlopen('http://google.com', timeout=5) as response:
        print(response.status)
    sys.exit(0)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(7)  # Use exit code 7 to match curl's connection error code
"""
    # This should raise a RuntimeError
    with pytest.raises(RuntimeError) as excinfo:
        run_podman_container(
            image="docker.io/library/python:3.11-slim",
            command=["python", "-c", script],
            network=PodmanNetwork.NONE,
            pull_policy=PullPolicy.MISSING,
            base_url=TEST_PODMAN_URI,
            trusted_images=["docker.io/library/python:3.11-slim"],
        )

    # Check that the error message contains the expected information
    error_message = str(excinfo.value)
    assert "exited with code 7" in error_message
    assert "Error:" in error_message


@skipif_no_container_runner
def test_expected_exit_codes():
    """Test that expected exit codes don't raise errors."""
    # Test a command that exits with code 1
    result = run_podman_container(
        image="alpine:latest",
        command=["/bin/sh", "-c", "exit 1"],
        expected_exit_codes=[1],
        base_url=TEST_PODMAN_URI,
        trusted_images=TEST_TRUSTED_IMAGES,
    )
    assert result.exit_code == 1

    # Test that other exit codes still raise errors
    with pytest.raises(RuntimeError) as excinfo:
        run_podman_container(
            image="alpine:latest",
            command=["/bin/sh", "-c", "exit 2"],
            expected_exit_codes=[1],  # only expect exit code 1
            base_url=TEST_PODMAN_URI,
            trusted_images=TEST_TRUSTED_IMAGES,
        )
    assert "exited with code 2" in str(excinfo.value)

    # Test multiple expected exit codes
    result = run_podman_container(
        image="alpine:latest",
        command=["/bin/sh", "-c", "exit 3"],
        expected_exit_codes=[1, 2, 3],
        base_url=TEST_PODMAN_URI,
        trusted_images=TEST_TRUSTED_IMAGES,
    )
    assert result.exit_code == 3
