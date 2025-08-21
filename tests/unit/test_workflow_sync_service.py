"""Tests for WorkflowSyncService functionality."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.models import ActionStatement
from tracecat.git.models import GitUrl
from tracecat.sync import PushOptions
from tracecat.types.auth import Role
from tracecat.workflow.store.sync import WorkflowSyncService


@pytest.fixture
def workspace_id():
    """Test workspace ID."""
    return uuid.UUID("550e8400-e29b-41d4-a716-446655440000")


@pytest.fixture
def git_url():
    """Test Git URL."""
    return GitUrl(host="github.com", org="test-org", repo="test-repo", ref="main")


@pytest.fixture
def sample_workflow():
    """Sample workflow DSL."""
    return DSLInput(
        title="Test Workflow",
        description="A test workflow",
        entrypoint=DSLEntrypoint(ref="start", expects={}),
        actions=[
            ActionStatement(
                ref="start",
                action="core.transform.passthrough",
                args={"value": "test"},
            )
        ],
    )


@pytest.fixture
def workflow_sync_service(workspace_id):
    """WorkflowSyncService instance for testing."""
    # Use a mock session for unit tests
    mock_session = AsyncMock()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
    )
    return WorkflowSyncService(session=mock_session, role=role)


class TestWorkflowSyncService:
    """Tests for WorkflowSyncService."""

    @pytest.mark.anyio
    async def test_pull_not_implemented(self, workflow_sync_service, git_url):
        """Test that pull raises NotImplementedError."""
        with pytest.raises(
            NotImplementedError, match="Pull functionality is not yet implemented"
        ):
            await workflow_sync_service.pull(url=git_url)

    @pytest.mark.anyio
    async def test_push_workflows_success(
        self, workflow_sync_service, git_url, sample_workflow
    ):
        """Test successful workflow push to Git repository."""
        options = PushOptions(message="Update workflows", create_pr=False)

        # Mock PyGithub components - use regular Mock objects, not AsyncMock
        from unittest.mock import Mock

        from github.GithubException import GithubException

        mock_repo = Mock()
        mock_branch = Mock()
        mock_branch.commit.sha = "abc123def"
        mock_repo.default_branch = "main"
        mock_repo.get_branch.return_value = mock_branch
        mock_repo.create_git_ref = Mock()
        mock_repo.create_file = Mock()
        # Mock get_contents to raise 404 (file doesn't exist)
        mock_repo.get_contents.side_effect = GithubException(
            404, {"message": "Not Found"}, {}
        )

        mock_github_client = Mock()
        mock_github_client.get_repo.return_value = mock_repo
        mock_github_client.close = Mock()

        with (
            patch(
                "tracecat.workflow.store.sync.GitHubAppService"
            ) as mock_gh_service_class,
            patch("asyncio.to_thread") as mock_to_thread,
        ):
            mock_gh_service = AsyncMock()
            mock_gh_service.get_github_client_for_repo.return_value = mock_github_client
            mock_gh_service_class.return_value = mock_gh_service

            # Mock asyncio.to_thread to return the direct result (not coroutine)
            async def mock_to_thread_impl(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = mock_to_thread_impl

            result = await workflow_sync_service.push(
                objects=[sample_workflow], url=git_url, options=options
            )

            assert result.sha == "abc123def"
            assert result.ref.startswith("tracecat-sync-")

            # Verify GitHub API calls were made
            mock_repo.create_git_ref.assert_called_once()
            mock_repo.create_file.assert_called_once()

    @pytest.mark.anyio
    async def test_push_workflows_with_pr(
        self, workflow_sync_service, git_url, sample_workflow
    ):
        """Test workflow push with pull request creation."""
        options = PushOptions(message="Update workflows", create_pr=True)

        # Mock PyGithub components - use regular Mock objects, not AsyncMock
        from unittest.mock import Mock

        from github.GithubException import GithubException

        mock_repo = Mock()
        mock_branch = Mock()
        mock_branch.commit.sha = "abc123def"
        mock_repo.default_branch = "main"
        mock_repo.get_branch.return_value = mock_branch
        mock_repo.create_git_ref = Mock()
        mock_repo.create_file = Mock()
        # Mock get_contents to raise 404 (file doesn't exist)
        mock_repo.get_contents.side_effect = GithubException(
            404, {"message": "Not Found"}, {}
        )

        mock_pr = Mock()
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/123"
        mock_pr.number = 123
        mock_repo.create_pull.return_value = mock_pr

        mock_github_client = Mock()
        mock_github_client.get_repo.return_value = mock_repo
        mock_github_client.close = Mock()

        with (
            patch(
                "tracecat.workflow.store.sync.GitHubAppService"
            ) as mock_gh_service_class,
            patch("asyncio.to_thread") as mock_to_thread,
        ):
            mock_gh_service = AsyncMock()
            mock_gh_service.get_github_client_for_repo.return_value = mock_github_client
            mock_gh_service_class.return_value = mock_gh_service

            # Mock asyncio.to_thread to return the direct result (not coroutine)
            async def mock_to_thread_impl(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = mock_to_thread_impl

            result = await workflow_sync_service.push(
                objects=[sample_workflow], url=git_url, options=options
            )

            assert result.sha == "abc123def"
            assert result.ref.startswith("tracecat-sync-")

            # Verify PR was created
            mock_repo.create_pull.assert_called_once()

    @pytest.mark.anyio
    async def test_push_workflows_empty_objects(self, workflow_sync_service, git_url):
        """Test push fails with empty objects list."""
        options = PushOptions(message="Update workflows")

        with pytest.raises(ValueError, match="No workflow objects to push"):
            await workflow_sync_service.push(objects=[], url=git_url, options=options)

    @pytest.mark.anyio
    async def test_push_workflows_github_failure(
        self, workflow_sync_service, git_url, sample_workflow
    ):
        """Test push handles GitHub API failures."""
        options = PushOptions(message="Update workflows")

        with (
            patch(
                "tracecat.workflow.store.sync.GitHubAppService"
            ) as mock_gh_service_class,
        ):
            mock_gh_service = AsyncMock()
            mock_gh_service.get_github_client_for_repo.side_effect = Exception(
                "GitHub API error"
            )
            mock_gh_service_class.return_value = mock_gh_service

            with pytest.raises(Exception, match="GitHub API error"):
                await workflow_sync_service.push(
                    objects=[sample_workflow], url=git_url, options=options
                )

    @pytest.mark.anyio
    async def test_filename_generation(self, workflow_sync_service):
        """Test workflow filename generation from title."""
        # Test with normal title
        workflow1 = DSLInput(
            title="My Test Workflow",
            description="Test",
            entrypoint=DSLEntrypoint(ref="start", expects={}),
            actions=[ActionStatement(ref="start", action="core.noop", args={})],
        )

        options = PushOptions(message="Test")
        git_url = GitUrl(host="github.com", org="test", repo="test")

        # Mock PyGithub components - use regular Mock objects, not AsyncMock
        from unittest.mock import Mock

        from github.GithubException import GithubException

        mock_repo = Mock()
        mock_branch = Mock()
        mock_branch.commit.sha = "abc123def"
        mock_repo.default_branch = "main"
        mock_repo.get_branch.return_value = mock_branch
        mock_repo.create_git_ref = Mock()
        mock_repo.create_file = Mock()
        # Mock get_contents to raise 404 (file doesn't exist)
        mock_repo.get_contents.side_effect = GithubException(
            404, {"message": "Not Found"}, {}
        )

        mock_github_client = Mock()
        mock_github_client.get_repo.return_value = mock_repo
        mock_github_client.close = Mock()

        with (
            patch(
                "tracecat.workflow.store.sync.GitHubAppService"
            ) as mock_gh_service_class,
            patch("asyncio.to_thread") as mock_to_thread,
        ):
            mock_gh_service = AsyncMock()
            mock_gh_service.get_github_client_for_repo.return_value = mock_github_client
            mock_gh_service_class.return_value = mock_gh_service

            # Mock asyncio.to_thread to return the direct result (not coroutine)
            async def mock_to_thread_impl(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = mock_to_thread_impl

            await workflow_sync_service.push(
                objects=[workflow1], url=git_url, options=options
            )

            # Verify the file was created with sanitized filename
            mock_repo.create_file.assert_called_once()
            call_args = mock_repo.create_file.call_args
            file_path = call_args.kwargs["path"]
            assert file_path == "workflows/my-test-workflow.yaml"
