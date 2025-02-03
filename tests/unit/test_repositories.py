import pytest

from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.types.exceptions import TracecatValidationError


@pytest.mark.parametrize(
    "url",
    [
        "git+ssh://git@github.com/user/repo.git",
        "git+ssh://git@gitlab.company.com/team/project.git",
        "git+ssh://git@example.com/org/repo.git",
    ],
)
def test_registry_repository_create_valid_urls(url: str) -> None:
    """Test that valid git SSH URLs are accepted.

    Args:
        url: A valid git SSH URL to test
    """
    repo = RegistryRepositoryCreate(origin=url)
    assert repo.origin == url


@pytest.mark.parametrize(
    "url",
    [
        # Shell injection attempts
        "git+ssh://git@github.com/user/repo.git;ls",
        "git+ssh://git@github.com/user/repo.git&&whoami",
        "git+ssh://git@github.com/user/repo.git|cat /etc/passwd",
        "git+ssh://git@github.com/user/repo.git`rm -rf /`",
        "git+ssh://git@github.com/user/repo.git$(echo pwned)",
        "git+ssh://git@github.com/user/repo.git > /tmp/hack",
        # Invalid characters
        "git+ssh://git@github.com/user/repo.git#branch;command",
        "git+ssh://git@github.com/user/repo.git#branch|pipe",
        # Path traversal attempts
        "git+ssh://git@github.com/../user/repo.git",
        "git+ssh://git@github.com/user/../../repo.git",
        # Empty or malformed components
        "git+ssh://git@/user/repo.git",
        "git+ssh://git@github.com//repo.git",
        "git+ssh://git@github.com/user/.git",
        # Invalid port
        "git+ssh://git@github.com:2222/user/repo.git",
        # Invalid host
        "git+ssh://git@test.com:22/user/repo; rm -rf; echo pwned.git",
    ],
)
def test_registry_repository_create_invalid_urls(url: str) -> None:
    """Test that invalid and potentially unsafe git SSH URLs are rejected.

    Args:
        url: An invalid git SSH URL that should be rejected
    """
    with pytest.raises(TracecatValidationError):
        RegistryRepositoryCreate(origin=url)
