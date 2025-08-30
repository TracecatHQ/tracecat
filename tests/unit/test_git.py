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
from tracecat.registry.repositories.models import GitCommitInfo
from tracecat.ssh import SshEnv


class TestGitUrl:
    """Test GitUrl dataclass."""

    def test_git_url_creation(self):
        """Test GitUrl creation."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo")
        assert git_url.host == "github.com"
        assert git_url.org == "myorg"
        assert git_url.repo == "myrepo"
        assert git_url.ref is None

    def test_git_url_with_ref(self):
        """Test GitUrl creation with ref."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo", ref="main")
        assert git_url.ref == "main"

    def test_git_url_to_url(self):
        """Test GitUrl to_url method."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo")
        assert git_url.to_url() == "git+ssh://git@github.com/myorg/myrepo.git"

    def test_git_url_to_url_with_ref(self):
        """Test GitUrl to_url method with ref."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo", ref="main")
        assert git_url.to_url() == "git+ssh://git@github.com/myorg/myrepo.git@main"

    def test_git_url_frozen(self):
        """Test that GitUrl is frozen (immutable)."""
        git_url = GitUrl(host="github.com", org="myorg", repo="myrepo")
        with pytest.raises(
            (AttributeError, TypeError)
        ):  # Should raise an error for frozen dataclass
            git_url.host = "gitlab.com"  # type: ignore


class TestParseGitUrl:
    """Test parse_git_url function."""

    def test_parse_valid_url(self):
        """Test parsing a valid Git SSH URL."""
        url = "git+ssh://git@github.com/myorg/myrepo.git"
        git_url = parse_git_url(url)
        assert git_url.host == "github.com"
        assert git_url.org == "myorg"
        assert git_url.repo == "myrepo"
        assert git_url.ref is None

    def test_parse_url_with_ref(self):
        """Test parsing a Git SSH URL with ref."""
        url = "git+ssh://git@github.com/myorg/myrepo.git@main"
        git_url = parse_git_url(url)
        assert git_url.host == "github.com"
        assert git_url.org == "myorg"
        assert git_url.repo == "myrepo"
        assert git_url.ref == "main"

    def test_parse_url_without_git_suffix(self):
        """Test parsing a Git SSH URL without .git suffix."""
        url = "git+ssh://git@github.com/myorg/myrepo@main"
        git_url = parse_git_url(url)
        assert git_url.host == "github.com"
        assert git_url.org == "myorg"
        assert git_url.repo == "myrepo"
        assert git_url.ref == "main"

    def test_parse_gitlab_self_hosted_with_port(self):
        """Test parsing GitLab self-hosted URL with port."""
        url = "git+ssh://git@gitlab.example.com:2222/myorg/myrepo.git"
        git_url = parse_git_url(url)
        assert git_url.host == "gitlab.example.com:2222"
        assert git_url.org == "myorg"
        assert git_url.repo == "myrepo"
        assert git_url.ref is None

    def test_parse_gitlab_self_hosted_with_port_and_ref(self):
        """Test parsing GitLab self-hosted URL with port and ref."""
        url = "git+ssh://git@gitlab.example.com:2222/myorg/myrepo.git@develop"
        git_url = parse_git_url(url)
        assert git_url.host == "gitlab.example.com:2222"
        assert git_url.org == "myorg"
        assert git_url.repo == "myrepo"
        assert git_url.ref == "develop"

    def test_parse_gitlab_nested_groups(self):
        """Test parsing GitLab URL with nested groups/subgroups."""
        url = "git+ssh://git@gitlab.com/myorg/team/subteam/myrepo.git"
        git_url = parse_git_url(url)
        assert git_url.host == "gitlab.com"
        assert git_url.org == "myorg/team/subteam"
        assert git_url.repo == "myrepo"
        assert git_url.ref is None

    def test_parse_gitlab_deep_nested_groups(self):
        """Test parsing GitLab URL with deeply nested groups."""
        url = (
            "git+ssh://git@gitlab.com/org/dept/team/project/subproject/repo.git@feature"
        )
        git_url = parse_git_url(url)
        assert git_url.host == "gitlab.com"
        assert git_url.org == "org/dept/team/project/subproject"
        assert git_url.repo == "repo"
        assert git_url.ref == "feature"

    def test_parse_gitlab_nested_groups_with_port(self):
        """Test parsing GitLab self-hosted URL with port and nested groups."""
        url = "git+ssh://git@gitlab.company.com:8022/platform/backend/services/auth-service.git"
        git_url = parse_git_url(url)
        assert git_url.host == "gitlab.company.com:8022"
        assert git_url.org == "platform/backend/services"
        assert git_url.repo == "auth-service"
        assert git_url.ref is None

    def test_parse_bitbucket_url(self):
        """Test parsing Bitbucket URL."""
        url = "git+ssh://git@bitbucket.org/workspace/myrepo.git"
        git_url = parse_git_url(url)
        assert git_url.host == "bitbucket.org"
        assert git_url.org == "workspace"
        assert git_url.repo == "myrepo"
        assert git_url.ref is None

    def test_parse_bitbucket_url_with_ref(self):
        """Test parsing Bitbucket URL with ref."""
        url = "git+ssh://git@bitbucket.org/workspace/myrepo.git@develop"
        git_url = parse_git_url(url)
        assert git_url.host == "bitbucket.org"
        assert git_url.org == "workspace"
        assert git_url.repo == "myrepo"
        assert git_url.ref == "develop"

    def test_parse_url_allowed_domains(self):
        """Test parsing with allowed domains."""
        url = "git+ssh://git@github.com/myorg/myrepo.git"
        allowed_domains = {"github.com", "gitlab.com"}
        git_url = parse_git_url(url, allowed_domains=allowed_domains)
        assert git_url.host == "github.com"

    def test_parse_url_allowed_domains_with_port(self):
        """Test parsing with allowed domains including port."""
        url = "git+ssh://git@gitlab.example.com:2222/myorg/myrepo.git"
        allowed_domains = {"gitlab.example.com:2222", "github.com"}
        git_url = parse_git_url(url, allowed_domains=allowed_domains)
        assert git_url.host == "gitlab.example.com:2222"

    def test_parse_url_disallowed_domain(self):
        """Test parsing with disallowed domain."""
        url = "git+ssh://git@example.com/myorg/myrepo.git"
        allowed_domains = {"github.com", "gitlab.com"}
        with pytest.raises(
            ValueError, match="Domain example.com not in allowed domains"
        ):
            parse_git_url(url, allowed_domains=allowed_domains)

    def test_parse_invalid_url_format(self):
        """Test parsing an invalid URL format."""
        url = "https://github.com/myorg/myrepo.git"
        with pytest.raises(ValueError, match="Unsupported URL format"):
            parse_git_url(url)

    def test_parse_invalid_url_components(self):
        """Test parsing URL with invalid components."""
        # This would be a malformed URL that doesn't match the regex
        url = "git+ssh://git@//"
        with pytest.raises(ValueError, match="Unsupported URL format"):
            parse_git_url(url)


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
    async def test_run_git_success(self):
        """Test successful git command execution."""
        # Mock the subprocess
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"output", b"")
        mock_process.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            code, stdout, stderr = await run_git(
                ["git", "version"],
                env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
            )

        assert code == 0
        assert stdout == "output"
        assert stderr == ""

    @pytest.mark.anyio
    async def test_run_git_failure(self):
        """Test failed git command execution."""
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"error message")
        mock_process.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            code, stdout, stderr = await run_git(
                ["git", "invalid-command"],
                env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
            )

        assert code == 1
        assert stdout == ""
        assert stderr == "error message"

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

    @pytest.mark.anyio
    async def test_run_git_with_cwd(self):
        """Test git command with working directory."""
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"output", b"")
        mock_process.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        ) as mock_create:
            await run_git(
                ["git", "status"],
                env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
                cwd="/tmp/repo",
            )

        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert kwargs["cwd"] == "/tmp/repo"


class TestListGitCommits:
    """Test list_git_commits function."""

    @pytest.mark.anyio
    async def test_list_git_commits_returns_typed_objects(self):
        """Test that list_git_commits returns GitCommitInfo objects."""
        # Mock git log output
        mock_git_log_output = (
            "abcdef1234567890abcdef1234567890abcdef12|Initial commit|John Doe|john@example.com|2024-01-01T10:00:00+00:00\n"
            "def4567890abcdef1234567890abcdef12345678|Second commit|Jane Smith|jane@example.com|2024-01-02T11:00:00+00:00\n"
        )

        # Mock run_git calls: init, fetch, log
        mock_run_git = AsyncMock(
            side_effect=[
                (0, "", ""),  # git init --bare
                (0, "", ""),  # git fetch
                (0, mock_git_log_output, ""),  # git log
            ]
        )

        with patch("tracecat.git.utils.run_git", mock_run_git):
            with patch("aiofiles.tempfile.TemporaryDirectory", new_callable=AsyncMock):
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

        # Verify the second commit data
        second_commit = commits[1]
        assert second_commit.sha == "def4567890abcdef1234567890abcdef12345678"
        assert second_commit.message == "Second commit"
        assert second_commit.author == "Jane Smith"
        assert second_commit.author_email == "jane@example.com"
        assert second_commit.date == "2024-01-02T11:00:00+00:00"

    @pytest.mark.anyio
    async def test_list_git_commits_empty_output(self):
        """Test list_git_commits with empty git log output."""
        # Mock run_git calls: init, fetch, empty log
        mock_run_git = AsyncMock(
            side_effect=[
                (0, "", ""),  # git init --bare
                (0, "", ""),  # git fetch
                (0, "", ""),  # git log (empty)
            ]
        )

        with patch("tracecat.git.utils.run_git", mock_run_git):
            with patch("aiofiles.tempfile.TemporaryDirectory"):
                commits = await list_git_commits(
                    "git+ssh://git@github.com/myorg/myrepo.git",
                    env=SshEnv(ssh_agent_pid="123456", ssh_auth_sock="/tmp/auth.sock"),
                )

        # Should return empty list
        assert commits == []
