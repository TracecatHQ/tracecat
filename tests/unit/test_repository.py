import pytest

from tracecat.registry.repository import GitUrl, parse_git_url


@pytest.mark.parametrize(
    "url, expected",
    [
        # GitHub (no branch)
        (
            "git+ssh://git@github.com/tracecat-dev/tracecat-registry.git",
            GitUrl(
                host="github.com",
                org="tracecat-dev",
                repo="tracecat-registry",
                branch="main",
            ),
        ),
        # GitHub (with branch)
        (
            "git+ssh://git@github.com/tracecat-dev/tracecat-registry.git@main",
            GitUrl(
                host="github.com",
                org="tracecat-dev",
                repo="tracecat-registry",
                branch="main",
            ),
        ),
        # GitHub (with semantic version)
        (
            "git+ssh://git@github.com/tracecat-dev/tracecat-registry.git@v1.0.0",
            GitUrl(
                host="github.com",
                org="tracecat-dev",
                repo="tracecat-registry",
                branch="v1.0.0",
            ),
        ),
        # GitLab
        (
            "git+ssh://git@gitlab.com/tracecat-dev/tracecat-registry.git",
            GitUrl(
                host="gitlab.com",
                org="tracecat-dev",
                repo="tracecat-registry",
                branch="main",
            ),
        ),
        # Bitbucket
        (
            "git+ssh://git@bitbucket.org/tracecat-dev/tracecat-registry.git@main",
            GitUrl(
                host="bitbucket.org",
                org="tracecat-dev",
                repo="tracecat-registry",
                branch="main",
            ),
        ),
    ],
)
def test_parse_git_url(url: str, expected: GitUrl):
    assert parse_git_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "git+ssh://git@tracecat.com/tracecat-dev/tracecat-registry.git@v1.0.0",
        "git+ssh://git@git.com/tracecat-dev/tracecat-registry.git@v1.0.0",
    ],
)
def test_parse_git_url_invalid(url: str):
    with pytest.raises(ValueError):
        parse_git_url(url)
