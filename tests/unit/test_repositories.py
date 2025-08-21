import pytest

from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.types.exceptions import TracecatValidationError


@pytest.mark.parametrize(
    "url",
    [
        "git+ssh://git@github.com/user/repo.git",
        "git+ssh://git@gitlab.company.com/team/project.git",
        "git+ssh://git@example.com/org/repo.git",
        "git+ssh://git@github.com:2222/user/repo.git",  # With port
        "git+ssh://git@gitlab.com/org/team/subteam/repo.git",  # Nested groups
        "git+ssh://git@github.com/user/repo",  # Without .git suffix
        "git+ssh://git@github.com/user/repo.git@main",  # With branch ref
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
        # Empty or malformed components that should still be rejected
        "git+ssh://git@/user/repo.git",  # No host
        # Non-git+ssh URLs
        "https://github.com/user/repo.git",
        "ssh://git@github.com/user/repo.git",
        # Missing git@ user
        "git+ssh://github.com/user/repo.git",
    ],
)
def test_registry_repository_create_invalid_urls(url: str) -> None:
    """Test that invalid and potentially unsafe git SSH URLs are rejected.

    Args:
        url: An invalid git SSH URL that should be rejected
    """
    with pytest.raises(TracecatValidationError):
        RegistryRepositoryCreate(origin=url)
