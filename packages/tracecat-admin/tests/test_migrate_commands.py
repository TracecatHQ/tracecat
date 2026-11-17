"""Tests for the migrate commands' alembic environment."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from tracecat_admin.commands import migrate


def test_run_alembic_drops_the_token_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The commands log in with the URI they're given, never the app's token mode."""
    seen: dict[str, Any] = {}

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setenv("TRACECAT__DB_AUTH_MODE", "aws_iam")
    monkeypatch.setattr(migrate, "_get_alembic_dir", lambda: "/tmp")
    monkeypatch.setattr(subprocess, "run", run)

    uri = "postgresql+psycopg://admin:pw@db.example:5432/tracecat"
    assert migrate._run_alembic(["upgrade", "head"], db_uri=uri) == 0
    assert seen["env"]["TRACECAT__DB_URI"] == uri
    assert "TRACECAT__DB_AUTH_MODE" not in seen["env"]
