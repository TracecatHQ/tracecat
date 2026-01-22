"""Mock tests for registry commands."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import respx
from httpx import Response
from tracecat_admin.cli import app
from typer.testing import CliRunner

from .conftest import API_URL

runner = CliRunner()


@pytest.fixture
def sample_status() -> dict:
    """Sample registry status data."""
    return {
        "total_repositories": 2,
        "last_sync_at": datetime.now(UTC).isoformat(),
        "repositories": [
            {
                "id": str(uuid.uuid4()),
                "name": "tracecat-registry",
                "origin": "https://github.com/TracecatHQ/tracecat",
                "last_synced_at": datetime.now(UTC).isoformat(),
                "commit_sha": "abc123def456",
            },
            {
                "id": str(uuid.uuid4()),
                "name": "custom-actions",
                "origin": "https://github.com/example/custom",
                "last_synced_at": None,
                "commit_sha": None,
            },
        ],
    }


@pytest.fixture
def sample_sync_result() -> dict:
    """Sample sync result data."""
    return {
        "success": True,
        "synced_at": datetime.now(UTC).isoformat(),
        "repositories": [
            {
                "repository_id": str(uuid.uuid4()),
                "repository_name": "tracecat-registry",
                "success": True,
                "error": None,
                "version": "1.0.0",
                "actions_count": 42,
            },
        ],
    }


@pytest.fixture
def sample_versions() -> list[dict]:
    """Sample registry versions."""
    repo_id = str(uuid.uuid4())
    return [
        {
            "id": str(uuid.uuid4()),
            "repository_id": repo_id,
            "version": "1.0.0",
            "commit_sha": "abc123",
            "tarball_uri": "s3://bucket/v1.0.0.tar.gz",
            "created_at": datetime.now(UTC).isoformat(),
        },
        {
            "id": str(uuid.uuid4()),
            "repository_id": repo_id,
            "version": "0.9.0",
            "commit_sha": "def456",
            "tarball_uri": "s3://bucket/v0.9.0.tar.gz",
            "created_at": datetime.now(UTC).isoformat(),
        },
    ]


class TestRegistryStatus:
    """Tests for registry status command."""

    @respx.mock
    def test_status_success(self, mock_env: None, sample_status: dict) -> None:
        """Test successful status retrieval."""
        respx.get(f"{API_URL}/admin/registry/status").mock(
            return_value=Response(200, json=sample_status)
        )

        result = runner.invoke(app, ["registry", "status"])

        assert result.exit_code == 0
        assert "Total repositories: 2" in result.stdout
        assert "tracecat-registry" in result.stdout

    @respx.mock
    def test_status_json_output(self, mock_env: None, sample_status: dict) -> None:
        """Test JSON output format."""
        respx.get(f"{API_URL}/admin/registry/status").mock(
            return_value=Response(200, json=sample_status)
        )

        result = runner.invoke(app, ["registry", "status", "--json"])

        assert result.exit_code == 0
        assert '"total_repositories": 2' in result.stdout


class TestRegistrySync:
    """Tests for registry sync command."""

    @respx.mock
    def test_sync_all_success(self, mock_env: None, sample_sync_result: dict) -> None:
        """Test successful sync of all repositories."""
        respx.post(f"{API_URL}/admin/registry/sync").mock(
            return_value=Response(200, json=sample_sync_result)
        )

        result = runner.invoke(app, ["registry", "sync"])

        assert result.exit_code == 0
        assert "Success" in result.stdout
        assert "tracecat-registry" in result.stdout

    @respx.mock
    def test_sync_specific_repo(self, mock_env: None, sample_sync_result: dict) -> None:
        """Test sync of specific repository."""
        repo_id = str(uuid.uuid4())
        respx.post(f"{API_URL}/admin/registry/sync/{repo_id}").mock(
            return_value=Response(200, json=sample_sync_result)
        )

        result = runner.invoke(app, ["registry", "sync", "--repository-id", repo_id])

        assert result.exit_code == 0
        assert "Success" in result.stdout

    @respx.mock
    def test_sync_failure(self, mock_env: None) -> None:
        """Test sync failure response."""
        failed_result = {
            "success": False,
            "synced_at": datetime.now(UTC).isoformat(),
            "repositories": [
                {
                    "repository_id": str(uuid.uuid4()),
                    "repository_name": "broken-repo",
                    "success": False,
                    "error": "Failed to clone repository",
                    "version": None,
                    "actions_count": None,
                },
            ],
        }
        respx.post(f"{API_URL}/admin/registry/sync").mock(
            return_value=Response(200, json=failed_result)
        )

        result = runner.invoke(app, ["registry", "sync"])

        assert result.exit_code == 0
        assert "Failed" in result.stdout
        assert "Failed to clone repository" in result.stdout


class TestRegistryVersions:
    """Tests for registry versions command."""

    @respx.mock
    def test_versions_success(
        self, mock_env: None, sample_versions: list[dict]
    ) -> None:
        """Test successful versions listing."""
        respx.get(f"{API_URL}/admin/registry/versions").mock(
            return_value=Response(200, json=sample_versions)
        )

        result = runner.invoke(app, ["registry", "versions"])

        assert result.exit_code == 0
        assert "1.0.0" in result.stdout
        assert "0.9.0" in result.stdout

    @respx.mock
    def test_versions_with_limit(
        self, mock_env: None, sample_versions: list[dict]
    ) -> None:
        """Test versions with limit parameter."""
        respx.get(f"{API_URL}/admin/registry/versions").mock(
            return_value=Response(200, json=sample_versions[:1])
        )

        result = runner.invoke(app, ["registry", "versions", "--limit", "1"])

        assert result.exit_code == 0
        assert "1.0.0" in result.stdout

    @respx.mock
    def test_versions_empty(self, mock_env: None) -> None:
        """Test empty versions list."""
        respx.get(f"{API_URL}/admin/registry/versions").mock(
            return_value=Response(200, json=[])
        )

        result = runner.invoke(app, ["registry", "versions"])

        assert result.exit_code == 0
        assert "No versions found" in result.stdout

    @respx.mock
    def test_versions_json_output(
        self, mock_env: None, sample_versions: list[dict]
    ) -> None:
        """Test JSON output format."""
        respx.get(f"{API_URL}/admin/registry/versions").mock(
            return_value=Response(200, json=sample_versions)
        )

        result = runner.invoke(app, ["registry", "versions", "--json"])

        assert result.exit_code == 0
        assert '"version": "1.0.0"' in result.stdout
