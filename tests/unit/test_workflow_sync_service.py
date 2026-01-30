"""Tests for WorkflowSyncService functionality."""

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from github.GithubException import GithubException

from tracecat.auth.types import Role
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.git.types import GitUrl
from tracecat.sync import Author, PushObject, PushOptions
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
            ValueError, match="We only support pushing one workflow object at a time"
        ):
            await workflow_sync_service.push(objects=[], url=git_url, options=options)

    @pytest.mark.anyio
    async def test_push_objects_empty_objects(self, workflow_sync_service, git_url):
        """Test push fails with empty objects list."""
        author = Author(name="Test User", email="test@example.com")
        options = PushOptions(message="Update workflows", author=author)

        with pytest.raises(
            ValueError, match="We only support pushing one workflow object at a time"
        ):
            await workflow_sync_service.push(objects=[], url=git_url, options=options)

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
        workflow_import_service._create_tags = AsyncMock()

        with patch(
            "tracecat.workflow.store.import_service.WorkflowDefinitionsService",
            return_value=mock_defn_service,
        ):
            await workflow_import_service._create_new_workflow(sample_remote_workflow)

        # Verify folder creation was not called and folder_id was not set
        workflow_import_service._ensure_folder_exists.assert_not_called()
        # folder_id should not be set (remains None by default)
