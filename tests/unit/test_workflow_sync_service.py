"""Tests for WorkflowSyncService functionality."""

import base64
import uuid
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
import yaml
from github.GithubException import GithubException

from tracecat.auth.types import Role
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.sync import Author, PushObject, PushOptions, PushStatus
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import RemoteWorkflowDefinition
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
def sample_remote_workflow(sample_workflow):
    """Sample RemoteWorkflowDefinition."""
    return RemoteWorkflowDefinition(
        id="wf_123abc",
        alias="test-workflow",
        definition=sample_workflow,
    )


@pytest.fixture
def sample_remote_workflow_with_folder(sample_workflow):
    """Sample RemoteWorkflowDefinition with folder_path."""
    return RemoteWorkflowDefinition(
        id="wf_folder123",
        alias="test-workflow-with-folder",
        folder_path="/security/detections/",
        definition=sample_workflow,
    )


@pytest.fixture
def organization_id():
    """Test organization ID."""
    return uuid.UUID("550e8400-e29b-41d4-a716-446655440001")


@pytest.fixture
def workflow_sync_service(workspace_id, organization_id):
    """WorkflowSyncService instance for testing."""
    # Use a mock session for unit tests
    mock_session = AsyncMock()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=organization_id,
    )
    return WorkflowSyncService(session=mock_session, role=role)


@pytest.fixture
def workflow_import_service(workspace_id, organization_id):
    """WorkflowImportService instance for testing."""
    # Use a mock session for unit tests
    mock_session = AsyncMock()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=organization_id,
    )
    return WorkflowImportService(session=mock_session, role=role)


async def _mock_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def _tree_element_path(tree_element: object) -> str:
    return vars(tree_element)["_InputGitTreeElement__path"]


def _create_target_branch_repo(
    *,
    branch_name: str,
    base_branch_name: str = "main",
    default_branch: str = "main",
    branch_exists: bool = True,
    existing_file_contents: dict[str, str] | None = None,
    base_commit_sha: str = "base123",
    branch_head_sha: str = "branch-head123",
    parent_commit_sha: str = "parent123",
    new_commit_sha: str = "new-commit123",
) -> tuple[Mock, Mock, Mock, Mock]:
    mock_repo = Mock()
    mock_repo.default_branch = default_branch

    base_branch = Mock()
    base_branch.commit.sha = base_commit_sha

    target_branch = Mock()
    target_branch.commit.sha = branch_head_sha

    def get_branch(name: str):
        if name == base_branch_name:
            return base_branch
        if name == branch_name:
            if not branch_exists:
                raise GithubException(404, {"message": "Not Found"}, {})
            return target_branch
        raise AssertionError(f"Unexpected branch lookup: {name}")

    mock_repo.get_branch.side_effect = get_branch

    encoded_contents = {
        path: base64.b64encode(content.encode("utf-8")).decode("utf-8")
        for path, content in (existing_file_contents or {}).items()
    }

    def get_contents(path: str, ref: str):
        if path not in encoded_contents:
            raise GithubException(404, {"message": "Not Found"}, {})
        content_file = Mock()
        content_file.path = path
        content_file.sha = f"sha-{path}"
        content_file.content = encoded_contents[path]
        return content_file

    mock_repo.get_contents.side_effect = get_contents

    branch_ref = Mock()
    branch_ref.object.sha = parent_commit_sha
    branch_ref.edit = Mock()

    parent_commit = Mock()
    parent_commit.sha = parent_commit_sha
    parent_commit.tree = Mock()

    new_tree = Mock()
    new_tree.sha = "tree123"

    new_commit = Mock()
    new_commit.sha = new_commit_sha

    mock_repo.get_git_ref.return_value = branch_ref
    mock_repo.get_git_commit.return_value = parent_commit
    mock_repo.create_git_tree.return_value = new_tree
    mock_repo.create_git_commit.return_value = new_commit
    mock_repo.create_git_ref = Mock()
    mock_repo.get_pulls.return_value = []
    mock_repo.create_pull = Mock()

    return mock_repo, branch_ref, parent_commit, new_commit


class TestWorkflowSyncService:
    """Tests for WorkflowSyncService."""

    @pytest.mark.anyio
    async def test_pull_requires_commit_sha(self, workflow_sync_service, git_url):
        """Test that pull returns error result when commit_sha is missing."""
        result = await workflow_sync_service.pull(url=git_url)

        assert result.success is False
        assert result.commit_sha == ""
        assert result.workflows_found == 0
        assert result.workflows_imported == 0
        assert result.message == "commit_sha is required"
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].error_type == "validation"
        assert "commit_sha is required" in result.diagnostics[0].message

    @pytest.mark.anyio
    async def test_push_workflows_success(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test successful workflow push to Git repository."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows", author=author, create_pr=False
        )

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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            # Mock asyncio.to_thread to return the direct result (not coroutine)
            async def mock_to_thread_impl(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = mock_to_thread_impl

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == "abc123def"
            assert result.ref.startswith("tracecat-sync-")

            # Verify GitHub API calls were made
            mock_repo.create_git_ref.assert_called_once()
            mock_repo.create_file.assert_called_once()

    @pytest.mark.anyio
    async def test_push_objects_with_stable_path(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test push with explicit stable path using PushObject."""
        stable_path = "workflows/wf_123abc.yml"
        push_item = PushObject(data=sample_remote_workflow, path=stable_path)
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows", author=author, create_pr=False
        )

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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            # Mock asyncio.to_thread to return the direct result (not coroutine)
            async def mock_to_thread_impl(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = mock_to_thread_impl

            result = await workflow_sync_service.push(
                objects=[push_item], url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == "abc123def"
            assert result.ref.startswith("tracecat-sync-")

            # Verify exact path was used
            mock_repo.create_file.assert_called_once()
            call_args = mock_repo.create_file.call_args
            assert call_args.kwargs["path"] == stable_path

    @pytest.mark.anyio
    async def test_push_workflows_with_pr(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test workflow push with pull request creation."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(message="Update workflows", author=author, create_pr=True)

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
            patch(
                "tracecat.workflow.store.sync.WorkspaceService"
            ) as mock_ws_service_class,
        ):
            mock_gh_service = AsyncMock()
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            # Mock WorkspaceService
            mock_ws_service = AsyncMock()
            mock_workspace = Mock()
            mock_workspace.name = "Test Workspace"
            mock_ws_service.get_workspace.return_value = mock_workspace
            mock_ws_service_class.return_value = mock_ws_service

            # Mock asyncio.to_thread to return the direct result (not coroutine)
            async def mock_to_thread_impl(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = mock_to_thread_impl

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == "abc123def"
            assert result.ref.startswith("tracecat-sync-")

            # Verify PR was created
            mock_repo.create_pull.assert_called_once()

    @pytest.mark.anyio
    async def test_push_workflows_empty_objects(self, workflow_sync_service, git_url):
        """Test push fails with empty objects list."""
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(message="Update workflows", author=author)

        with pytest.raises(
            ValueError, match="At least one workflow object is required"
        ):
            await workflow_sync_service.push(objects=[], url=git_url, options=options)

    @pytest.mark.anyio
    async def test_push_legacy_rejects_multiple_objects(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test legacy publish mode rejects multi-object pushes."""
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(message="Update workflows", author=author)
        push_objects = [
            PushObject(data=sample_remote_workflow, path="workflows/test-workflow.yml"),
            PushObject(
                data=sample_remote_workflow, path="workflows/test-workflow-2.yml"
            ),
        ]

        mock_repo = Mock()
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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service
            mock_to_thread.side_effect = _mock_to_thread

            with pytest.raises(
                ValueError,
                match="Legacy publish mode only supports pushing one workflow object at a time",
            ):
                await workflow_sync_service.push(
                    objects=push_objects, url=git_url, options=options
                )

    @pytest.mark.anyio
    async def test_push_workflows_github_failure(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test push handles GitHub API failures."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(message="Update workflows", author=author)

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
                    objects=[push_obj], url=git_url, options=options
                )

    @pytest.mark.anyio
    async def test_filename_generation_backward_compatibility(
        self, workflow_sync_service
    ):
        """Test workflow filename generation from title (backward compatibility)."""
        # Test with normal title
        workflow1 = DSLInput(
            title="My Test Workflow",
            description="Test",
            entrypoint=DSLEntrypoint(ref="start", expects={}),
            actions=[ActionStatement(ref="start", action="core.noop", args={})],
        )

        # Create remote workflow definition
        remote_workflow = RemoteWorkflowDefinition(
            id="wf_test123",
            alias="my-test-workflow",
            definition=workflow1,
        )

        # Use title-based path for backward compatibility test
        push_obj = PushObject(
            data=remote_workflow, path="workflows/my-test-workflow.yaml"
        )

        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(message="Test", author=author)
        git_url = GitUrl(host="github.com", org="test", repo="test")

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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            # Mock asyncio.to_thread to return the direct result (not coroutine)
            async def mock_to_thread_impl(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = mock_to_thread_impl

            await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            # Verify the file was created with sanitized filename (backward compatibility)
            mock_repo.create_file.assert_called_once()
            call_args = mock_repo.create_file.call_args
            file_path = call_args.kwargs["path"]
            assert file_path == "workflows/my-test-workflow.yaml"

    @pytest.mark.anyio
    async def test_push_target_branch_commits_to_existing_branch(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test branch-target mode commits directly to an existing branch."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=False,
            branch="feature/shared-workflow",
        )

        mock_repo, branch_ref, _, new_commit = _create_target_branch_repo(
            branch_name="feature/shared-workflow",
            existing_file_contents={},
            new_commit_sha="target123",
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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == new_commit.sha
            assert result.ref == "feature/shared-workflow"
            assert result.base_ref == "main"
            assert result.pr_url is None
            assert result.pr_number is None
            assert result.pr_reused is False
            assert result.object_results is not None
            mock_repo.create_git_ref.assert_not_called()
            assert mock_repo.get_git_ref.call_args_list == [
                call("heads/feature/shared-workflow"),
                call("heads/feature/shared-workflow"),
            ]
            mock_repo.create_git_tree.assert_called_once()
            mock_repo.create_git_commit.assert_called_once()
            branch_ref.edit.assert_called_once_with(new_commit.sha)

            object_results = result.object_results or []
            assert len(object_results) == 1
            assert object_results[0].path == "workflows/test-workflow.yml"
            assert object_results[0].status == PushStatus.COMMITTED

            tree_elements = mock_repo.create_git_tree.call_args.args[0]
            assert len(tree_elements) == 1
            assert _tree_element_path(tree_elements[0]) == "workflows/test-workflow.yml"

    @pytest.mark.anyio
    async def test_push_target_branch_creates_missing_branch_from_base(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test branch-target mode creates target branch when it is missing."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=False,
            branch="feature/new-workflow",
        )

        mock_repo, branch_ref, _, new_commit = _create_target_branch_repo(
            branch_name="feature/new-workflow",
            branch_exists=False,
            existing_file_contents={},
            new_commit_sha="target123",
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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == new_commit.sha
            assert result.ref == "feature/new-workflow"
            assert result.base_ref == "main"
            mock_repo.create_git_ref.assert_called_once_with(
                ref="refs/heads/feature/new-workflow",
                sha="base123",
            )
            mock_repo.create_git_tree.assert_called_once()
            branch_ref.edit.assert_called_once_with(new_commit.sha)

    @pytest.mark.anyio
    async def test_push_target_branch_defaults_base_to_url_ref(
        self, workflow_sync_service, sample_remote_workflow
    ):
        """Test branch-target mode uses URL ref as base when pr_base_branch is unset."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=False,
            branch="feature/new-workflow",
        )
        git_url = GitUrl(
            host="github.com", org="test-org", repo="test-repo", ref="release"
        )

        mock_repo, _, _, new_commit = _create_target_branch_repo(
            branch_name="feature/new-workflow",
            base_branch_name="release",
            existing_file_contents={},
            base_commit_sha="release123",
            new_commit_sha="target123",
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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == new_commit.sha
            assert result.base_ref == "release"
            mock_repo.create_git_ref.assert_not_called()

    @pytest.mark.anyio
    async def test_push_target_branch_noop_returns_no_op(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test branch-target mode returns no_op on identical file contents."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=False,
            branch="feature/shared-workflow",
        )

        expected_yaml = yaml.dump(
            sample_remote_workflow.model_dump(
                mode="json", exclude_none=True, exclude_unset=True
            ),
            sort_keys=False,
        )

        mock_repo, _, _, _ = _create_target_branch_repo(
            branch_name="feature/shared-workflow",
            existing_file_contents={
                "workflows/test-workflow.yml": expected_yaml,
            },
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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.NO_OP
            assert result.sha is None
            assert result.ref == "feature/shared-workflow"
            assert result.base_ref == "main"
            assert result.object_results is not None
            assert len(result.object_results) == 1
            assert result.object_results[0].status == PushStatus.NO_OP
            assert mock_repo.get_git_ref.call_args_list == [
                call("heads/feature/shared-workflow"),
                call("heads/feature/shared-workflow"),
            ]
            mock_repo.get_git_commit.assert_not_called()
            mock_repo.create_git_tree.assert_not_called()
            mock_repo.create_git_commit.assert_not_called()

    @pytest.mark.anyio
    async def test_push_target_branch_noop_rejects_concurrent_branch_changes(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Target-branch no-op pushes should fail if the branch advances mid-prepare."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=False,
            branch="feature/shared-workflow",
        )

        expected_yaml = yaml.dump(
            sample_remote_workflow.model_dump(
                mode="json", exclude_none=True, exclude_unset=True
            ),
            sort_keys=False,
        )

        mock_repo, _, _, _ = _create_target_branch_repo(
            branch_name="feature/shared-workflow",
            existing_file_contents={
                "workflows/test-workflow.yml": expected_yaml,
            },
        )
        initial_ref = Mock()
        initial_ref.object.sha = "snapshot123"
        updated_ref = Mock()
        updated_ref.object.sha = "snapshot456"
        mock_repo.get_git_ref.side_effect = [initial_ref, updated_ref]

        with pytest.raises(
            TracecatValidationError,
            match="changed while preparing the bulk push",
        ):
            with patch("asyncio.to_thread") as mock_to_thread:
                mock_to_thread.side_effect = _mock_to_thread
                await workflow_sync_service._push_to_target_branch(
                    repo=mock_repo,
                    url=git_url,
                    objects=[push_obj],
                    options=options,
                )

        mock_repo.get_git_commit.assert_not_called()
        mock_repo.create_git_tree.assert_not_called()
        mock_repo.create_git_commit.assert_not_called()

    @pytest.mark.anyio
    async def test_push_empty_branch_still_uses_target_branch_mode(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test empty-string branch does not fall back to legacy mode."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=False,
            branch="",
        )

        mock_repo = Mock()
        mock_github_client = Mock()
        mock_github_client.get_repo.return_value = mock_repo
        mock_github_client.close = Mock()

        target_mode_result = Mock()

        with (
            patch(
                "tracecat.workflow.store.sync.GitHubAppService"
            ) as mock_gh_service_class,
            patch("asyncio.to_thread") as mock_to_thread,
            patch.object(
                workflow_sync_service,
                "_push_to_target_branch",
                new=AsyncMock(return_value=target_mode_result),
            ) as mock_target_mode,
            patch.object(
                workflow_sync_service,
                "_push_legacy",
                new=AsyncMock(return_value=Mock()),
            ) as mock_legacy_mode,
        ):
            mock_gh_service = AsyncMock()
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result is target_mode_result
            mock_target_mode.assert_awaited_once()
            mock_legacy_mode.assert_not_called()

    @pytest.mark.anyio
    async def test_push_target_branch_create_pr_failure_returns_committed_result(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test successful commits are returned even when PR creation fails."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=True,
            branch="feature/shared-workflow",
        )

        mock_repo, _, _, new_commit = _create_target_branch_repo(
            branch_name="feature/shared-workflow",
            existing_file_contents={},
            new_commit_sha="target123",
        )

        mock_github_client = Mock()
        mock_github_client.get_repo.return_value = mock_repo
        mock_github_client.close = Mock()

        with (
            patch(
                "tracecat.workflow.store.sync.GitHubAppService"
            ) as mock_gh_service_class,
            patch("asyncio.to_thread") as mock_to_thread,
            patch.object(
                workflow_sync_service,
                "_upsert_pull_request",
                new=AsyncMock(
                    side_effect=GithubException(
                        422, {"message": "Validation Failed"}, {}
                    )
                ),
            ) as mock_upsert_pr,
        ):
            mock_gh_service = AsyncMock()
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == new_commit.sha
            assert result.pr_url is None
            assert result.pr_number is None
            assert result.pr_reused is False
            mock_upsert_pr.assert_awaited_once()
            mock_repo.create_git_tree.assert_called_once()
            mock_repo.create_git_commit.assert_called_once()

    @pytest.mark.anyio
    async def test_push_target_branch_rejects_pr_to_same_branch(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Target-branch pushes should not commit directly to the PR base branch."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=True,
            branch="main",
        )

        mock_repo, _, _, _ = _create_target_branch_repo(
            branch_name="main",
            base_branch_name="main",
        )

        with pytest.raises(
            TracecatValidationError,
            match="branch must differ from the PR base branch",
        ):
            with patch("asyncio.to_thread") as mock_to_thread:
                mock_to_thread.side_effect = _mock_to_thread
                await workflow_sync_service._push_to_target_branch(
                    repo=mock_repo,
                    url=git_url,
                    objects=[push_obj],
                    options=options,
                )

        mock_repo.create_git_tree.assert_not_called()
        mock_repo.create_git_commit.assert_not_called()

    @pytest.mark.anyio
    async def test_push_target_branch_rejects_concurrent_branch_changes(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Target-branch pushes should fail if the branch advances mid-prepare."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=False,
            branch="feature/shared-workflow",
        )

        mock_repo, _, _, _ = _create_target_branch_repo(
            branch_name="feature/shared-workflow",
        )
        initial_ref = Mock()
        initial_ref.object.sha = "snapshot123"
        updated_ref = Mock()
        updated_ref.object.sha = "snapshot456"
        mock_repo.get_git_ref.side_effect = [initial_ref, updated_ref]

        with pytest.raises(
            TracecatValidationError,
            match="changed while preparing the bulk push",
        ):
            with patch("asyncio.to_thread") as mock_to_thread:
                mock_to_thread.side_effect = _mock_to_thread
                await workflow_sync_service._push_to_target_branch(
                    repo=mock_repo,
                    url=git_url,
                    objects=[push_obj],
                    options=options,
                )

        mock_repo.create_git_tree.assert_not_called()
        mock_repo.create_git_commit.assert_not_called()

    @pytest.mark.anyio
    async def test_push_target_branch_noop_pr_failure_returns_no_op_result(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test no-op publish still succeeds when PR creation fails."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=True,
            branch="feature/shared-workflow",
        )

        expected_yaml = yaml.dump(
            sample_remote_workflow.model_dump(
                mode="json", exclude_none=True, exclude_unset=True
            ),
            sort_keys=False,
        )

        mock_repo, _, _, _ = _create_target_branch_repo(
            branch_name="feature/shared-workflow",
            existing_file_contents={
                "workflows/test-workflow.yml": expected_yaml,
            },
        )

        mock_github_client = Mock()
        mock_github_client.get_repo.return_value = mock_repo
        mock_github_client.close = Mock()

        with (
            patch(
                "tracecat.workflow.store.sync.GitHubAppService"
            ) as mock_gh_service_class,
            patch("asyncio.to_thread") as mock_to_thread,
            patch.object(
                workflow_sync_service,
                "_upsert_pull_request",
                new=AsyncMock(
                    side_effect=GithubException(
                        422, {"message": "Validation Failed"}, {}
                    )
                ),
            ) as mock_upsert_pr,
        ):
            mock_gh_service = AsyncMock()
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.NO_OP
            assert result.sha is None
            assert result.pr_url is None
            assert result.pr_number is None
            assert result.pr_reused is False
            mock_upsert_pr.assert_awaited_once()
            assert mock_repo.get_git_ref.call_args_list == [
                call("heads/feature/shared-workflow"),
                call("heads/feature/shared-workflow"),
            ]
            mock_repo.get_git_commit.assert_not_called()
            mock_repo.create_git_tree.assert_not_called()
            mock_repo.create_git_commit.assert_not_called()

    @pytest.mark.anyio
    async def test_push_target_branch_create_pr_creates_new_pull_request(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test branch-target mode creates a PR when requested and none exists."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=True,
            branch="feature/shared-workflow",
        )

        mock_repo, _, _, new_commit = _create_target_branch_repo(
            branch_name="feature/shared-workflow",
            existing_file_contents={},
            new_commit_sha="target123",
        )

        mock_pr = Mock()
        mock_pr.number = 456
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/456"
        mock_repo.create_pull.return_value = mock_pr

        mock_github_client = Mock()
        mock_github_client.get_repo.return_value = mock_repo
        mock_github_client.close = Mock()

        with (
            patch(
                "tracecat.workflow.store.sync.GitHubAppService"
            ) as mock_gh_service_class,
            patch("asyncio.to_thread") as mock_to_thread,
            patch(
                "tracecat.workflow.store.sync.WorkspaceService"
            ) as mock_ws_service_class,
        ):
            mock_gh_service = AsyncMock()
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            mock_ws_service = AsyncMock()
            mock_workspace = Mock()
            mock_workspace.name = "Test Workspace"
            mock_ws_service.get_workspace.return_value = mock_workspace
            mock_ws_service_class.return_value = mock_ws_service

            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == new_commit.sha
            assert result.pr_reused is False
            assert result.pr_number == 456
            assert result.pr_url == "https://github.com/test-org/test-repo/pull/456"
            mock_repo.create_pull.assert_called_once()

    @pytest.mark.anyio
    async def test_push_target_branch_create_pr_reuses_existing_pull_request(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test branch-target mode reuses existing open PR for same head/base."""
        push_obj = PushObject(
            data=sample_remote_workflow, path="workflows/test-workflow.yml"
        )
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Update workflows",
            author=author,
            create_pr=True,
            branch="feature/shared-workflow",
        )

        mock_repo, _, _, new_commit = _create_target_branch_repo(
            branch_name="feature/shared-workflow",
            existing_file_contents={},
            new_commit_sha="target123",
        )

        existing_pr = Mock()
        existing_pr.number = 789
        existing_pr.html_url = "https://github.com/test-org/test-repo/pull/789"
        mock_repo.get_pulls.return_value = [existing_pr]
        mock_repo.create_pull = Mock()

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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=[push_obj], url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == new_commit.sha
            assert result.pr_reused is True
            assert result.pr_number == 789
            assert result.pr_url == "https://github.com/test-org/test-repo/pull/789"
            mock_repo.create_pull.assert_not_called()

    @pytest.mark.anyio
    async def test_push_target_branch_multiple_objects_uses_single_commit(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test branch-target mode batches multiple workflow files into one commit."""
        second_workflow = RemoteWorkflowDefinition(
            id="wf_456def",
            alias="another-workflow",
            definition=sample_remote_workflow.definition.model_copy(
                update={"title": "Another workflow"}
            ),
        )
        push_objects = [
            PushObject(
                data=sample_remote_workflow, path="workflows/wf_123abc/definition.yml"
            ),
            PushObject(data=second_workflow, path="workflows/wf_456def/definition.yml"),
        ]
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Push multiple workflows",
            author=author,
            create_pr=False,
            branch="feature/bulk-push",
        )

        mock_repo, branch_ref, _, new_commit = _create_target_branch_repo(
            branch_name="feature/bulk-push",
            existing_file_contents={},
            new_commit_sha="bulk-commit123",
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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service
            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=push_objects, url=git_url, options=options
            )

            assert result.status == PushStatus.COMMITTED
            assert result.sha == new_commit.sha
            assert result.message == "Committed changes for 2 workflow file(s)."
            assert result.object_results is not None
            assert [
                object_result.status for object_result in result.object_results
            ] == [
                PushStatus.COMMITTED,
                PushStatus.COMMITTED,
            ]

            assert mock_repo.get_git_ref.call_args_list == [
                call("heads/feature/bulk-push"),
                call("heads/feature/bulk-push"),
            ]
            mock_repo.create_git_tree.assert_called_once()
            mock_repo.create_git_commit.assert_called_once()
            branch_ref.edit.assert_called_once_with(new_commit.sha)

            tree_elements = mock_repo.create_git_tree.call_args.args[0]
            assert len(tree_elements) == 2
            assert {_tree_element_path(element) for element in tree_elements} == {
                "workflows/wf_123abc/definition.yml",
                "workflows/wf_456def/definition.yml",
            }

    @pytest.mark.anyio
    async def test_push_target_branch_multiple_objects_returns_no_op_when_unchanged(
        self, workflow_sync_service, git_url, sample_remote_workflow
    ):
        """Test branch-target mode skips commit creation when all workflow files match."""
        second_workflow = RemoteWorkflowDefinition(
            id="wf_456def",
            alias="another-workflow",
            definition=sample_remote_workflow.definition.model_copy(
                update={"title": "Another workflow"}
            ),
        )
        push_objects = [
            PushObject(
                data=sample_remote_workflow, path="workflows/wf_123abc/definition.yml"
            ),
            PushObject(data=second_workflow, path="workflows/wf_456def/definition.yml"),
        ]
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(
            message="Push multiple workflows",
            author=author,
            create_pr=False,
            branch="feature/bulk-push",
        )

        first_yaml = yaml.dump(
            sample_remote_workflow.model_dump(
                mode="json", exclude_none=True, exclude_unset=True
            ),
            sort_keys=False,
        )
        second_yaml = yaml.dump(
            second_workflow.model_dump(
                mode="json", exclude_none=True, exclude_unset=True
            ),
            sort_keys=False,
        )

        mock_repo, _, _, _ = _create_target_branch_repo(
            branch_name="feature/bulk-push",
            existing_file_contents={
                "workflows/wf_123abc/definition.yml": first_yaml,
                "workflows/wf_456def/definition.yml": second_yaml,
            },
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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service
            mock_to_thread.side_effect = _mock_to_thread

            result = await workflow_sync_service.push(
                objects=push_objects, url=git_url, options=options
            )

            assert result.status == PushStatus.NO_OP
            assert result.sha is None
            assert result.message == "No changes detected; nothing to commit."
            assert result.object_results is not None
            assert [
                object_result.status for object_result in result.object_results
            ] == [
                PushStatus.NO_OP,
                PushStatus.NO_OP,
            ]
            assert mock_repo.get_git_ref.call_args_list == [
                call("heads/feature/bulk-push"),
                call("heads/feature/bulk-push"),
            ]
            mock_repo.get_git_commit.assert_not_called()
            mock_repo.create_git_tree.assert_not_called()
            mock_repo.create_git_commit.assert_not_called()

    @pytest.mark.anyio
    async def test_list_branches_success(self, workflow_sync_service, git_url):
        """Test branch listing from GitHub repository."""
        mock_repo = Mock()
        mock_repo.default_branch = "main"

        main_branch = Mock()
        main_branch.name = "main"
        main_branch.commit.sha = "a" * 40
        feature_branch = Mock()
        feature_branch.name = "feature/workflow-sync"
        feature_branch.commit.sha = "b" * 40
        mock_repo.get_branches.return_value = [main_branch, feature_branch]

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
            mock_gh_service.get_github_client_for_repo = AsyncMock(
                return_value=mock_github_client
            )
            mock_gh_service_class.return_value = mock_gh_service

            async def mock_to_thread_impl(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = mock_to_thread_impl

            branches = await workflow_sync_service.list_branches(url=git_url, limit=10)

        assert len(branches) == 2
        assert branches[0].name == "main"
        assert branches[0].sha == "a" * 40
        assert branches[0].is_default is True
        assert branches[1].name == "feature/workflow-sync"
        assert branches[1].sha == "b" * 40
        assert branches[1].is_default is False


class TestWorkflowImportServiceFolders:
    """Tests for WorkflowImportService folder functionality."""

    @pytest.mark.anyio
    async def test_ensure_folder_exists_creates_nested_folders(
        self, workflow_import_service
    ):
        """Test that _ensure_folder_exists creates nested folder structure."""
        # Mock folder service
        mock_folder_service = AsyncMock()
        workflow_import_service.folder_service = mock_folder_service

        # Mock that no folders exist initially
        mock_folder_service.get_folder_by_path.side_effect = [
            None,  # /security/ doesn't exist
            None,  # /security/detections/ doesn't exist
            Mock(id=uuid.uuid4()),  # final folder exists after creation
        ]

        # Mock folder creation
        mock_security_folder = Mock(id=uuid.uuid4())
        mock_detections_folder = Mock(id=uuid.uuid4())
        mock_folder_service.create_folder.side_effect = [
            mock_security_folder,
            mock_detections_folder,
        ]

        await workflow_import_service._ensure_folder_exists("/security/detections/")

        # Verify folders were created in correct order
        assert mock_folder_service.create_folder.call_count == 2

        # First call creates 'security' folder at root
        first_call = mock_folder_service.create_folder.call_args_list[0]
        assert first_call.kwargs["name"] == "security"
        assert first_call.kwargs["parent_path"] == "/"

        # Second call creates 'detections' folder under /security/
        second_call = mock_folder_service.create_folder.call_args_list[1]
        assert second_call.kwargs["name"] == "detections"
        assert second_call.kwargs["parent_path"] == "/security/"

        # Final get_folder_by_path call to return created folder
        mock_folder_service.get_folder_by_path.assert_called_with(
            "/security/detections/"
        )

    @pytest.mark.anyio
    async def test_ensure_folder_exists_with_existing_folders(
        self, workflow_import_service
    ):
        """Test that _ensure_folder_exists handles existing folders."""
        # Mock folder service
        mock_folder_service = AsyncMock()
        workflow_import_service.folder_service = mock_folder_service

        # Mock that security folder exists but detections doesn't
        mock_security_folder = Mock(id=uuid.uuid4())
        mock_detections_folder = Mock(id=uuid.uuid4())

        mock_folder_service.get_folder_by_path.side_effect = [
            mock_security_folder,  # /security/ exists
            None,  # /security/detections/ doesn't exist
            mock_detections_folder,  # final folder exists after creation
        ]

        mock_folder_service.create_folder.return_value = mock_detections_folder

        await workflow_import_service._ensure_folder_exists("/security/detections/")

        # Verify only detections folder was created
        assert mock_folder_service.create_folder.call_count == 1
        call_args = mock_folder_service.create_folder.call_args
        assert call_args.kwargs["name"] == "detections"
        assert call_args.kwargs["parent_path"] == "/security/"

    @pytest.mark.anyio
    async def test_create_new_workflow_with_folder_path(
        self, workflow_import_service, sample_remote_workflow_with_folder
    ):
        """Test creating a new workflow with folder_path sets folder_id."""
        # Mock dependencies
        mock_wf_mgmt = AsyncMock()
        mock_workflow = Mock()
        mock_workflow.id = uuid.uuid4()
        mock_wf_mgmt.create_db_workflow_from_dsl.return_value = mock_workflow
        workflow_import_service.wf_mgmt = mock_wf_mgmt

        mock_defn_service = AsyncMock()
        mock_defn = Mock(version=1)
        mock_defn_service.create_workflow_definition.return_value = mock_defn

        # Mock session and flush
        workflow_import_service.session.flush = AsyncMock()

        # Mock folder creation
        test_folder_id = uuid.uuid4()
        workflow_import_service._ensure_folder_exists = AsyncMock(
            return_value=test_folder_id
        )
        workflow_import_service._create_schedules = AsyncMock()
        workflow_import_service._update_webhook = AsyncMock()
        workflow_import_service._update_case_trigger = AsyncMock()
        workflow_import_service._create_tags = AsyncMock()

        with patch(
            "tracecat.workflow.store.import_service.WorkflowDefinitionsService",
            return_value=mock_defn_service,
        ):
            await workflow_import_service._create_new_workflow(
                sample_remote_workflow_with_folder
            )

        # Verify folder was created and workflow.folder_id was set
        workflow_import_service._ensure_folder_exists.assert_called_once_with(
            "/security/detections/"
        )
        assert mock_workflow.folder_id == test_folder_id

    @pytest.mark.anyio
    async def test_create_new_workflow_without_folder_path(
        self, workflow_import_service, sample_remote_workflow
    ):
        """Test creating a new workflow without folder_path leaves folder_id as None."""
        # Mock dependencies
        mock_wf_mgmt = AsyncMock()
        mock_workflow = Mock()
        mock_workflow.id = uuid.uuid4()
        mock_wf_mgmt.create_db_workflow_from_dsl.return_value = mock_workflow
        workflow_import_service.wf_mgmt = mock_wf_mgmt

        mock_defn_service = AsyncMock()
        mock_defn = Mock(version=1)
        mock_defn_service.create_workflow_definition.return_value = mock_defn

        # Mock session and flush
        workflow_import_service.session.flush = AsyncMock()

        workflow_import_service._ensure_folder_exists = AsyncMock()
        workflow_import_service._create_schedules = AsyncMock()
        workflow_import_service._update_webhook = AsyncMock()
        workflow_import_service._update_case_trigger = AsyncMock()
        workflow_import_service._create_tags = AsyncMock()

        with patch(
            "tracecat.workflow.store.import_service.WorkflowDefinitionsService",
            return_value=mock_defn_service,
        ):
            await workflow_import_service._create_new_workflow(sample_remote_workflow)

        # Verify folder creation was not called and folder_id was not set
        workflow_import_service._ensure_folder_exists.assert_not_called()
        # folder_id should not be set (remains None by default)
