"""Fixture definitions and idempotent setup for the scatter load test.

Everything here goes through the public API. Setup is idempotent so it can be
re-run between matrix cells without recreating the cluster or the database
volume: existing tables and workflows are reused by name/title.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .client import TracecatClient
from .models import (
    FixtureHandles,
    TableColumnFixture,
    TableFixture,
    WorkflowFixture,
    WritePath,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
TABLE_FIXTURE_PATH = FIXTURE_DIR / "table.json"

WORKFLOW_FIXTURE_PATHS: dict[WritePath, Path] = {
    WritePath.SCATTER: FIXTURE_DIR / "workflow_scatter_insert_row.yml",
    WritePath.BULK: FIXTURE_DIR / "workflow_bulk_insert_rows.yml",
}


class FixtureError(RuntimeError):
    """Fixture setup could not complete."""


def load_table_fixture(path: Path = TABLE_FIXTURE_PATH) -> TableFixture:
    """Load the synthetic table definition."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FixtureError(f"{path} must contain a JSON object")
    raw_columns = raw.get("columns")
    if not isinstance(raw_columns, list):
        raise FixtureError(f"{path} is missing a 'columns' array")
    columns = tuple(
        TableColumnFixture(
            name=str(column["name"]),
            type=str(column["type"]),
            nullable=bool(column.get("nullable", True)),
        )
        for column in raw_columns
        if isinstance(column, dict)
    )
    return TableFixture(
        name=str(raw["name"]),
        columns=columns,
        unique_index_column=str(raw["unique_index_column"]),
        unique_index_note=str(raw.get("unique_index_note", "")),
    )


def load_workflow_fixture(write_path: WritePath) -> WorkflowFixture:
    """Load one fixture workflow and read its declared title."""
    path = WORKFLOW_FIXTURE_PATHS[write_path]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FixtureError(f"{path} must contain a YAML mapping")
    definition = raw.get("definition")
    if not isinstance(definition, dict):
        raise FixtureError(f"{path} is missing a 'definition' mapping")
    title = definition.get("title")
    if not isinstance(title, str):
        raise FixtureError(f"{path} is missing 'definition.title'")
    return WorkflowFixture(write_path=write_path, path=str(path), title=title)


async def _ensure_table(
    client: TracecatClient, workspace_id: str, fixture: TableFixture
) -> tuple[str, str]:
    """Create the fixture table if absent and ensure its unique index exists."""
    tables = await client.list_tables(workspace_id)
    table_id = next((t["id"] for t in tables if t["name"] == fixture.name), None)

    if table_id is None:
        await client.create_table(
            workspace_id,
            fixture.name,
            [
                {"name": c.name, "type": c.type, "nullable": c.nullable}
                for c in fixture.columns
            ],
        )
        tables = await client.list_tables(workspace_id)
        table_id = next((t["id"] for t in tables if t["name"] == fixture.name), None)
        if table_id is None:
            raise FixtureError(f"table '{fixture.name}' was not created")

    columns = await client.get_table_columns(workspace_id, table_id)
    index_column = next(
        (c for c in columns if c["name"] == fixture.unique_index_column), None
    )
    if index_column is None:
        raise FixtureError(
            f"table '{fixture.name}' has no column "
            f"'{fixture.unique_index_column}' to index"
        )
    if not index_column["is_index"]:
        await client.set_column_unique_index(workspace_id, table_id, index_column["id"])

    return table_id, fixture.name


async def _ensure_workflow(
    client: TracecatClient, workspace_id: str, fixture: WorkflowFixture
) -> str:
    """Import the fixture workflow if absent, then commit it."""
    workflows = await client.list_workflows(workspace_id)
    workflow_id = next(
        (w["id"] for w in workflows if w["title"] == fixture.title), None
    )

    if workflow_id is None:
        path = Path(fixture.path)
        created = await client.create_workflow_from_yaml(
            workspace_id, path.name, path.read_bytes()
        )
        workflow_id = created["id"]

    result = await client.commit_workflow(workspace_id, workflow_id)
    if result["status"] != "success":
        raise FixtureError(
            f"commit failed for '{fixture.title}': {'; '.join(result['errors'])}"
        )
    return workflow_id


async def ensure_fixtures(
    client: TracecatClient,
    workspace_id: str,
    write_paths: tuple[WritePath, ...] = (WritePath.SCATTER, WritePath.BULK),
) -> FixtureHandles:
    """Create (or reuse) the fixture table and workflows, then commit them."""
    table_fixture = load_table_fixture()
    table_id, table_name = await _ensure_table(client, workspace_id, table_fixture)

    workflow_ids: dict[WritePath, str] = {}
    for write_path in write_paths:
        workflow_fixture = load_workflow_fixture(write_path)
        workflow_ids[write_path] = await _ensure_workflow(
            client, workspace_id, workflow_fixture
        )

    return FixtureHandles(
        workspace_id=workspace_id,
        table_id=table_id,
        table_name=table_name,
        unique_index_column=table_fixture.unique_index_column,
        workflow_ids=workflow_ids,
    )


async def resolve_workspace(
    client: TracecatClient, workspace_id: str | None, workspace_name: str
) -> str:
    """Resolve the target workspace, creating it by name when necessary."""
    if workspace_id:
        return workspace_id

    workspaces = await client.list_workspaces()
    for workspace in workspaces:
        if workspace["name"] == workspace_name:
            return workspace["id"]

    created = await client.create_workspace(workspace_name)
    return created["id"]
