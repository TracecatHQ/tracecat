"""Tests for ``tracecat db roles``: a pass-through to ``tracecat.db.roles``."""

from __future__ import annotations

from collections.abc import Sequence
from types import ModuleType

import pytest
from tracecat_admin.cli import app
from tracecat_admin.commands import db
from typer.testing import CliRunner

from tracecat import config

runner = CliRunner()


class _RolesModule(ModuleType):
    """Stands in for tracecat.db.roles and records what it was given."""

    def __init__(self, code: int) -> None:
        super().__init__("tracecat.db.roles")
        self.code = code
        self.calls: list[list[str]] = []

    def main(self, argv: Sequence[str] | None = None) -> int:
        self.calls.append(list(argv or []))
        return self.code


def test_roles_forwards_every_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    """Options the CLI doesn't know (--login, --from, --sql) reach the script."""
    fake = _RolesModule(code=3)
    monkeypatch.setattr(db.importlib, "import_module", lambda name: fake)
    args = ["upgrade", "--login", "aws_iam", "--from", "tracecat"]
    result = runner.invoke(app, ["db", "roles", *args])
    assert fake.calls == [args]
    assert result.exit_code == 3


def test_roles_help_is_the_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RolesModule(code=0)
    monkeypatch.setattr(db.importlib, "import_module", lambda name: fake)
    result = runner.invoke(app, ["db", "roles", "--help"])
    assert fake.calls == [["--help"]]
    assert result.exit_code == 0


def test_roles_explains_a_missing_tracecat_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> ModuleType:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(db.importlib, "import_module", missing)
    result = runner.invoke(app, ["db", "roles", "status"])
    assert result.exit_code == 1
    assert "tracecat-admin[bootstrap]" in result.stderr


def test_roles_runs_the_real_script() -> None:
    """With tracecat installed, the script's own argparse answers."""
    result = runner.invoke(app, ["db", "roles", "--help"])
    assert result.exit_code == 0
    assert "tracecat db roles" in result.stdout
    for command in ("bootstrap", "upgrade", "status", "migrate"):
        assert command in result.stdout


def test_roles_connection_failure_prints_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable database is an error line, not a traceback with the URI."""
    secret = "synthetic-admin-secret"
    monkeypatch.setattr(
        config, "TRACECAT__DB_ADMIN_URI", f"postgresql://admin:{secret}@127.0.0.1:1/x"
    )
    monkeypatch.setattr(config, "TRACECAT__DB_SSLMODE", "disable")
    result = runner.invoke(app, ["db", "roles", "status"])
    assert result.exit_code == 1
    assert "error:" in result.stderr
    assert secret not in result.output + result.stderr
