from collections.abc import AsyncGenerator
from typing import Any

import pytest
import yaml
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Workflow, Workspace
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import ScopeDeniedError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.management.definitions import WorkflowDefinitionsService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def definitions_service(
    session: AsyncSession, svc_role: Role
) -> WorkflowDefinitionsService:
    """Create a workflow definitions service instance for testing."""
    return WorkflowDefinitionsService(session=session, role=svc_role)


@pytest.fixture
async def workflow_id(
    session: AsyncSession, svc_workspace: Workspace
) -> AsyncGenerator[WorkflowUUID, None]:
    """Create a test workflow in the database and return its ID."""
    workflow = Workflow(
        title="test-workflow",
        workspace_id=svc_workspace.id,
        description="Test workflow for definitions testing",
        status="active",
        entrypoint=None,
        returns=None,
    )
    session.add(workflow)
    await session.commit()
    try:
        yield WorkflowUUID.new(workflow.id)
    finally:
        await session.delete(workflow)
        await session.commit()


def _dsl_input(marker: str) -> DSLInput:
    return DSLInput.model_validate(
        {
            "title": f"Test Workflow {marker}",
            "description": "Test workflow for definitions testing",
            "entrypoint": {"ref": "test_action"},
            "actions": [
                {
                    "ref": "test_action",
                    "action": "core.transform.transform",
                    "args": {"value": marker},
                }
            ],
        }
    )


@pytest.mark.anyio
async def test_get_definition_prefers_workflow_current_version(
    definitions_service: WorkflowDefinitionsService,
    workflow_id: WorkflowUUID,
    session: AsyncSession,
):
    version_one = await definitions_service.create_workflow_definition(
        workflow_id=workflow_id,
        dsl=_dsl_input("v1"),
        commit=True,
    )
    version_two = await definitions_service.create_workflow_definition(
        workflow_id=workflow_id,
        dsl=_dsl_input("v2"),
        commit=True,
    )

    workflow = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
    assert workflow is not None
    workflow.version = version_one.version
    await session.commit()

    current = await definitions_service.get_definition_by_workflow_id(workflow_id)
    assert current is not None
    assert current.id == version_one.id

    explicit = await definitions_service.get_definition_by_workflow_id(
        workflow_id, version=version_two.version
    )
    assert explicit is not None
    assert explicit.id == version_two.id


@pytest.mark.anyio
async def test_get_definition_falls_back_to_latest_when_current_version_missing(
    definitions_service: WorkflowDefinitionsService,
    workflow_id: WorkflowUUID,
    session: AsyncSession,
):
    await definitions_service.create_workflow_definition(
        workflow_id=workflow_id,
        dsl=_dsl_input("v1"),
        commit=True,
    )
    version_two = await definitions_service.create_workflow_definition(
        workflow_id=workflow_id,
        dsl=_dsl_input("v2"),
        commit=True,
    )

    workflow = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
    assert workflow is not None
    workflow.version = version_two.version + 10
    await session.commit()

    stale_pointer_result = await definitions_service.get_definition_by_workflow_id(
        workflow_id
    )
    assert stale_pointer_result is not None
    assert stale_pointer_result.id == version_two.id

    workflow.version = None
    await session.commit()

    null_pointer_result = await definitions_service.get_definition_by_workflow_id(
        workflow_id
    )
    assert null_pointer_result is not None
    assert null_pointer_result.id == version_two.id


async def _count_definition_lookup_statements(
    session: AsyncSession,
    definitions_service: WorkflowDefinitionsService,
    workflow_id: WorkflowUUID,
    **kwargs: Any,
) -> int:
    """Count SQL statements one definition lookup sends from a cold session."""
    session.expunge_all()
    statements: list[str] = []

    def record(*args: Any) -> None:
        statements.append(args[2])

    connection = (await session.connection()).sync_connection
    assert connection is not None
    event.listen(connection, "before_cursor_execute", record)
    try:
        defn = await definitions_service.get_definition_by_workflow_id(
            workflow_id, **kwargs
        )
    finally:
        event.remove(connection, "before_cursor_execute", record)
    assert defn is not None
    # Leave no loaded copy behind to clash with the fixture's workflow teardown.
    session.expunge_all()
    return len(statements)


@pytest.mark.anyio
async def test_get_definition_without_relationships_is_one_statement(
    definitions_service: WorkflowDefinitionsService,
    workflow_id: WorkflowUUID,
    session: AsyncSession,
):
    """Run and case-trigger lookups skip the workflow's always-on selectins."""
    await definitions_service.create_workflow_definition(
        workflow_id=workflow_id, dsl=_dsl_input("v1"), commit=True
    )

    full = await _count_definition_lookup_statements(
        session, definitions_service, workflow_id
    )
    bare = await _count_definition_lookup_statements(
        session, definitions_service, workflow_id, load_relationships=False
    )
    with_workflow = await _count_definition_lookup_statements(
        session,
        definitions_service,
        workflow_id,
        load_relationships=False,
        load_workflow=True,
    )

    # Definition, workflow, then the workflow's actions, webhook, case trigger
    # and schedules selectins.
    assert full == 6
    assert bare == 1
    assert with_workflow == 1


@pytest.mark.anyio
async def test_get_definition_load_workflow_reads_version_in_one_query(
    definitions_service: WorkflowDefinitionsService,
    workflow_id: WorkflowUUID,
    session: AsyncSession,
):
    """The case-trigger consumer compares versions without extra round trips."""
    created = await definitions_service.create_workflow_definition(
        workflow_id=workflow_id, dsl=_dsl_input("v1"), commit=True
    )
    workflow = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
    assert workflow is not None
    workflow.version = created.version
    await session.commit()
    session.expunge_all()

    defn = await definitions_service.get_definition_by_workflow_id(
        workflow_id, load_relationships=False, load_workflow=True
    )

    assert defn is not None
    assert defn.workflow.version == created.version
    session.expunge_all()


@pytest.mark.anyio
async def test_get_definition_refuses_other_workspace(
    definitions_service: WorkflowDefinitionsService,
    workflow_id: WorkflowUUID,
    session: AsyncSession,
    svc_workspace: Workspace,
):
    """Skipping relationships keeps the workspace predicate on the lookup."""
    await definitions_service.create_workflow_definition(
        workflow_id=workflow_id, dsl=_dsl_input("v1"), commit=True
    )
    other = Workspace(name="other", organization_id=svc_workspace.organization_id)
    session.add(other)
    await session.commit()
    other_service = WorkflowDefinitionsService(
        session=session,
        role=definitions_service.role.model_copy(update={"workspace_id": other.id}),
    )

    for kwargs in ({}, {"load_workflow": True}):
        assert (
            await other_service.get_definition_by_workflow_id(
                workflow_id, load_relationships=False, **kwargs
            )
            is None
        )


@pytest.mark.anyio
async def test_create_initial_workflow_definition_uses_create_scope(
    session: AsyncSession,
    svc_workspace: Workspace,
    workflow_id: WorkflowUUID,
):
    create_only_role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=svc_workspace.organization_id,
        workspace_id=svc_workspace.id,
        scopes=frozenset({"workflow:create"}),
    )
    service = WorkflowDefinitionsService(session=session, role=create_only_role)

    definition = await service.create_initial_workflow_definition(
        workflow_id=workflow_id,
        dsl=_dsl_input("initial"),
        commit=True,
    )

    assert definition.version == 1
    with pytest.raises(ScopeDeniedError):
        await service.create_workflow_definition(
            workflow_id=workflow_id,
            dsl=_dsl_input("v2"),
            commit=True,
        )


@pytest.mark.anyio
async def test_create_workflow_definition_with_various_datetime_formats(
    definitions_service: WorkflowDefinitionsService,
    workflow_id: WorkflowUUID,
):
    """Test workflow definition creation with various datetime formats.

    Regression: datetime objects from YAML parsing caused JSON serialization
    errors when saving to the JSONB column.
    """
    yaml_content = """
    title: Test Workflow
    description: Test workflow with various datetime formats
    entrypoint:
      ref: datetime_action
    actions:
      - ref: datetime_action
        action: core.transform.transform
        args:
          iso_datetime: 2024-11-01T00:00:00
          iso_with_tz: 2024-11-01T00:00:00+00:00
          date_only: 2024-11-01
          nested_dates:
            start: 2024-11-01T09:00:00
            end: 2024-11-01T17:00:00
    """

    parsed_yaml = yaml.safe_load(yaml_content)
    dsl_input = DSLInput.model_validate(parsed_yaml)

    # This should not raise any serialization errors
    definition = await definitions_service.create_workflow_definition(
        workflow_id=workflow_id,
        dsl=dsl_input,
        commit=True,
    )

    assert definition is not None
    assert definition.workflow_id == workflow_id
    assert definition.version == 1

    # Verify all datetime formats were handled correctly
    content = definition.content
    args = content["actions"][0]["args"]

    # All datetime values should be strings in the saved content
    assert isinstance(args["iso_datetime"], str)
    assert "2024-11-01T00:00:00" in args["iso_datetime"]
    assert isinstance(args["iso_with_tz"], str)
    assert isinstance(args["date_only"], str)
    assert isinstance(args["nested_dates"]["start"], str)
    assert isinstance(args["nested_dates"]["end"], str)
