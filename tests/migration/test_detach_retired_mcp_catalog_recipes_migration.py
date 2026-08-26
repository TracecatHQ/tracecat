"""Tests for the retired MCP catalog recipe detachment migration."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "2ba251c25b81"
PREVIOUS_REVISION = "598b32358ec5"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / f"{MIGRATION_REVISION}_detach_retired_mcp_catalog_recipes.py"
)

SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)

# Columns the migration must leave byte-identical. ``slug`` matters most: MCP
# tool names are derived from it.
PRESERVED_COLUMNS = (
    "name",
    "slug",
    "server_type",
    "server_uri",
    "auth_type",
    "stdio_command",
    "stdio_args",
    "tools",
)

# The per-slug report lines the migration is expected to emit, given the rows
# seeded below.
EXPECTED_REPORT_LINES = (
    "catalog_slug=google-security-command-center-mcp server_type=any rows=1",
    "catalog_slug=virustotal-mcp server_type=any rows=1",
    "catalog_slug=servicenow-mcp server_type=stdio rows=1",
    "catalog_slug=google-cloud-secops-mcp server_type=stdio rows=1",
    "catalog_slug=semgrep-mcp server_type=stdio rows=1",
    "catalog_slug=jamf-mcp server_type=stdio rows=1",
)


@dataclass(frozen=True, slots=True)
class SeededIntegration:
    """One ``mcp_integration`` row to seed, plus its expected outcome."""

    key: str
    catalog_slug: str | None
    server_type: str
    auth_type: str
    expect_detached: bool
    stdio_command: str | None = None
    stdio_args: list[str] | None = None
    server_uri: str | None = None


SEEDED_INTEGRATIONS: tuple[SeededIntegration, ...] = (
    # Catalog entry removed outright: every row detaches regardless of type.
    SeededIntegration(
        key="scc-stdio",
        catalog_slug="google-security-command-center-mcp",
        server_type="stdio",
        auth_type="CUSTOM",
        expect_detached=True,
        stdio_command="uvx",
        stdio_args=["scc-mcp"],
    ),
    SeededIntegration(
        key="virustotal-http",
        catalog_slug="virustotal-mcp",
        server_type="http",
        auth_type="CUSTOM",
        expect_detached=True,
        server_uri="https://example.invalid/virustotal/mcp",
    ),
    # Replaced recipes: the stdio row detaches, the HTTP row keeps its binding.
    SeededIntegration(
        key="servicenow-stdio",
        catalog_slug="servicenow-mcp",
        server_type="stdio",
        auth_type="CUSTOM",
        expect_detached=True,
        stdio_command="npx",
        stdio_args=["-y", "servicenow-mcp"],
    ),
    SeededIntegration(
        key="servicenow-http",
        catalog_slug="servicenow-mcp",
        server_type="http",
        auth_type="CUSTOM",
        expect_detached=False,
        server_uri="https://example.invalid/servicenow/mcp",
    ),
    SeededIntegration(
        key="jamf-stdio",
        catalog_slug="jamf-mcp",
        server_type="stdio",
        auth_type="CUSTOM",
        expect_detached=True,
        stdio_command="npx",
        stdio_args=["-y", "jamf-mcp"],
    ),
    SeededIntegration(
        key="jamf-http",
        catalog_slug="jamf-mcp",
        server_type="http",
        auth_type="NONE",
        expect_detached=False,
        server_uri="https://developer.jamf.com/mcp",
    ),
    SeededIntegration(
        key="semgrep-stdio",
        catalog_slug="semgrep-mcp",
        server_type="stdio",
        auth_type="CUSTOM",
        expect_detached=True,
        stdio_command="uvx",
        stdio_args=["semgrep-mcp"],
    ),
    SeededIntegration(
        key="secops-stdio",
        catalog_slug="google-cloud-secops-mcp",
        server_type="stdio",
        auth_type="CUSTOM",
        expect_detached=True,
        stdio_command="uvx",
        stdio_args=["secops-mcp"],
    ),
    # Unrelated catalog row: untouched.
    SeededIntegration(
        key="linear-http",
        catalog_slug="linear-mcp",
        server_type="http",
        auth_type="NONE",
        expect_detached=False,
        server_uri="https://example.invalid/linear/mcp",
    ),
    # Already-custom row: never had a binding to lose.
    SeededIntegration(
        key="custom-stdio",
        catalog_slug=None,
        server_type="stdio",
        auth_type="CUSTOM",
        expect_detached=False,
        stdio_command="uvx",
        stdio_args=["custom-mcp"],
    ),
)


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    result = subprocess.run(
        ["uv", "run", "alembic", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Alembic command failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


@pytest.fixture(scope="function")
def migration_db_url() -> Iterator[str]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    db_name = f"test_detach_mcp_{uuid.uuid4().hex[:8]}"
    termination_query = text(
        f"""
        SELECT pg_terminate_backend(pg_stat_activity.pid)
        FROM pg_stat_activity
        WHERE pg_stat_activity.datname = '{db_name}'
          AND pid <> pg_backend_pid();
        """
    )

    try:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))

        db_url = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, db_name
        )
        _run_alembic(db_url, "upgrade", PREVIOUS_REVISION)
        yield db_url
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        default_engine.dispose()


@pytest.fixture
def migration() -> Any:
    """Import the migration module from its file path.

    Alembic version files aren't part of a real Python package, so we load by
    file path rather than dotted import.
    """
    module_name = "_detach_retired_mcp_catalog_recipes_migration_under_test"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _seed_workspace_and_integrations(
    conn: Connection,
) -> tuple[
    uuid.UUID,
    dict[str, uuid.UUID],
]:
    """Seed one org, one workspace, and every ``SEEDED_INTEGRATIONS`` row."""
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO organization (id, name, slug, is_active)
            VALUES (:id, 'Detach MCP org', :slug, true)
            """
        ),
        {"id": organization_id, "slug": f"detach-mcp-org-{organization_id.hex[:8]}"},
    )
    conn.execute(
        text(
            """
            INSERT INTO workspace (id, organization_id, name)
            VALUES (:id, :organization_id, 'Detach MCP workspace')
            """
        ),
        {"id": workspace_id, "organization_id": organization_id},
    )

    ids_by_key: dict[str, uuid.UUID] = {}
    for seeded in SEEDED_INTEGRATIONS:
        row_id = uuid.uuid4()
        ids_by_key[seeded.key] = row_id
        conn.execute(
            text(
                """
                INSERT INTO mcp_integration (
                    id,
                    workspace_id,
                    name,
                    slug,
                    catalog_slug,
                    server_type,
                    server_uri,
                    auth_type,
                    stdio_command,
                    stdio_args,
                    tools,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :workspace_id,
                    :name,
                    :slug,
                    :catalog_slug,
                    :server_type,
                    :server_uri,
                    CAST(:auth_type AS mcpauthtype),
                    :stdio_command,
                    CAST(:stdio_args AS jsonb),
                    CAST(:tools AS jsonb),
                    :created_at,
                    :created_at
                )
                """
            ),
            {
                "id": row_id,
                "workspace_id": workspace_id,
                "name": f"Seeded {seeded.key}",
                "slug": seeded.key,
                "catalog_slug": seeded.catalog_slug,
                "server_type": seeded.server_type,
                "server_uri": seeded.server_uri,
                "auth_type": seeded.auth_type,
                "stdio_command": seeded.stdio_command,
                "stdio_args": (
                    None if seeded.stdio_args is None else json.dumps(seeded.stdio_args)
                ),
                "tools": json.dumps(
                    [{"name": f"{seeded.key}-tool", "enabled": True}],
                ),
                "created_at": SEEDED_AT,
            },
        )

    return workspace_id, ids_by_key


def _snapshot(conn: Connection, workspace_id: uuid.UUID) -> dict[uuid.UUID, RowMapping]:
    rows = (
        conn.execute(
            text(
                """
                SELECT
                    id,
                    name,
                    slug,
                    catalog_slug,
                    server_type,
                    server_uri,
                    auth_type,
                    stdio_command,
                    stdio_args,
                    tools,
                    created_at,
                    updated_at
                FROM mcp_integration
                WHERE workspace_id = :workspace_id
                """
            ),
            {"workspace_id": workspace_id},
        )
        .mappings()
        .all()
    )
    return {row["id"]: row for row in rows}


def _assert_preserved(
    before: dict[uuid.UUID, RowMapping],
    after: dict[uuid.UUID, RowMapping],
) -> None:
    assert before.keys() == after.keys()
    for row_id, before_row in before.items():
        after_row = after[row_id]
        for column in PRESERVED_COLUMNS:
            assert after_row[column] == before_row[column], (
                f"column {column} changed on row {row_id}"
            )
        assert after_row["created_at"] == before_row["created_at"]
        assert after_row["updated_at"] == before_row["updated_at"]


def test_upgrade_detaches_retired_catalog_recipes(migration_db_url: str) -> None:
    """Retired bindings are cleared; every other column and row is untouched."""
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            workspace_id, ids_by_key = _seed_workspace_and_integrations(conn)
            before = _snapshot(conn, workspace_id)

        result = _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)
        output = f"{result.stdout}\n{result.stderr}"

        with engine.begin() as conn:
            after = _snapshot(conn, workspace_id)

        expected_null = {
            ids_by_key[seeded.key]
            for seeded in SEEDED_INTEGRATIONS
            if seeded.expect_detached or seeded.catalog_slug is None
        }
        actual_null = {
            row_id for row_id, row in after.items() if row["catalog_slug"] is None
        }
        assert actual_null == expected_null

        # Rows that keep a binding keep the exact slug they were created from.
        for seeded in SEEDED_INTEGRATIONS:
            if seeded.expect_detached or seeded.catalog_slug is None:
                continue
            assert after[ids_by_key[seeded.key]]["catalog_slug"] == seeded.catalog_slug

        _assert_preserved(before, after)

        # Reports carry the repo-owned catalog slug and a count only.
        for line in EXPECTED_REPORT_LINES:
            assert line in output
        assert str(workspace_id) not in output
        for row_id in ids_by_key.values():
            assert str(row_id) not in output
    finally:
        engine.dispose()


def test_detach_is_idempotent(
    migration_db_url: str,
    migration: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A second detach pass matches zero rows and changes nothing."""
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            workspace_id, _ = _seed_workspace_and_integrations(conn)

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        with engine.begin() as conn:
            after_first = _snapshot(conn, workspace_id)

        capsys.readouterr()
        with engine.begin() as conn:
            migration._detach_retired_catalog_bindings(conn)
        rerun_output = capsys.readouterr().out

        with engine.begin() as conn:
            after_second = _snapshot(conn, workspace_id)

        _assert_preserved(after_first, after_second)
        for row_id, first_row in after_first.items():
            assert after_second[row_id]["catalog_slug"] == first_row["catalog_slug"]

        reported_slugs = (
            *migration.RETIRED_CATALOG_SLUGS,
            *migration.RETIRED_STDIO_CATALOG_SLUGS,
        )
        for slug in reported_slugs:
            assert f"catalog_slug={slug} " in rerun_output
        report_lines = [
            line
            for line in rerun_output.splitlines()
            if "Detached retired MCP catalog binding" in line
        ]
        assert len(report_lines) == len(reported_slugs)
        assert all(line.rstrip().endswith("rows=0") for line in report_lines)
    finally:
        engine.dispose()
