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
        assert "invite" in result.stdout

    def test_list_users_requires_service_key(self) -> None:
        """Test list-users requires service key."""
        result = runner.invoke(app, ["admin", "list-users"])
        assert result.exit_code == 1
        assert (
            "TRACECAT__SERVICE_KEY" in result.output
            or "Not authenticated" in result.output
        )


class TestInviteCommands:
    """Test invite command group."""

    def test_invite_help(self) -> None:
        """Test invite help."""
        result = runner.invoke(app, ["admin", "invite", "--help"])
        assert result.exit_code == 0
        assert "org" in result.stdout

    def test_invite_org_help(self) -> None:
        """Test invite org help."""
        result = runner.invoke(app, ["admin", "invite", "org", "--help"])
        assert result.exit_code == 0
        assert "--email" in result.stdout
        assert "--role" in result.stdout
        assert "--org-name" in result.stdout
        assert "--org-slug" in result.stdout

    def test_invite_org_requires_email(self) -> None:
        """Test invite org requires email."""
        result = runner.invoke(app, ["admin", "invite", "org"])
        assert result.exit_code == 2
        assert "Missing option" in result.output or "--email" in result.output

    def test_invite_org_validates_role(self) -> None:
        """Test invite org validates role."""
        result = runner.invoke(
            app,
            [
                "admin",
                "invite",
                "org",
                "--email",
                "test@example.com",
                "--role",
                "invalid",
            ],
        )
        assert result.exit_code == 1
        assert "Invalid role" in result.output

    def test_invite_org_requires_auth(self) -> None:
        """Test invite org requires authentication."""
        result = runner.invoke(
            app,
            [
                "admin",
                "invite",
                "org",
                "--email",
                "test@example.com",
                "--role",
                "admin",
            ],
        )
        assert result.exit_code == 1
        assert (
            "TRACECAT__SERVICE_KEY" in result.output
            or "Not authenticated" in result.output
        )


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
