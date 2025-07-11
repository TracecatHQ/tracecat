from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
import yaml
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Workflow, Workspace
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.types.auth import Role
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
        owner_id=svc_workspace.id,
        description="Test workflow for definitions testing",
        status="active",
        entrypoint=None,
        returns=None,
        object=None,
    )
    session.add(workflow)
    await session.commit()
    try:
        yield WorkflowUUID.new(workflow.id)
    finally:
        await session.delete(workflow)
        await session.commit()


@pytest.mark.anyio
async def test_create_workflow_definition_with_datetime_serialization(
    definitions_service: WorkflowDefinitionsService,
    workflow_id: WorkflowUUID,
):
    """Test that workflow definitions can be created with datetime objects in DSL.

    This test specifically catches the regression where datetime objects in YAML
    would cause JSON serialization errors when saving to the JSONB column.
    """
    # Create a DSL with datetime values (simulating YAML parsing that converts
    # datetime strings to datetime objects)
    yaml_content = """
    title: Test Workflow
    description: Test workflow with datetime values
    entrypoint:
      ref: datetime_action
    actions:
      - ref: datetime_action
        action: core.transform.transform
        args:
          value: "test"
          date_field: 2024-11-01T00:00:00
    """

    # Parse YAML to simulate the real scenario where datetime strings become datetime objects
    parsed_yaml = yaml.safe_load(yaml_content)

    # Create DSLInput from parsed YAML (this will have datetime objects)
    dsl_input = DSLInput.model_validate(parsed_yaml)

    # Verify that the DSL contains datetime objects (not strings)
    datetime_value = dsl_input.actions[0].args["date_field"]
    assert isinstance(datetime_value, datetime)

    # This should not raise a JSON serialization error
    definition = await definitions_service.create_workflow_definition(
        workflow_id=workflow_id,
        dsl=dsl_input,
        commit=True,
    )

    # Verify the definition was created successfully
    assert definition is not None
    assert definition.workflow_id == workflow_id
    assert definition.version == 1

    # Verify the content was serialized properly (datetime should be converted to string)
    content = definition.content
    assert isinstance(content, dict)

    # Verify that datetime objects were properly serialized to JSON-compatible format
    saved_datetime = content["actions"][0]["args"]["date_field"]
    assert isinstance(saved_datetime, str)  # Should be serialized as ISO string
    assert "2024-11-01T00:00:00" in saved_datetime


@pytest.mark.anyio
async def test_dslinput_model_dump_forces_json_serialization():
    """Test that DSLInput.model_dump() always forces JSON serialization mode.

    This test ensures that the fix for datetime serialization is working at the
    Pydantic model level.
    """
    # Create DSLInput with datetime values
    yaml_content = """
    title: Test Workflow
    description: Test workflow with datetime
    entrypoint:
      ref: test_action
    actions:
      - ref: test_action
        action: core.transform.transform
        args:
          timestamp: 2024-11-01T00:00:00
          nested:
            date_field: 2024-12-01T12:30:00
    """

    parsed_yaml = yaml.safe_load(yaml_content)
    dsl_input = DSLInput.model_validate(parsed_yaml)

    # Verify datetime objects exist in the model
    assert isinstance(dsl_input.actions[0].args["timestamp"], datetime)
    assert isinstance(dsl_input.actions[0].args["nested"]["date_field"], datetime)

    # Test model_dump() - should not raise JSON serialization errors
    dumped = dsl_input.model_dump()

    # Verify that datetime objects were serialized as strings
    assert isinstance(dumped["actions"][0]["args"]["timestamp"], str)
    assert isinstance(dumped["actions"][0]["args"]["nested"]["date_field"], str)

    # Verify the format is correct
    assert "2024-11-01T00:00:00" in dumped["actions"][0]["args"]["timestamp"]
    assert "2024-12-01T12:30:00" in dumped["actions"][0]["args"]["nested"]["date_field"]


@pytest.mark.anyio
async def test_create_workflow_definition_with_various_datetime_formats(
    definitions_service: WorkflowDefinitionsService,
    workflow_id: WorkflowUUID,
):
    """Test workflow definition creation with various datetime formats."""
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

    # Verify all datetime formats were handled correctly
    content = definition.content
    args = content["actions"][0]["args"]

    # All datetime values should be strings in the saved content
    assert isinstance(args["iso_datetime"], str)
    assert isinstance(args["iso_with_tz"], str)
    assert isinstance(args["date_only"], str)
    assert isinstance(args["nested_dates"]["start"], str)
    assert isinstance(args["nested_dates"]["end"], str)
