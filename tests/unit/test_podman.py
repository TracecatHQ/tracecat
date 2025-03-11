"""Test the container runner live."""

import pytest

from tracecat.sandbox.podman import (
    PodmanNetwork,
    PullPolicy,
    get_podman_version,
    run_podman_container,
)

TEST_PODMAN_URI = "tcp://localhost:8080"
TEST_TRUSTED_IMAGES = ["alpine:latest", "datadog/stratus-red-team:latest"]


@pytest.fixture(autouse=True)
def podman_config(monkeypatch):
    """Configure Podman for tests."""

    from tracecat import config

    monkeypatch.setattr(
        config, "TRACECAT__TRUSTED_DOCKER_IMAGES", ",".join(TEST_TRUSTED_IMAGES)
    )


def test_podman_version():
    """Smoke test to verify Podman API connectivity."""
    version = get_podman_version(base_url=TEST_PODMAN_URI)
    assert version == "5.4.0"


def test_hello_world():
    """Test basic container operation with Alpine Linux."""
    result = run_podman_container(
        image="alpine:latest",
        command=["echo", "hello world"],
        network=PodmanNetwork.NONE,
        pull_policy=PullPolicy.MISSING,
        base_url=TEST_PODMAN_URI,  # Pass base_url explicitly
    )

    assert result.success
    assert "hello world" in result.output
    assert result.exit_code == 0
    assert result.status == "exited"


def test_stratus_red_team_list():
    """Test running list command with Stratus Red Team image."""
    result = run_podman_container(
        image="datadog/stratus-red-team:latest",
        command=["stratus", "list"],
        network=PodmanNetwork.NONE,
        pull_policy=PullPolicy.MISSING,
        base_url=TEST_PODMAN_URI,  # Pass base_url explicitly
    )

    assert result.success
    assert "Available attack techniques:" in result.output
    assert result.exit_code == 0
    assert result.status == "exited"


def test_untrusted_image():
    """Test that non-allowlisted images are rejected."""
    result = run_podman_container(
        image="nginx:latest",
        command=["nginx", "-v"],
        network=PodmanNetwork.NONE,
        base_url=TEST_PODMAN_URI,
    )

    assert not result.success
    assert "Image not in trusted list" in result.output
    assert result.exit_code == 1
    assert result.status == "failed"
