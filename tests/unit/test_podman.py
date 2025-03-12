import pytest

from tracecat.sandbox.podman import (
    PodmanNetwork,
    PullPolicy,
    get_podman_version,
    list_podman_volumes,
    remove_podman_volumes,
    run_podman_container,
)

TEST_PODMAN_URI = "tcp://localhost:8080"
TEST_TRUSTED_IMAGES = [
    "alpine:latest",
    "ghcr.io/datadog/stratus-red-team:latest",
]


def test_podman_version():
    """Smoke test to verify Podman API connectivity."""
    version = get_podman_version(base_url=TEST_PODMAN_URI)
    assert version == "5.4.0"


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

    assert result.success
    assert result.exit_code == 0
    assert result.status == "exited"
    assert any("Hello, World!" in log for log in result.logs)


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

    assert result.success
    assert result.exit_code == 0
    assert result.status == "exited"
    assert any(
        "View the list of all available attack techniques" in log for log in result.logs
    )


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
    assert first_run.success, f"First container failed: {first_run.logs}"

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

    # Verify the content persisted
    assert second_run.success, f"Second container failed: {second_run.logs}"
    assert test_content in second_run.logs[0], "Content did not persist in volume"

    # Remove the volume
    remove_podman_volumes(volume_name=volume_name, base_url=TEST_PODMAN_URI)

    # Verify the volume is removed
    volumes = list_podman_volumes(base_url=TEST_PODMAN_URI)
    assert volume_name not in volumes, (
        f"Volume {volume_name} was not removed. Found volumes: {volumes}"
    )


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
