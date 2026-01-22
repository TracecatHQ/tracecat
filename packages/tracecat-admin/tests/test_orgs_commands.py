"""Mock tests for organization commands."""

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
def sample_orgs() -> list[dict]:
    """Sample organization data."""
    return [
        {
            "id": str(uuid.uuid4()),
            "name": "Acme Corp",
            "slug": "acme-corp",
            "is_active": True,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Test Org",
            "slug": "test-org",
            "is_active": True,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": None,
        },
    ]


class TestListOrgs:
    """Tests for orgs list command."""

    @respx.mock
    def test_list_orgs_success(self, mock_env: None, sample_orgs: list[dict]) -> None:
        """Test successful organization listing."""
        respx.get(f"{API_URL}/admin/organizations").mock(
            return_value=Response(200, json=sample_orgs)
        )

        result = runner.invoke(app, ["orgs", "list"])

        assert result.exit_code == 0
        assert "Acme Corp" in result.stdout
        assert "acme-corp" in result.stdout

    @respx.mock
    def test_list_orgs_json_output(
        self, mock_env: None, sample_orgs: list[dict]
    ) -> None:
        """Test JSON output format."""
        respx.get(f"{API_URL}/admin/organizations").mock(
            return_value=Response(200, json=sample_orgs)
        )

        result = runner.invoke(app, ["orgs", "list", "--json"])

        assert result.exit_code == 0
        assert '"name": "Acme Corp"' in result.stdout
        assert '"slug": "acme-corp"' in result.stdout

    @respx.mock
    def test_list_orgs_empty(self, mock_env: None) -> None:
        """Test empty organization list."""
        respx.get(f"{API_URL}/admin/organizations").mock(
            return_value=Response(200, json=[])
        )

        result = runner.invoke(app, ["orgs", "list"])

        assert result.exit_code == 0
        assert "No organizations found" in result.stdout


class TestCreateOrg:
    """Tests for orgs create command."""

    @respx.mock
    def test_create_org_success(self, mock_env: None) -> None:
        """Test successful organization creation."""
        new_org = {
            "id": str(uuid.uuid4()),
            "name": "New Org",
            "slug": "new-org",
            "is_active": True,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": None,
        }

        respx.post(f"{API_URL}/admin/organizations").mock(
            return_value=Response(201, json=new_org)
        )

        result = runner.invoke(
            app, ["orgs", "create", "--name", "New Org", "--slug", "new-org"]
        )

        assert result.exit_code == 0
        assert "created successfully" in result.stdout
        assert "New Org" in result.stdout

    @respx.mock
    def test_create_org_conflict(self, mock_env: None) -> None:
        """Test organization creation with duplicate slug."""
        respx.post(f"{API_URL}/admin/organizations").mock(
            return_value=Response(409, json={"detail": "Organization already exists"})
        )

        result = runner.invoke(
            app, ["orgs", "create", "--name", "Existing Org", "--slug", "existing-org"]
        )

        assert result.exit_code == 1
        assert "Error" in result.stdout


class TestGetOrg:
    """Tests for orgs get command."""

    @respx.mock
    def test_get_org_success(self, mock_env: None, sample_orgs: list[dict]) -> None:
        """Test successful organization retrieval."""
        org = sample_orgs[0]
        org_id = org["id"]

        respx.get(f"{API_URL}/admin/organizations/{org_id}").mock(
            return_value=Response(200, json=org)
        )

        result = runner.invoke(app, ["orgs", "get", org_id])

        assert result.exit_code == 0
        assert "Acme Corp" in result.stdout

    @respx.mock
    def test_get_org_not_found(self, mock_env: None) -> None:
        """Test organization not found."""
        org_id = str(uuid.uuid4())

        respx.get(f"{API_URL}/admin/organizations/{org_id}").mock(
            return_value=Response(
                404, json={"detail": f"Organization {org_id} not found"}
            )
        )

        result = runner.invoke(app, ["orgs", "get", org_id])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    @respx.mock
    def test_get_org_json_output(self, mock_env: None, sample_orgs: list[dict]) -> None:
        """Test JSON output format."""
        org = sample_orgs[0]
        org_id = org["id"]

        respx.get(f"{API_URL}/admin/organizations/{org_id}").mock(
            return_value=Response(200, json=org)
        )

        result = runner.invoke(app, ["orgs", "get", org_id, "--json"])

        assert result.exit_code == 0
        assert '"name": "Acme Corp"' in result.stdout
