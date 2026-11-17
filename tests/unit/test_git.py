"""Tests for git base functionality."""

from unittest.mock import AsyncMock, patch

import pytest

from tracecat.git.utils import (
    GitUrl,
    list_git_commits,
    parse_git_url,
    resolve_git_ref,
    run_git,
)
from tracecat.registry.repositories.schemas import GitCommitInfo
from tracecat.ssh import SshEnv


@pytest.mark.parametrize(
    ("git_url", "expected"),
    [
        pytest.param(
            GitUrl(host="github.com", org="myorg", repo="myrepo"),
            "git+ssh://git@github.com/myorg/myrepo.git",
            id="default-user",
        ),
        pytest.param(
            GitUrl(host="git.example.com", org="myorg", repo="myrepo", user="someuser"),
            "git+ssh://someuser@git.example.com/myorg/myrepo.git",
            id="custom-user",
        ),
        pytest.param(
            GitUrl(host="github.com", org="myorg", repo="myrepo", ref="main"),
            "git+ssh://git@github.com/myorg/myrepo.git@main",
            id="with-ref",
        ),
    ],
)
def test_git_url_to_url(git_url: GitUrl, expected: str) -> None:
    assert git_url.to_url() == expected


@pytest.mark.parametrize(
    ("url", "allowed_domains", "expected"),
    [
        pytest.param(
            "git+ssh://git@github.com/myorg/myrepo.git",
            None,
            ("git", "github.com", "myorg", "myrepo", None),
            id="valid",
        ),
        pytest.param(
            "git+ssh://git@github.com/myorg/myrepo.git@main",
            None,
            ("git", "github.com", "myorg", "myrepo", "main"),
            id="with-ref",
        ),
        pytest.param(
            "git+ssh://git@github.com/myorg/myrepo@main",
            None,
            ("git", "github.com", "myorg", "myrepo", "main"),
            id="without-git-suffix",
        ),
        pytest.param(
            "git+ssh://git@gitlab.example.com:2222/myorg/myrepo.git",
            None,
            ("git", "gitlab.example.com:2222", "myorg", "myrepo", None),
            id="self-hosted-port",
        ),
        pytest.param(
            "git+ssh://git@gitlab.example.com:2222/myorg/myrepo.git@develop",
            None,
            ("git", "gitlab.example.com:2222", "myorg", "myrepo", "develop"),
            id="self-hosted-port-and-ref",
        ),
        pytest.param(
            "git+ssh://git@gitlab.com/myorg/team/subteam/myrepo.git",
            None,
            ("git", "gitlab.com", "myorg/team/subteam", "myrepo", None),
            id="nested-groups",
        ),
        pytest.param(
            "git+ssh://git@gitlab.com/org/dept/team/project/subproject/repo.git@feature",
            None,
            (
                "git",
                "gitlab.com",
                "org/dept/team/project/subproject",
                "repo",
                "feature",
            ),
            id="deep-nested-groups",
        ),
        pytest.param(
            "git+ssh://git@gitlab.company.com:8022/platform/backend/services/auth-service.git",
            None,
            (
                "git",
                "gitlab.company.com:8022",
                "platform/backend/services",
                "auth-service",
                None,
            ),
            id="nested-groups-with-port",
        ),
        pytest.param(
            "git+ssh://git@bitbucket.org/workspace/myrepo.git",
            None,
            ("git", "bitbucket.org", "workspace", "myrepo", None),
            id="bitbucket",
        ),
        pytest.param(
            "git+ssh://git@bitbucket.org/workspace/myrepo.git@develop",
            None,
            ("git", "bitbucket.org", "workspace", "myrepo", "develop"),
            id="bitbucket-with-ref",
        ),
        pytest.param(
            "git+ssh://someuser@git.example.com/acme/platform/custom-registry.git",
            None,
            ("someuser", "git.example.com", "acme/platform", "custom-registry", None),
            id="enterprise-custom-user",
        ),
        pytest.param(
            "git+ssh://git@github.com/myorg/myrepo.git@feature/custom-branch",
            None,
            ("git", "github.com", "myorg", "myrepo", "feature/custom-branch"),
            id="slash-ref",
        ),
        pytest.param(
            "git+ssh://git@github.com/myorg/myrepo.git",
            {"github.com", "gitlab.com"},
            ("git", "github.com", "myorg", "myrepo", None),
            id="allowed-domain",
        ),
        pytest.param(
            "git+ssh://git@gitlab.example.com:2222/myorg/myrepo.git",
            {"gitlab.example.com:2222", "github.com"},
            ("git", "gitlab.example.com:2222", "myorg", "myrepo", None),
            id="allowed-domain-with-port",
        ),
    ],
)
def test_parse_git_url(
    url: str,
    allowed_domains: set[str] | None,
    expected: tuple[str, str, str, str, str | None],
) -> None:
    git_url = parse_git_url(url, allowed_domains=allowed_domains)
    assert (
        git_url.user,
        git_url.host,
        git_url.org,
        git_url.repo,
        git_url.ref,
    ) == expected


@pytest.mark.parametrize(
    ("url", "allowed_domains", "match"),
    [
        pytest.param(
            "git+ssh://git@example.com/myorg/myrepo.git",
            {"github.com", "gitlab.com"},
            "Domain example.com not in allowed domains",
            id="disallowed-domain",
        ),
        pytest.param(
            "https://github.com/myorg/myrepo.git",
            None,
            "Unsupported URL format",
            id="https-url",
        ),
        pytest.param(
            "git+ssh://git@//",
            None,
            "Unsupported URL format",
            id="malformed-components",
        ),
    ],
)
def test_parse_git_url_rejects_invalid_urls(
    url: str, allowed_domains: set[str] | None, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        parse_git_url(url, allowed_domains=allowed_domains)


class TestResolveGitRef:
    """Test resolve_git_ref function."""

    @pytest.mark.anyio
    async def test_resolve_head_ref(self):
        """Test resolving HEAD ref."""
        mock_run_git = AsyncMock(return_value=(0, "abc123\tHEAD\n", ""))

        with patch("tracecat.git.utils.run_git", mock_run_git):
            sha = await resolve_git_ref(
                "git+ssh://git@github.com/myorg/myrepo.git",
                ref=None,
                env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
            )

        assert sha == "abc123"
        mock_run_git.assert_called_once_with(
            ["git", "ls-remote", "git+ssh://git@github.com/myorg/myrepo.git", "HEAD"],
            env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
            timeout=20.0,
        )

    @pytest.mark.anyio
    async def test_resolve_branch_ref(self):
        """Test resolving a branch ref."""
        mock_run_git = AsyncMock(return_value=(0, "def456\trefs/heads/main\n", ""))

        with patch("tracecat.git.utils.run_git", mock_run_git):
            sha = await resolve_git_ref(
                "git+ssh://git@github.com/myorg/myrepo.git",
                ref="main",
                env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
            )

        assert sha == "def456"
        mock_run_git.assert_called_once_with(
            [
                "git",
                "ls-remote",
                "git+ssh://git@github.com/myorg/myrepo.git",
                "refs/heads/main",
            ],
            env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
            timeout=20.0,
        )

    @pytest.mark.anyio
    async def test_resolve_ref_fallback(self):
        """Test resolving ref with fallback to direct ref."""
        # First call fails, second succeeds
        mock_run_git = AsyncMock(
            side_effect=[
                (1, "", "ref not found"),  # refs/heads/v1.0 fails
                (0, "ghi789\tv1.0\n", ""),  # v1.0 succeeds
            ]
        )

        with patch("tracecat.git.utils.run_git", mock_run_git):
            sha = await resolve_git_ref(
                "git+ssh://git@github.com/myorg/myrepo.git",
                ref="v1.0",
                env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
            )

        assert sha == "ghi789"
        assert mock_run_git.call_count == 2

    @pytest.mark.anyio
    async def test_resolve_ref_failure(self):
        """Test resolving ref that fails."""
        mock_run_git = AsyncMock(
            side_effect=[
                (1, "", "ref not found"),  # refs/heads/nonexistent fails
                (1, "", "ref not found"),  # nonexistent fails
            ]
        )

        with patch("tracecat.git.utils.run_git", mock_run_git):
            with pytest.raises(
                RuntimeError, match="Failed to resolve git ref 'nonexistent'"
            ):
                await resolve_git_ref(
                    "git+ssh://git@github.com/myorg/myrepo.git",
                    ref="nonexistent",
                    env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
                )

    @pytest.mark.anyio
    async def test_resolve_head_failure(self):
        """Test resolving HEAD that fails."""
        mock_run_git = AsyncMock(return_value=(1, "", "repository not found"))

        with patch("tracecat.git.utils.run_git", mock_run_git):
            with pytest.raises(RuntimeError, match="Failed to resolve git HEAD"):
                await resolve_git_ref(
                    "git+ssh://git@github.com/myorg/nonexistent.git",
                    ref=None,
                    env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
                )

    @pytest.mark.anyio
    async def test_resolve_empty_output(self):
        """Test resolving ref with empty output."""
        mock_run_git = AsyncMock(return_value=(0, "", ""))

        with patch("tracecat.git.utils.run_git", mock_run_git):
            with pytest.raises(RuntimeError, match="No output from git ls-remote"):
                await resolve_git_ref(
                    "git+ssh://git@github.com/myorg/myrepo.git",
                    ref=None,
                    env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
                )


class TestRunGit:
    """Test run_git function."""

    @pytest.mark.anyio
    async def test_run_git_timeout(self):
        """Test git command timeout."""
        mock_process = AsyncMock()
        mock_process.communicate.side_effect = TimeoutError()
        mock_process.kill.return_value = None
        mock_process.wait.return_value = None

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(RuntimeError, match="Git command timed out"):
                await run_git(
                    ["git", "clone", "huge-repo"],
                    env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
                    timeout=0.1,
                )

        mock_process.kill.assert_called_once()
        mock_process.wait.assert_called_once()


class TestListGitCommits:
    """Test list_git_commits function."""

    @pytest.mark.anyio
    async def test_list_git_commits_returns_typed_objects(self):
        """Test that list_git_commits returns GitCommitInfo objects."""
        # Mock git log output (using ASCII record separator \x1f)
        sep = "\x1f"
        mock_git_log_output = sep.join(
            [
                "abcdef1234567890abcdef1234567890abcdef12",
                "Initial commit",
                "John Doe",
                "john@example.com",
                "2024-01-01T10:00:00+00:00\n",
            ]
        ) + sep.join(
            [
                "def4567890abcdef1234567890abcdef12345678",
                "Second commit",
                "Jane Smith",
                "jane@example.com",
                "2024-01-02T11:00:00+00:00\n",
            ]
        )

        # Mock run_git calls: init, fetch, for-each-ref (tags), log
        mock_run_git = AsyncMock(
            side_effect=[
                (0, "", ""),  # git init --bare
                (0, "", ""),  # git fetch
                (0, "", ""),  # git for-each-ref tags (no tags)
                (0, mock_git_log_output, ""),  # git log
            ]
        )

        with patch("tracecat.git.utils.run_git", mock_run_git):
            # Create a proper async context manager mock
            mock_temp_dir = AsyncMock()
            mock_temp_dir.__aenter__ = AsyncMock(return_value="/tmp/test_repo")
            mock_temp_dir.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "aiofiles.tempfile.TemporaryDirectory", return_value=mock_temp_dir
            ):
                commits = await list_git_commits(
                    "git+ssh://git@github.com/myorg/myrepo.git",
                    env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
                    branch="main",
                    limit=2,
                )

        # Verify we get the correct number of commits
        assert len(commits) == 2

        # Verify all returned objects are GitCommitInfo instances
        assert all(isinstance(commit, GitCommitInfo) for commit in commits)

        # Verify the first commit data
        first_commit = commits[0]
        assert first_commit.sha == "abcdef1234567890abcdef1234567890abcdef12"
        assert first_commit.message == "Initial commit"
        assert first_commit.author == "John Doe"
        assert first_commit.author_email == "john@example.com"
        assert first_commit.date == "2024-01-01T10:00:00+00:00"
        assert first_commit.tags == []  # No tags in this test

        # Verify the second commit data
        second_commit = commits[1]
        assert second_commit.sha == "def4567890abcdef1234567890abcdef12345678"
        assert second_commit.message == "Second commit"
        assert second_commit.author == "Jane Smith"
        assert second_commit.author_email == "jane@example.com"
        assert second_commit.date == "2024-01-02T11:00:00+00:00"
        assert second_commit.tags == []  # No tags in this test

    @pytest.mark.anyio
    async def test_list_git_commits_with_tags(self):
        """Test that list_git_commits includes tag information."""
        # Mock git log output (using ASCII record separator \x1f)
        sep = "\x1f"
        mock_git_log_output = sep.join(
            [
                "abcdef1234567890abcdef1234567890abcdef12",
                "Release v1.0.0",
                "John Doe",
                "john@example.com",
                "2024-01-01T10:00:00+00:00\n",
            ]
        ) + sep.join(
            [
                "def4567890abcdef1234567890abcdef12345678",
                "Patch release",
                "Jane Smith",
                "jane@example.com",
                "2024-01-02T11:00:00+00:00\n",
            ]
        )

        # Mock tag output
        mock_tag_output = (
            "abcdef1234567890abcdef1234567890abcdef12 v1.0.0\n"
            "abcdef1234567890abcdef1234567890abcdef12 1.0.0\n"
            "def4567890abcdef1234567890abcdef12345678 v1.0.1"
        )

        # Mock run_git calls: init, fetch, for-each-ref (tags), log
        mock_run_git = AsyncMock(
            side_effect=[
                (0, "", ""),  # git init --bare
                (0, "", ""),  # git fetch
                (0, mock_tag_output, ""),  # git for-each-ref tags
                (0, mock_git_log_output, ""),  # git log
            ]
        )

        with patch("tracecat.git.utils.run_git", mock_run_git):
            # Create a proper async context manager mock
            mock_temp_dir = AsyncMock()
            mock_temp_dir.__aenter__ = AsyncMock(return_value="/tmp/test_repo")
            mock_temp_dir.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "aiofiles.tempfile.TemporaryDirectory", return_value=mock_temp_dir
            ):
                commits = await list_git_commits(
                    "git+ssh://git@github.com/myorg/myrepo.git",
                    env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
                    branch="main",
                    limit=2,
                )

        # Verify we get the correct number of commits
        assert len(commits) == 2

        # Verify the first commit has multiple tags
        first_commit = commits[0]
        assert first_commit.sha == "abcdef1234567890abcdef1234567890abcdef12"
        assert first_commit.message == "Release v1.0.0"
        assert sorted(first_commit.tags) == ["1.0.0", "v1.0.0"]

        # Verify the second commit has one tag
        second_commit = commits[1]
        assert second_commit.sha == "def4567890abcdef1234567890abcdef12345678"
        assert second_commit.message == "Patch release"
        assert second_commit.tags == ["v1.0.1"]

    @pytest.mark.anyio
    async def test_list_git_commits_tag_command_failure(self):
        """Test that list_git_commits handles tag command failure gracefully."""
        # Mock git log output
        sep = "\x1f"
        mock_git_log_output = sep.join(
            [
                "abcdef1234567890abcdef1234567890abcdef12",
                "Initial commit",
                "John Doe",
                "john@example.com",
                "2024-01-01T10:00:00+00:00\n",
            ]
        )

        # Mock run_git calls: init, fetch, for-each-ref (fails), log
        mock_run_git = AsyncMock(
            side_effect=[
                (0, "", ""),  # git init --bare
                (0, "", ""),  # git fetch
                (1, "", "Failed to list tags"),  # git for-each-ref tags (fails)
                (0, mock_git_log_output, ""),  # git log
            ]
        )

        with patch("tracecat.git.utils.run_git", mock_run_git):
            # Create a proper async context manager mock
            mock_temp_dir = AsyncMock()
            mock_temp_dir.__aenter__ = AsyncMock(return_value="/tmp/test_repo")
            mock_temp_dir.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "aiofiles.tempfile.TemporaryDirectory", return_value=mock_temp_dir
            ):
                commits = await list_git_commits(
                    "git+ssh://git@github.com/myorg/myrepo.git",
                    env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
                    branch="main",
                    limit=1,
                )

        # Should still work, just without tags
        assert len(commits) == 1
        first_commit = commits[0]
        assert first_commit.sha == "abcdef1234567890abcdef1234567890abcdef12"
        assert first_commit.tags == []
