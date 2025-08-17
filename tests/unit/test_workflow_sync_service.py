"""Tests for WorkflowSyncService functionality."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.models import ActionStatement
from tracecat.git.models import GitUrl
from tracecat.sync import PullOptions, PushOptions
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
    async def test_pull_workflows_success(
        self, workflow_sync_service, git_url, sample_workflow
    ):
        """Test successful workflow pull from Git repository."""
        mock_store = AsyncMock()
        mock_store.list_sources.return_value = [
            MagicMock(path="workflows/test.yaml", sha="abc123", id="wf-123")
        ]
        mock_store.fetch_content.return_value = sample_workflow.dump_yaml()

        with (
            patch(
                "tracecat.workflow.store.sync.GitWorkflowStore", return_value=mock_store
            ),
            patch("tracecat.workflow.store.sync.git_env_context") as mock_env_context,
            patch(
                "tracecat.workflow.store.sync.resolve_git_ref", return_value="abc123"
            ),
        ):
            mock_env_context.return_value.__aenter__.return_value = {}

            workflows = await workflow_sync_service.pull(url=git_url)

            assert len(workflows) == 1
            assert workflows[0].title == "Test Workflow"
            assert workflows[0].description == "A test workflow"

    @pytest.mark.anyio
    async def test_pull_workflows_with_path_filter(
        self, workflow_sync_service, git_url, sample_workflow
    ):
        """Test workflow pull with path filtering."""
        mock_store = AsyncMock()
        mock_store.list_sources.return_value = [
            MagicMock(path="workflows/prod/test.yaml", sha="abc123", id="wf-123"),
            MagicMock(path="workflows/dev/test.yaml", sha="def456", id="wf-456"),
        ]
        mock_store.fetch_content.return_value = sample_workflow.dump_yaml()

        with (
            patch(
                "tracecat.workflow.store.sync.GitWorkflowStore", return_value=mock_store
            ),
            patch("tracecat.workflow.store.sync.git_env_context") as mock_env_context,
            patch(
                "tracecat.workflow.store.sync.resolve_git_ref", return_value="abc123"
            ),
        ):
            mock_env_context.return_value.__aenter__.return_value = {}

            options = PullOptions(paths=["workflows/prod/"])
            workflows = await workflow_sync_service.pull(url=git_url, options=options)

            assert len(workflows) == 1
            # Only the prod workflow should be included

    @pytest.mark.anyio
    async def test_pull_workflows_without_ee(self, workflow_sync_service, git_url):
        """Test pull fails gracefully when EE not available."""
        with patch("tracecat.workflow.store.sync.GitWorkflowStore", None):
            with pytest.raises(RuntimeError, match="GitWorkflowStore not available"):
                await workflow_sync_service.pull(url=git_url)

    @pytest.mark.anyio
    async def test_push_workflows_success(
        self, workflow_sync_service, git_url, sample_workflow
    ):
        """Test successful workflow push to Git repository."""
        options = PushOptions(message="Update workflows", create_pr=False)

        with (
            patch("tracecat.workflow.store.sync.git_env_context") as mock_env_context,
            patch("tracecat.workflow.store.sync.run_git") as mock_run_git,
            patch("tempfile.TemporaryDirectory") as mock_temp_dir,
            patch("pathlib.Path.mkdir"),
            patch("builtins.open", create=True),
        ):
            mock_env_context.return_value.__aenter__.return_value = {}
            mock_run_git.return_value = (0, "abc123def", "")

            # Mock temporary directory
            mock_temp_dir.return_value.__enter__.return_value = "/tmp/test"

            result = await workflow_sync_service.push(
                objects=[sample_workflow], url=git_url, options=options
            )

            assert result.sha == "abc123def"
            assert result.ref.startswith("tracecat-sync-")

            # Verify git commands were called
            assert mock_run_git.call_count >= 4  # clone, checkout, add, commit, push

    @pytest.mark.anyio
    async def test_push_workflows_with_pr(
        self, workflow_sync_service, git_url, sample_workflow
    ):
        """Test workflow push with pull request creation."""
        options = PushOptions(message="Update workflows", create_pr=True)

        with (
            patch("tracecat.workflow.store.sync.git_env_context") as mock_env_context,
            patch("tracecat.workflow.store.sync.run_git") as mock_run_git,
            patch("tempfile.TemporaryDirectory") as mock_temp_dir,
            patch("pathlib.Path.mkdir"),
            patch("builtins.open", create=True),
        ):
            mock_env_context.return_value.__aenter__.return_value = {}
            # Return different values for different git commands
            mock_run_git.side_effect = [
                (0, "", ""),  # clone
                (0, "", ""),  # checkout
                (0, "", ""),  # add
                (0, "", ""),  # commit
                (0, "", ""),  # push
                (0, "abc123def", ""),  # rev-parse
                (
                    0,
                    "https://github.com/test-org/test-repo/pull/123",
                    "",
                ),  # gh pr create
            ]

            # Mock temporary directory
            mock_temp_dir.return_value.__enter__.return_value = "/tmp/test"

            result = await workflow_sync_service.push(
                objects=[sample_workflow], url=git_url, options=options
            )

            assert result.sha == "abc123def"
            assert result.ref.startswith("tracecat-sync-")

    @pytest.mark.anyio
    async def test_push_workflows_empty_objects(self, workflow_sync_service, git_url):
        """Test push fails with empty objects list."""
        options = PushOptions(message="Update workflows")

        with pytest.raises(ValueError, match="No workflow objects to push"):
            await workflow_sync_service.push(objects=[], url=git_url, options=options)

    @pytest.mark.anyio
    async def test_push_workflows_no_message(
        self, workflow_sync_service, git_url, sample_workflow
    ):
        """Test push fails without commit message."""
        options = PushOptions(message="")

        with pytest.raises(ValueError, match="Commit message is required"):
            await workflow_sync_service.push(
                objects=[sample_workflow], url=git_url, options=options
            )

    @pytest.mark.anyio
    async def test_push_workflows_git_failure(
        self, workflow_sync_service, git_url, sample_workflow
    ):
        """Test push handles Git command failures."""
        options = PushOptions(message="Update workflows")

        with (
            patch("tracecat.workflow.store.sync.git_env_context") as mock_env_context,
            patch("tracecat.workflow.store.sync.run_git") as mock_run_git,
            patch("tempfile.TemporaryDirectory") as mock_temp_dir,
        ):
            mock_env_context.return_value.__aenter__.return_value = {}
            # Simulate git clone failure
            mock_run_git.return_value = (1, "", "Permission denied")
            mock_temp_dir.return_value.__enter__.return_value = "/tmp/test"

            with pytest.raises(RuntimeError, match="Failed to clone repository"):
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

        # Test with special characters
        workflow2 = DSLInput(
            title="Special@#$%Characters!",
            description="Test",
            entrypoint=DSLEntrypoint(ref="start", expects={}),
            actions=[ActionStatement(ref="start", action="core.noop", args={})],
        )

        # Test with very long title
        workflow3 = DSLInput(
            title="A" * 100,  # Very long title
            description="Test",
            entrypoint=DSLEntrypoint(ref="start", expects={}),
            actions=[ActionStatement(ref="start", action="core.noop", args={})],
        )

        options = PushOptions(message="Test")

        with (
            patch("tracecat.workflow.store.sync.git_env_context") as mock_env_context,
            patch("tracecat.workflow.store.sync.run_git") as mock_run_git,
            patch("tempfile.TemporaryDirectory") as mock_temp_dir,
            patch("pathlib.Path.mkdir"),
            patch("builtins.open", create=True) as mock_open,
        ):
            mock_env_context.return_value.__aenter__.return_value = {}
            mock_run_git.return_value = (0, "abc123", "")
            mock_temp_dir.return_value.__enter__.return_value = "/tmp/test"

            git_url = GitUrl(host="github.com", org="test", repo="test")

            await workflow_sync_service.push(
                objects=[workflow1, workflow2, workflow3], url=git_url, options=options
            )

            # Check that files were created with sanitized names
            call_args = [call[0][0] for call in mock_open.call_args_list]

            # Should have my-test-workflow.yaml
            assert any("my-test-workflow.yaml" in str(arg) for arg in call_args)

            # Should have special characters removed
            assert any("specialcharacters.yaml" in str(arg) for arg in call_args)

            # Should have truncated long title
            assert any(
                len(str(arg).split("/")[-1].replace(".yaml", "")) <= 50
                for arg in call_args
            )
