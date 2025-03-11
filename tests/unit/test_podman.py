"""Test the container runner live."""

from tracecat.sandbox.podman import (
    PodmanNetwork,
    PullPolicy,
    run_podman_container,
)


def test_hello_world():
    """Test basic container operation with Alpine Linux."""
    result = run_podman_container(
        image="alpine:latest",
        command=["echo", "hello world"],
        network=PodmanNetwork.NONE,
        pull_policy=PullPolicy.MISSING,
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
    )

    assert result.success
    assert "Available attack techniques:" in result.output
    assert result.exit_code == 0
    assert result.status == "exited"


def test_untrusted_image():
    """Test that non-allowlisted images are rejected."""
    result = run_podman_container(
        image="nginx:latest", command=["nginx", "-v"], network=PodmanNetwork.NONE
    )

    assert not result.success
    assert "Image not in trusted list" in result.output
    assert result.exit_code == 1
    assert result.status == "failed"
