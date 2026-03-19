import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from sqlalchemy import select

from tracecat.auth.types import Role
from tracecat.db.models import CaseTag, CaseTrigger, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.case_triggers.schemas import (
    CaseTriggerConfig,
    CaseTriggerEventFilters,
)
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import (
    ExternalWorkflowDefinition,
    WorkflowLayout,
    WorkflowLayoutActionPosition,
    WorkflowLayoutPosition,
    WorkflowLayoutViewport,
)

pytestmark = pytest.mark.usefixtures("registry_version_with_manifest")


@pytest.mark.parametrize(
    "id",
    [
        pytest.param(WorkflowUUID.new_uuid4().to_legacy(), id="legacy"),
        pytest.param(WorkflowUUID.new_uuid4(), id="workflow_uuid"),
        pytest.param(str(WorkflowUUID.new_uuid4()), id="str"),
        pytest.param(WorkflowUUID.new_uuid4().short(), id="short"),
    ],
)
@pytest.mark.anyio
async def test_workflow_can_import(test_role: Role, id: Any):
    dsl = ExternalWorkflowDefinition(
        workflow_id=id,
        definition=DSLInput(
            **{
                "title": "multiple_entrypoints",
                "description": "Test that workflow can have multiple entrypoints",
                "entrypoint": {"expects": {}, "ref": None},
                "actions": [
                    {
                        "ref": "entrypoint_1",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_1"},
                    },
                    {
                        "ref": "entrypoint_2",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_2"},
                    },
                    {
                        "ref": "entrypoint_3",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_3"},
                    },
                    {
                        "ref": "join",
                        "action": "core.transform.reshape",
                        "args": {
                            "value": {
                                "first": "${{ ACTIONS.entrypoint_1.result }}",
                                "second": "${{ ACTIONS.entrypoint_2.result }}",
                                "third": "${{ ACTIONS.entrypoint_3.result }}",
                            }
                        },
                        "depends_on": ["entrypoint_1", "entrypoint_2", "entrypoint_3"],
                        "join_strategy": "all",
                    },
                ],
                "returns": "${{ ACTIONS.join.result }}",
            }
        ),
    )

    # Import it into the database
    async with WorkflowsManagementService.with_session(test_role) as service:
        import_data = dsl.model_dump(mode="json")

        workflow = await service.create_workflow_from_external_definition(import_data)

        assert workflow.actions and len(workflow.actions) == 4


@pytest.mark.anyio
async def test_workflow_import_case_trigger_creates_tags(test_role: Role):
    dsl = ExternalWorkflowDefinition(
        definition=DSLInput(
            **{
                "title": "case_trigger_import",
                "description": "Workflow import with case trigger",
                "entrypoint": {"expects": {}, "ref": None},
                "actions": [
                    {
                        "ref": "entrypoint_1",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_1"},
                    }
                ],
                "returns": "${{ ACTIONS.entrypoint_1.result }}",
            }
        ),
        case_trigger=CaseTriggerConfig(
            status="offline",
            event_types=[],
            tag_filters=["phishing"],
            event_filters=CaseTriggerEventFilters(),
        ),
    )

    async with WorkflowsManagementService.with_session(test_role) as service:
        import_data = dsl.model_dump(mode="json")
        workflow = await service.create_workflow_from_external_definition(import_data)

        stmt = select(CaseTrigger).where(CaseTrigger.workflow_id == workflow.id)
        result = await service.session.execute(stmt)
        case_trigger = result.scalar_one()
        assert case_trigger.tag_filters == ["phishing"]
        assert case_trigger.event_filters == {
            "status_changed": [],
            "severity_changed": [],
            "priority_changed": [],
        }

        tag_stmt = select(CaseTag).where(
            CaseTag.workspace_id == workflow.workspace_id, CaseTag.ref == "phishing"
        )
        tag_result = await service.session.execute(tag_stmt)
        assert tag_result.scalar_one().ref == "phishing"


def test_external_workflow_definition_omits_empty_case_trigger():
    workflow_id = WorkflowUUID.new_uuid4()
    external = ExternalWorkflowDefinition.from_database(
        cast(
            WorkflowDefinition,
            SimpleNamespace(
                workspace_id=uuid.uuid4(),
                workflow_id=workflow_id,
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                updated_at=datetime(2024, 1, 1, tzinfo=UTC),
                version=1,
                content={
                    "title": "empty_case_trigger",
                    "description": "Export should omit inert case triggers",
                    "entrypoint": {"expects": {}, "ref": None},
                    "actions": [
                        {
                            "ref": "entrypoint_1",
                            "action": "core.transform.reshape",
                            "args": {"value": "ENTRYPOINT_1"},
                        }
                    ],
                },
                workflow=SimpleNamespace(
                    trigger_position_x=0.0,
                    trigger_position_y=0.0,
                    viewport_x=0.0,
                    viewport_y=0.0,
                    viewport_zoom=1.0,
                    actions=[],
                    case_trigger=SimpleNamespace(
                        status="offline",
                        event_types=[],
                        tag_filters=[],
                        event_filters={},
                    ),
                ),
            ),
        )
    )

    assert external.case_trigger is None


def test_external_workflow_definition_includes_layout():
    workflow_id = WorkflowUUID.new_uuid4()
    external = ExternalWorkflowDefinition.from_database(
        cast(
            WorkflowDefinition,
            SimpleNamespace(
                workspace_id=uuid.uuid4(),
                workflow_id=workflow_id,
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                updated_at=datetime(2024, 1, 1, tzinfo=UTC),
                version=1,
                content={
                    "title": "layout_export",
                    "description": "Export should preserve layout",
                    "entrypoint": {"expects": {}, "ref": None},
                    "actions": [
                        {
                            "ref": "entrypoint_1",
                            "action": "core.transform.reshape",
                            "args": {"value": "ENTRYPOINT_1"},
                        }
                    ],
                },
                workflow=SimpleNamespace(
                    trigger_position_x=12.0,
                    trigger_position_y=24.0,
                    viewport_x=30.0,
                    viewport_y=40.0,
                    viewport_zoom=1.5,
                    actions=[
                        SimpleNamespace(
                            ref="entrypoint_1",
                            position_x=100.0,
                            position_y=200.0,
                        )
                    ],
                    case_trigger=SimpleNamespace(
                        status="offline",
                        event_types=[],
                        tag_filters=[],
                        event_filters={},
                    ),
                ),
            ),
        )
    )

    assert external.layout is not None
    assert external.layout.trigger == WorkflowLayoutPosition(x=12.0, y=24.0)
    assert external.layout.viewport == WorkflowLayoutViewport(x=30.0, y=40.0, zoom=1.5)
    assert external.layout.actions == [
        WorkflowLayoutActionPosition(ref="entrypoint_1", x=100.0, y=200.0)
    ]


@pytest.mark.anyio
async def test_workflow_import_ignores_empty_case_trigger(test_role: Role):
    dsl = ExternalWorkflowDefinition(
        definition=DSLInput(
            **{
                "title": "empty_case_trigger_import",
                "description": "Workflow import with inert case trigger",
                "entrypoint": {"expects": {}, "ref": None},
                "actions": [
                    {
                        "ref": "entrypoint_1",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_1"},
                    }
                ],
                "returns": "${{ ACTIONS.entrypoint_1.result }}",
            }
        ),
        case_trigger=CaseTriggerConfig(
            status="offline",
            event_types=[],
            tag_filters=[],
            event_filters=CaseTriggerEventFilters(),
        ),
    )

    with patch(
        "tracecat.workflow.management.management.CaseTriggersService"
    ) as case_trigger_service_cls:
        async with WorkflowsManagementService.with_session(test_role) as service:
            workflow = await service.create_workflow_from_external_definition(
                dsl.model_dump(mode="json")
            )

        assert workflow.id is not None
        case_trigger_service_cls.assert_not_called()


@pytest.mark.anyio
async def test_workflow_import_applies_layout(test_role: Role):
    dsl = ExternalWorkflowDefinition(
        definition=DSLInput(
            **{
                "title": "layout_import",
                "description": "Workflow import with layout",
                "entrypoint": {"expects": {}, "ref": None},
                "actions": [
                    {
                        "ref": "entrypoint_1",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_1"},
                    }
                ],
                "returns": "${{ ACTIONS.entrypoint_1.result }}",
            }
        ),
        layout=WorkflowLayout(
            trigger=WorkflowLayoutPosition(x=12.0, y=24.0),
            viewport=WorkflowLayoutViewport(x=30.0, y=40.0, zoom=1.5),
            actions=[
                WorkflowLayoutActionPosition(ref="entrypoint_1", x=100.0, y=200.0)
            ],
        ),
    )

    async with WorkflowsManagementService.with_session(test_role) as service:
        workflow = await service.create_workflow_from_external_definition(
            dsl.model_dump(mode="json")
        )
        await service.session.refresh(workflow, ["actions"])

        assert workflow.trigger_position_x == 12.0
        assert workflow.trigger_position_y == 24.0
        assert workflow.viewport_x == 30.0
        assert workflow.viewport_y == 40.0
        assert workflow.viewport_zoom == 1.5
        assert workflow.actions is not None
        assert len(workflow.actions) == 1
        assert workflow.actions[0].position_x == 100.0
        assert workflow.actions[0].position_y == 200.0


@pytest.mark.anyio
async def test_workflow_import_prefers_explicit_layout_over_exported_layout(
    test_role: Role,
):
    dsl = ExternalWorkflowDefinition(
        definition=DSLInput(
            **{
                "title": "layout_override_import",
                "description": "Workflow import with explicit layout override",
                "entrypoint": {"expects": {}, "ref": None},
                "actions": [
                    {
                        "ref": "entrypoint_1",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_1"},
                    }
                ],
                "returns": "${{ ACTIONS.entrypoint_1.result }}",
            }
        ),
        layout=WorkflowLayout(
            trigger=WorkflowLayoutPosition(x=12.0, y=24.0),
            viewport=WorkflowLayoutViewport(x=30.0, y=40.0, zoom=1.5),
            actions=[
                WorkflowLayoutActionPosition(ref="entrypoint_1", x=100.0, y=200.0)
            ],
        ),
    )

    async with WorkflowsManagementService.with_session(test_role) as service:
        workflow = await service.create_workflow_from_external_definition(
            dsl.model_dump(mode="json"),
            trigger_position=(1.0, 2.0),
            viewport=(3.0, 4.0, 0.75),
            action_positions={"entrypoint_1": (5.0, 6.0)},
        )
        await service.session.refresh(workflow, ["actions"])

        assert workflow.trigger_position_x == 1.0
        assert workflow.trigger_position_y == 2.0
        assert workflow.viewport_x == 3.0
        assert workflow.viewport_y == 4.0
        assert workflow.viewport_zoom == 0.75
        assert workflow.actions is not None
        assert len(workflow.actions) == 1
        assert workflow.actions[0].position_x == 5.0
        assert workflow.actions[0].position_y == 6.0
