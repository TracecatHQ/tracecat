"""Tests for tracecat-admin CLI."""

from __future__ import annotations

from tracecat_admin.cli import app
from typer.testing import CliRunner

runner = CliRunner()


class TestCLI:
    """Test CLI basic functionality."""

    def test_version(self) -> None:
        """Test version command."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "tracecat-admin version" in result.stdout

    def test_help(self) -> None:
        """Test help command."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "admin" in result.stdout
        assert "orgs" in result.stdout
        assert "registry" in result.stdout
        assert "migrate" in result.stdout


class TestAdminCommands:
    """Test admin command group."""

    def test_admin_help(self) -> None:
        """Test admin help."""
        result = runner.invoke(app, ["admin", "--help"])
        assert result.exit_code == 0
        assert "list-users" in result.stdout
        assert "promote-user" in result.stdout
        assert "demote-user" in result.stdout
        assert "create-superuser" in result.stdout

    def test_list_users_requires_service_key(self) -> None:
        """Test list-users requires service key."""
        result = runner.invoke(app, ["admin", "list-users"])
        assert result.exit_code == 1
        assert "TRACECAT__SERVICE_KEY" in result.stdout


class TestOrgsCommands:
    """Test orgs command group."""

    def test_orgs_help(self) -> None:
        """Test orgs help."""
        result = runner.invoke(app, ["orgs", "--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout
        assert "create" in result.stdout
        assert "get" in result.stdout


class TestRegistryCommands:
    """Test registry command group."""

    def test_registry_help(self) -> None:
        """Test registry help."""
        result = runner.invoke(app, ["registry", "--help"])
        assert result.exit_code == 0
        assert "sync" in result.stdout
        assert "status" in result.stdout
        assert "versions" in result.stdout


class TestMigrateCommands:
    """Test migrate command group."""

    def test_migrate_help(self) -> None:
        """Test migrate help."""
        result = runner.invoke(app, ["migrate", "--help"])
        assert result.exit_code == 0
        assert "upgrade" in result.stdout
        assert "downgrade" in result.stdout
        assert "status" in result.stdout
        assert "history" in result.stdout
