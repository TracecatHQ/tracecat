"""Tests for Git workflow synchronization functionality."""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from tracecat_ee.workflows.git_store import GitWorkflowStore
from tracecat_ee.workflows.git_sync import sync_repo_workflows

from tracecat.types.auth import Role
from tracecat.workflows.store import WorkflowSource


@pytest.fixture
def temp_git_repo():
    """Create a temporary directory for Git operations."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def mock_role():
    """Create a mock role for testing."""
    return Role(
        type="service",
        service_id="tracecat-service",
        workspace_id=uuid.uuid4(),
    )


@pytest.fixture
def sample_workflow_yaml():
    """Sample workflow YAML content."""
    return """
title: "Test Workflow"
description: "A test workflow for Git sync"
triggers:
  - type: webhook
    webhook:
      method: POST
actions:
  - ref: "test_action"
    action: "core.http_request"
    args:
      url: "https://httpbin.org/post"
      method: "POST"
"""


@pytest.fixture
def mock_git_env():
    """Mock Git environment variables."""
    return {
        "GIT_SSH_COMMAND": "ssh -o StrictHostKeyChecking=no",
        "SSH_AUTH_SOCK": "/tmp/ssh-agent",
    }


class TestGitWorkflowStore:
    """Test GitWorkflowStore functionality."""

    @pytest.mark.anyio
    async def test_init_repository(
        self, temp_git_repo: Path, mock_git_env: dict[str, str]
    ):
        """Test repository initialization."""
        with patch("tracecat_ee.workflows.git_store.run_git") as mock_run_git:
            # Mock successful git commands
            mock_run_git.return_value = (0, "", "")

            store = GitWorkflowStore(
                repo_url="git+ssh://git@github.com/org/repo.git",
                commit_sha="abc123",
                work_dir=temp_git_repo,
                env=mock_git_env,
            )

            # Test init repository
            await store._init_repository(temp_git_repo)

            # Verify git commands were called
            assert mock_run_git.call_count >= 4  # init, remote add, fetch, checkout

    @pytest.mark.anyio
    async def test_extract_workflow_id(self):
        """Test workflow ID extraction from file paths."""
        store = GitWorkflowStore(
            repo_url="git+ssh://git@github.com/org/repo.git",
            commit_sha="abc123",
        )

        # Test short pattern
        assert store._extract_workflow_id("workflows/wf_ABC123.yml") == "wf_ABC123"

        # Test legacy pattern
        assert (
            store._extract_workflow_id(
                "playbooks/wf-1234567890abcdef1234567890abcdef.yaml"
            )
            == "wf-1234567890abcdef1234567890abcdef"
        )

        # Test no match
        assert store._extract_workflow_id("workflows/invalid.yml") is None

    @pytest.mark.anyio
    async def test_list_sources(
        self, temp_git_repo: Path, mock_git_env: dict[str, str]
    ):
        """Test listing workflow sources from Git repository."""
        with patch("tracecat_ee.workflows.git_store.run_git") as mock_run_git:
            # Mock git ls-tree output
            mock_run_git.side_effect = [
                (0, "", ""),  # init
                (0, "", ""),  # remote add
                (0, "", ""),  # fetch
                (0, "", ""),  # checkout
                (
                    0,
                    "workflows/wf_ABC123.yml\nplaybooks/wf_DEF456.yaml\nREADME.md",
                    "",
                ),  # ls-tree
            ]

            store = GitWorkflowStore(
                repo_url="git+ssh://git@github.com/org/repo.git",
                commit_sha="abc123",
                work_dir=temp_git_repo,
                env=mock_git_env,
            )

            sources = list(await store.list_sources())

            assert len(sources) == 2
            assert sources[0].workflow_id == "wf_ABC123"
            assert sources[0].path == "workflows/wf_ABC123.yml"
            assert sources[1].workflow_id == "wf_DEF456"
            assert sources[1].path == "playbooks/wf_DEF456.yaml"

    @pytest.mark.anyio
    async def test_fetch_yaml(
        self,
        temp_git_repo: Path,
        sample_workflow_yaml: str,
        mock_git_env: dict[str, str],
    ):
        """Test fetching YAML content from Git."""
        with patch("tracecat_ee.workflows.git_store.run_git") as mock_run_git:
            mock_run_git.return_value = (0, sample_workflow_yaml, "")

            store = GitWorkflowStore(
                repo_url="git+ssh://git@github.com/org/repo.git",
                commit_sha="abc123",
                work_dir=temp_git_repo,
                env=mock_git_env,
            )

            content = await store.fetch_yaml("workflows/wf_ABC123.yml", "abc123")

            assert content == sample_workflow_yaml
            mock_run_git.assert_called_with(
                ["git", "show", "abc123:workflows/wf_ABC123.yml"],
                env=mock_git_env,
                cwd=str(temp_git_repo),
                timeout=30.0,
            )


class TestSyncOrchestrator:
    """Test sync orchestrator functionality."""

    @pytest.mark.anyio
    async def test_sync_basic_flow(self, mock_role: Role):
        """Test basic sync flow without database dependencies."""
        workspace_id = str(uuid.uuid4())
        repo_url = "git+ssh://git@github.com/org/repo.git"
        commit_sha = "abc123"

        mock_sources = [
            WorkflowSource(
                path="workflows/wf_ABC123.yml",
                sha=commit_sha,
                workflow_id="wf_ABC123",
            ),
        ]

        with patch("tracecat_ee.workflows.git_sync.resolve_git_ref") as mock_resolve:
            mock_resolve.return_value = commit_sha

            with patch(
                "tracecat_ee.workflows.git_sync.git_env_context"
            ) as mock_env_context:
                mock_env_context.return_value.__aenter__.return_value = {}

                with patch(
                    "tracecat_ee.workflows.git_sync.get_setting_cached"
                ) as mock_settings:
                    mock_settings.return_value = ["github.com"]

                    with patch(
                        "tracecat_ee.workflows.git_sync.pg_advisory_lock"
                    ) as mock_lock:
                        mock_lock.return_value.__aenter__.return_value = None

                        with patch(
                            "tracecat_ee.workflows.git_sync._get_or_create_repo_state"
                        ) as mock_get_state:
                            mock_repo_state = AsyncMock()
                            mock_repo_state.last_synced_sha = None  # Not synced before
                            mock_get_state.return_value = mock_repo_state

                            with patch(
                                "tracecat_ee.workflows.git_sync.GitWorkflowStore"
                            ) as mock_store_class:
                                mock_store = AsyncMock()
                                mock_store.list_sources.return_value = mock_sources
                                mock_store_class.return_value = mock_store

                                with patch(
                                    "tracecat_ee.workflows.git_sync._mirror_delete_workflows"
                                ) as mock_delete:
                                    mock_delete.return_value = 0

                                    with patch(
                                        "tracecat_ee.workflows.git_sync._count_existing_workflows"
                                    ) as mock_count:
                                        mock_count.side_effect = [0, 1]  # before, after

                                        with patch(
                                            "tracecat_ee.workflows.git_sync.upsert_workflow_definitions"
                                        ) as mock_upsert:
                                            mock_session = AsyncMock()

                                            result = await sync_repo_workflows(
                                                session=mock_session,
                                                workspace_id=workspace_id,
                                                repo_url=repo_url,
                                                role=mock_role,
                                            )

                                            assert result["status"] == "synced"
                                            assert result["commit_sha"] == commit_sha
                                            assert "created" in result
                                            assert "updated" in result
                                            assert "deleted" in result

                                            # Verify important calls were made
                                            mock_lock.assert_called_once()
                                            mock_upsert.assert_called_once()


class TestAdvisoryLocks:
    """Test PostgreSQL advisory lock functionality."""

    @pytest.mark.anyio
    async def test_derive_lock_key(self):
        """Test lock key derivation from workspace and repo URL."""
        from tracecat.db.locks import derive_lock_key

        workspace_id = str(uuid.uuid4())
        repo_url = "git+ssh://git@github.com/org/repo.git"

        key1 = derive_lock_key(workspace_id, repo_url)
        key2 = derive_lock_key(workspace_id, repo_url)

        # Should be deterministic
        assert key1 == key2

        # Should be in valid range for PostgreSQL advisory locks
        assert 0 <= key1 < 2**63

        # Different inputs should produce different keys
        key3 = derive_lock_key(workspace_id, "git+ssh://git@github.com/other/repo.git")
        assert key1 != key3

    @pytest.mark.anyio
    async def test_pg_advisory_lock(self):
        """Test PostgreSQL advisory lock context manager."""
        from tracecat.db.locks import pg_advisory_lock

        mock_session = AsyncMock()

        async with pg_advisory_lock(mock_session, 12345):
            # Verify lock was acquired
            assert mock_session.execute.call_count >= 1
            acquire_call = mock_session.execute.call_args_list[0]
            assert "pg_advisory_lock" in str(acquire_call.args[0])

        # Verify lock was released
        release_calls = [
            call
            for call in mock_session.execute.call_args_list
            if "pg_advisory_unlock" in str(call.args[0])
        ]
        assert len(release_calls) >= 1
