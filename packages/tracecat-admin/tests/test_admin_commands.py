"""Mock tests for admin commands."""

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
def sample_users() -> list[dict]:
    """Sample user data."""
    return [
        {
            "id": str(uuid.uuid4()),
            "email": "admin@example.com",
            "first_name": "Admin",
            "last_name": "User",
            "role": "ADMIN",
            "is_active": True,
            "is_superuser": True,
            "is_verified": True,
            "last_login_at": datetime.now(UTC).isoformat(),
            "created_at": datetime.now(UTC).isoformat(),
        },
        {
            "id": str(uuid.uuid4()),
            "email": "basic@example.com",
            "first_name": "Basic",
            "last_name": "User",
            "role": "BASIC",
            "is_active": True,
            "is_superuser": False,
            "is_verified": True,
            "last_login_at": None,
            "created_at": datetime.now(UTC).isoformat(),
        },
    ]


class TestListUsers:
    """Tests for list-users command."""

    @respx.mock
    def test_list_users_success(self, mock_env: None, sample_users: list[dict]) -> None:
        """Test successful user listing."""
        respx.get(f"{API_URL}/admin/users").mock(
            return_value=Response(200, json=sample_users)
        )

        result = runner.invoke(app, ["admin", "list-users"])

        assert result.exit_code == 0
        assert "admin@example.com" in result.stdout
        assert "basic@example.com" in result.stdout

    @respx.mock
    def test_list_users_json_output(
        self, mock_env: None, sample_users: list[dict]
    ) -> None:
        """Test JSON output format."""
        respx.get(f"{API_URL}/admin/users").mock(
            return_value=Response(200, json=sample_users)
        )

        result = runner.invoke(app, ["admin", "list-users", "--json"])

        assert result.exit_code == 0
        assert '"email": "admin@example.com"' in result.stdout

    @respx.mock
    def test_list_users_empty(self, mock_env: None) -> None:
        """Test empty user list."""
        respx.get(f"{API_URL}/admin/users").mock(return_value=Response(200, json=[]))

        result = runner.invoke(app, ["admin", "list-users"])

        assert result.exit_code == 0
        assert "No users found" in result.stdout

    @respx.mock
    def test_list_users_api_error(self, mock_env: None) -> None:
        """Test API error handling."""
        respx.get(f"{API_URL}/admin/users").mock(
            return_value=Response(500, json={"detail": "Internal server error"})
        )

        result = runner.invoke(app, ["admin", "list-users"])

        assert result.exit_code == 1
        assert "Error" in result.stdout


class TestGetUser:
    """Tests for get-user command."""

    @respx.mock
    def test_get_user_success(self, mock_env: None, sample_users: list[dict]) -> None:
        """Test successful user retrieval."""
        user = sample_users[0]
        user_id = user["id"]

        respx.get(f"{API_URL}/admin/users/{user_id}").mock(
            return_value=Response(200, json=user)
        )

        result = runner.invoke(app, ["admin", "get-user", user_id])

        assert result.exit_code == 0
        assert "admin@example.com" in result.stdout

    @respx.mock
    def test_get_user_not_found(self, mock_env: None) -> None:
        """Test user not found."""
        user_id = str(uuid.uuid4())

        respx.get(f"{API_URL}/admin/users/{user_id}").mock(
            return_value=Response(404, json={"detail": f"User {user_id} not found"})
        )

        result = runner.invoke(app, ["admin", "get-user", user_id])

        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestPromoteUser:
    """Tests for promote-user command."""

    @respx.mock
    def test_promote_user_success(
        self, mock_env: None, sample_users: list[dict]
    ) -> None:
        """Test successful user promotion."""
        user = sample_users[1].copy()  # Basic user
        email = user["email"]

        # Mock list users to find by email
        respx.get(f"{API_URL}/admin/users").mock(
            return_value=Response(200, json=sample_users)
        )

        # Mock promote endpoint
        promoted_user = {**user, "is_superuser": True}
        respx.post(f"{API_URL}/admin/users/{user['id']}/promote").mock(
            return_value=Response(200, json=promoted_user)
        )

        result = runner.invoke(app, ["admin", "promote-user", "--email", email])

        assert result.exit_code == 0
        assert "promoted to superuser" in result.stdout

    @respx.mock
    def test_promote_user_not_found(self, mock_env: None) -> None:
        """Test promoting non-existent user."""
        respx.get(f"{API_URL}/admin/users").mock(return_value=Response(200, json=[]))

        result = runner.invoke(
            app, ["admin", "promote-user", "--email", "nonexistent@example.com"]
        )

        assert result.exit_code == 1
        assert "not found" in result.stdout

    @respx.mock
    def test_promote_already_superuser(
        self, mock_env: None, sample_users: list[dict]
    ) -> None:
        """Test promoting user who is already superuser."""
        respx.get(f"{API_URL}/admin/users").mock(
            return_value=Response(200, json=sample_users)
        )

        # Admin user is already superuser
        result = runner.invoke(
            app, ["admin", "promote-user", "--email", "admin@example.com"]
        )

        assert result.exit_code == 1
        assert "already a superuser" in result.stdout


class TestDemoteUser:
    """Tests for demote-user command."""

    @respx.mock
    def test_demote_user_success(
        self, mock_env: None, sample_users: list[dict]
    ) -> None:
        """Test successful user demotion."""
        user = sample_users[0].copy()  # Admin user (superuser)
        email = user["email"]

        respx.get(f"{API_URL}/admin/users").mock(
            return_value=Response(200, json=sample_users)
        )

        demoted_user = {**user, "is_superuser": False}
        respx.post(f"{API_URL}/admin/users/{user['id']}/demote").mock(
            return_value=Response(200, json=demoted_user)
        )

        result = runner.invoke(app, ["admin", "demote-user", "--email", email])

        assert result.exit_code == 0
        assert "demoted from superuser" in result.stdout

    @respx.mock
    def test_demote_user_not_superuser(
        self, mock_env: None, sample_users: list[dict]
    ) -> None:
        """Test demoting user who is not superuser."""
        respx.get(f"{API_URL}/admin/users").mock(
            return_value=Response(200, json=sample_users)
        )

        # Basic user is not superuser
        result = runner.invoke(
            app, ["admin", "demote-user", "--email", "basic@example.com"]
        )

        assert result.exit_code == 1
        assert "not a superuser" in result.stdout
