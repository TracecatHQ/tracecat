"""Load-type fixture definitions and idempotent setup.

Everything here goes through the public API. Setup is idempotent so it can be
re-run between matrix cells without recreating the cluster or the database
volume: existing tables are reused, while alias-marked fixture workflows are
replaced from checked-in YAML so an older draft cannot contaminate a later cell.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import yaml

from .client import TableColumnRef, TracecatClient
from .models import (
    FixtureHandles,
    LoadType,
    TableColumnFixture,
    TableFixture,
    WorkflowFixture,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
TABLE_FIXTURE_PATH = FIXTURE_DIR / "table.json"

SUBFLOW_CHILD_ALIAS: Final = "scatter_load_subflow_child"


@dataclass(frozen=True, slots=True)
class LoadTypeFixtureSpec:
    """Checked-in workflows needed to run one load type."""

    workflow_path: Path
    workflow_alias: str
    child_workflows: tuple[tuple[str, Path], ...] = ()


LOAD_TYPE_FIXTURES: Final[dict[LoadType, LoadTypeFixtureSpec]] = {
    LoadType.SCATTER: LoadTypeFixtureSpec(
        workflow_path=FIXTURE_DIR / "workflow_scatter_insert_row.yml",
        workflow_alias="scatter_load_insert_row_fixture",
    ),
    LoadType.NOOP: LoadTypeFixtureSpec(
        workflow_path=FIXTURE_DIR / "workflow_noop_reshape.yml",
        workflow_alias="scatter_load_noop_reshape_fixture",
    ),
    LoadType.BULK: LoadTypeFixtureSpec(
        workflow_path=FIXTURE_DIR / "workflow_bulk_insert_rows.yml",
        workflow_alias="scatter_load_insert_rows_fixture",
    ),
    LoadType.SUBFLOW: LoadTypeFixtureSpec(
        workflow_path=FIXTURE_DIR / "workflow_subflow_parent.yml",
        workflow_alias="scatter_load_subflow_parent",
        child_workflows=(
            (
                SUBFLOW_CHILD_ALIAS,
                FIXTURE_DIR / "workflow_subflow_child_insert.yml",
            ),
        ),
    ),
}

ALL_WORKFLOW_FIXTURE_ALIASES: Final = (
    *(spec.workflow_alias for spec in LOAD_TYPE_FIXTURES.values()),
    *(
        alias
        for spec in LOAD_TYPE_FIXTURES.values()
        for alias, _path in spec.child_workflows
    ),
)


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
    columns: list[TableColumnFixture] = []
    for index, raw_column in enumerate(raw_columns):
        if not isinstance(raw_column, dict):
            raise FixtureError(f"{path} columns[{index}] must be a JSON object")
        column = cast(dict[str, object], raw_column)
        columns.append(
            TableColumnFixture(
                name=str(column["name"]),
                type=str(column["type"]),
                nullable=bool(column.get("nullable", True)),
            )
        )
    return TableFixture(
        name=str(raw["name"]),
        columns=tuple(columns),
        unique_index_column=str(raw["unique_index_column"]),
        unique_index_note=str(raw.get("unique_index_note", "")),
    )


def _read_workflow_title(path: Path) -> str:
    """Read `definition.title` out of an external workflow definition file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FixtureError(f"{path} must contain a YAML mapping")
    definition = raw.get("definition")
    if not isinstance(definition, dict):
        raise FixtureError(f"{path} is missing a 'definition' mapping")
    title = definition.get("title")
    if not isinstance(title, str):
        raise FixtureError(f"{path} is missing 'definition.title'")
    return title


def _static_action_workflow_content(
    path: Path, load_type: LoadType, branch_count: int
) -> bytes:
    """Materialize one independent static action per logical branch."""
    if branch_count <= 0:
        raise FixtureError("static action branch_count must be positive")
    if not load_type.materializes_static_actions:
        raise FixtureError(f"{load_type.value} does not use static action expansion")

    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FixtureError(f"{path} must contain a YAML mapping")
    definition = raw.get("definition")
    if not isinstance(definition, dict):
        raise FixtureError(f"{path} is missing a 'definition' mapping")
    actions = definition.get("actions")
    if not isinstance(actions, list) or len(actions) != 1:
        raise FixtureError(f"{path} must contain exactly one static action template")
    template = actions[0]
    if not isinstance(template, dict):
        raise FixtureError(f"{path} static action template must be a mapping")

    ref_prefix = "insert_row" if load_type is LoadType.SCATTER else "noop"

    materialized: list[dict[str, object]] = []
    for branch_seq in range(branch_count):
        action = cast(dict[str, object], copy.deepcopy(template))
        action["ref"] = ref_prefix if branch_seq == 0 else f"{ref_prefix}_{branch_seq}"
        if load_type is LoadType.SCATTER:
            args = action.get("args")
            if not isinstance(args, dict):
                raise FixtureError(
                    f"{path} scatter action template has no 'args' mapping"
                )
            row_data = args.get("row_data")
            if not isinstance(row_data, dict):
                raise FixtureError(
                    f"{path} scatter action template has no 'row_data' mapping"
                )
            row_data["branch_seq"] = branch_seq
            row_data["dedupe_key"] = (
                f"${{{{ TRIGGER.run_id }}}}:${{{{ TRIGGER.workflow_seq }}}}:{branch_seq}"
            )
        materialized.append(action)

    definition["actions"] = materialized
    return yaml.safe_dump(raw, sort_keys=False).encode()


def load_workflow_fixture(
    load_type: LoadType, *, branch_count: int = 1
) -> WorkflowFixture:
    """Load one fixture workflow and materialize its configured static shape."""
    spec = LOAD_TYPE_FIXTURES[load_type]
    path = spec.workflow_path
    return WorkflowFixture(
        load_type=load_type,
        path=str(path),
        title=_read_workflow_title(path),
        alias=spec.workflow_alias,
        content=(
            _static_action_workflow_content(path, load_type, branch_count)
            if load_type.materializes_static_actions
            else None
        ),
    )


def load_child_workflow_fixtures(load_type: LoadType) -> tuple[WorkflowFixture, ...]:
    """Load child workflows that the selected load type depends on."""
    return tuple(
        WorkflowFixture(
            load_type=load_type,
            path=str(path),
            title=_read_workflow_title(path),
            alias=alias,
        )
        for alias, path in LOAD_TYPE_FIXTURES[load_type].child_workflows
    )


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

    await _validate_table(client, workspace_id, table_id, fixture)
    return table_id, fixture.name


async def _validate_table(
    client: TracecatClient,
    workspace_id: str,
    table_id: str,
    fixture: TableFixture,
) -> list[TableColumnRef]:
    """Verify the exact synthetic schema and ensure its supported unique index."""
    columns = await client.get_table_columns(workspace_id, table_id)
    actual_schema = {
        column["name"]: (column["type"], column["nullable"]) for column in columns
    }
    expected_schema = {
        column.name: (column.type, column.nullable) for column in fixture.columns
    }
    if actual_schema != expected_schema:
        raise FixtureError(
            f"table '{fixture.name}' schema does not match the fixture: "
            f"expected {expected_schema!r}, got {actual_schema!r}"
        )

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

    return columns


async def _assert_fixture_workflows_quiescent(
    client: TracecatClient, workspace_id: str
) -> None:
    """Refuse to reset while an alias-owned fixture workflow can still write."""
    workflows = await client.list_workflows(workspace_id)
    fixture_workflows = [
        workflow
        for workflow in workflows
        if workflow["alias"] in ALL_WORKFLOW_FIXTURE_ALIASES
    ]
    running_aliases = {
        workflow["alias"]
        for workflow in fixture_workflows
        if await client.has_running_executions(workspace_id, workflow["id"])
    }
    if running_aliases:
        aliases = ", ".join(sorted(alias for alias in running_aliases if alias))
        raise FixtureError(
            "fixture workflows must be quiescent before resetting the table; "
            f"still running: {aliases}. Wait for them to finish, then retry."
        )


async def reset_fixture_table(client: TracecatClient, workspace_id: str) -> str:
    """Drop and recreate only the checked-in synthetic table through the API.

    Recreating the relation removes accumulated rows, dead tuples, and index
    drift between matrix cells. The existing table must match the checked-in
    fixture schema before it can be deleted, so this mode cannot target an
    arbitrary user table. Alias-owned fixture workflows must also be quiescent
    so an execution from the previous cell cannot write into the new relation.
    """
    fixture = load_table_fixture()
    tables = await client.list_tables(workspace_id)
    matching_tables = [table for table in tables if table["name"] == fixture.name]
    if len(matching_tables) > 1:
        raise FixtureError(
            f"multiple tables named '{fixture.name}' exist; refusing fixture reset"
        )
    if matching_tables:
        table_id = matching_tables[0]["id"]
        await _validate_table(client, workspace_id, table_id, fixture)

    await _assert_fixture_workflows_quiescent(client, workspace_id)

    if matching_tables:
        table_id = matching_tables[0]["id"]
        await client.delete_table(workspace_id, table_id)

    await _ensure_table(client, workspace_id, fixture)
    return fixture.name


async def _ensure_workflow(
    client: TracecatClient, workspace_id: str, fixture: WorkflowFixture
) -> str:
    """Replace only an alias-marked fixture workflow from YAML, then commit it."""
    if fixture.alias is None:
        raise FixtureError(f"workflow fixture '{fixture.title}' has no ownership alias")

    workflows = await client.list_workflows(workspace_id)
    alias_matches = [w for w in workflows if w["alias"] == fixture.alias]
    if len(alias_matches) > 1:
        raise FixtureError(
            f"multiple workflows use fixture alias '{fixture.alias}'; "
            "refusing replacement"
        )
    workflow_id: str | None = None
    if alias_matches:
        existing = alias_matches[0]
        if existing["title"] != fixture.title:
            raise FixtureError(
                f"workflow alias '{fixture.alias}' belongs to title "
                f"'{existing['title']}', not fixture '{fixture.title}'; "
                "refusing replacement"
            )
        workflow_id = existing["id"]

    if workflow_id is not None:
        if await client.has_running_executions(workspace_id, workflow_id):
            raise FixtureError(
                f"workflow fixture '{fixture.title}' still has running executions; "
                "wait for them to finish before replacing it"
            )
        # The public API has no whole-draft import endpoint for an existing
        # workflow. The reserved alias plus exact title is the ownership marker
        # that makes deletion safe; a title match alone is never sufficient.
        await client.delete_workflow(workspace_id, workflow_id)

    path = Path(fixture.path)
    created = await client.create_workflow_from_yaml(
        workspace_id,
        path.name,
        fixture.content if fixture.content is not None else path.read_bytes(),
    )
    workflow_id = created["id"]

    # Idempotent: re-assigning the same alias to the same workflow is a no-op,
    # and commit below snapshots it onto the new definition.
    await client.set_workflow_alias(workspace_id, workflow_id, fixture.alias)

    result = await client.commit_workflow(workspace_id, workflow_id)
    if result["status"] != "success":
        raise FixtureError(
            f"commit failed for '{fixture.title}': {'; '.join(result['errors'])}"
        )
    return workflow_id


async def ensure_fixtures(
    client: TracecatClient,
    workspace_id: str,
    load_types: tuple[LoadType, ...] = (LoadType.SCATTER, LoadType.BULK),
    *,
    branch_count: int = 1,
) -> FixtureHandles:
    """Create/reuse the table and replace fixture workflows from source YAML."""
    table_fixture = load_table_fixture()
    table_id, table_name = await _ensure_table(client, workspace_id, table_fixture)

    workflow_ids: dict[LoadType, str] = {}
    for load_type in load_types:
        # Children first: the parent resolves them by their committed alias.
        for child_fixture in load_child_workflow_fixtures(load_type):
            await _ensure_workflow(client, workspace_id, child_fixture)
        workflow_fixture = load_workflow_fixture(load_type, branch_count=branch_count)
        workflow_ids[load_type] = await _ensure_workflow(
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
