import pytest

from tracecat.exceptions import TracecatValidationError
from tracecat.git.constants import GIT_SSH_URL_REGEX
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate


@pytest.mark.parametrize(
    "url",
    [
        "git+ssh://git@github.com/user/repo.git",
        "git+ssh://git@gitlab.company.com/team/project.git",
        "git+ssh://git@example.com/org/repo.git",
        "git+ssh://someuser@git.example.com/org/repo.git",
        "git+ssh://git@github.com:2222/user/repo.git",  # With port
        "git+ssh://git@gitlab.com/org/team/subteam/repo.git",  # Nested groups
        "git+ssh://someuser@git.example.com/very/deep/nested/org/repo.git",
        "git+ssh://git@github.com/user/repo",  # Without .git suffix
        "git+ssh://git@github.com/user/repo.git@main",  # With branch ref
        "git+ssh://git@github.com/user/repo.git@feature/custom-branch",  # Slash ref
    ],
)
def test_registry_repository_create_valid_urls(url: str) -> None:
    """Valid git SSH URLs match the shared regex and are accepted."""
    assert GIT_SSH_URL_REGEX.match(url)
    repo = RegistryRepositoryCreate(origin=url)
    assert repo.origin == url


@pytest.mark.parametrize(
    "url",
    [
        "git+ssh://git@/user/repo.git",  # No host
        "https://github.com/user/repo.git",
        "ssh://git@github.com/user/repo.git",
        "git://git@github.com/org/repo.git",  # Missing +ssh
        "not-a-url",
        "",
        "git+ssh://github.com/user/repo.git",  # Missing SSH user
        "git+ssh://git@github.com",  # No path
        "git+ssh://git@github.com/",  # Empty path
        "git+ssh://git@github.com:not_a_port/org/repo.git",  # Non numeric port
        "git+ssh://git@github.com:/org/repo.git",  # Missing port after colon
        "git+ssh://git@github.com/repo.git",  # Missing org segment
    ],
)
def test_registry_repository_create_invalid_urls(url: str) -> None:
    """Invalid git SSH URLs fail the shared regex and are rejected."""
    assert not GIT_SSH_URL_REGEX.match(url)
    with pytest.raises(TracecatValidationError):
        RegistryRepositoryCreate(origin=url)
