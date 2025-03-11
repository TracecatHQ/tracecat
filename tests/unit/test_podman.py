import pytest

from tracecat.sandbox.podman import (
    PodmanNetwork,
    PullPolicy,
    get_podman_version,
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
