import json
import uuid
from typing import Any, cast

import pytest

from tracecat.auth.types import Role
from tracecat.db.models import Action, Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.expressions.expectations import ExpectedField
from tracecat.workflow.management import management
from tracecat.workflow.management.management import WorkflowsManagementService


class _ScalarResult:
    def __init__(self, value: Any):
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _FakeSession:
    def __init__(self, *, graph_version: int, actions: list[Action]):
        self._graph_version = graph_version
        self._actions = actions
        self.execute_count = 0

    async def execute(self, _statement: Any) -> _ScalarResult:
        self.execute_count += 1
        if self.execute_count == 1:
            return _ScalarResult(self._graph_version)
        return _ScalarResult(None)

    async def flush(self) -> None:
        return None

    async def refresh(
        self, instance: Any, attribute_names: list[str] | None = None
    ) -> None:
        if (
            isinstance(instance, Workflow)
            and attribute_names
            and "actions" in attribute_names
        ):
            instance.actions = self._actions


class _FakeWorkflowGraphService:
    def __init__(self, _session: Any, *, role: Role):
        self.role = role

    async def apply_operations(self, **_kwargs: Any) -> None:
        return None


def test_workflow_fields_from_dsl_serializes_expected_fields() -> None:
    dsl = DSLInput(
        **{
            "title": "Workflow with expects",
            "description": "Ensures expects can be written to JSONB columns",
            "entrypoint": {
                "ref": "start",
                "expects": {
                    "report_url": ExpectedField(type="str", optional=True),
                    "report_text": ExpectedField(type="str", optional=True),
                },
            },
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "ok"},
                }
            ],
        }
    )

    fields = WorkflowsManagementService._workflow_fields_from_dsl(dsl)

    assert fields["expects"] == {
        "report_url": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
        "report_text": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
    }
    json.dumps(fields["expects"])


@pytest.mark.anyio
async def test_restore_workflow_definition_serializes_expected_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=workspace_id,
        scopes=frozenset({"*"}),
    )
    workflow = Workflow(
        id=workflow_id,
        workspace_id=workspace_id,
        title="Current workflow",
        description="Current description",
        expects={},
        config={},
        graph_version=1,
        version=1,
    )
    definition = WorkflowDefinition(
        workspace_id=workspace_id,
        workflow_id=workflow_id,
        version=2,
        alias="restored-alias",
        registry_lock=None,
        content={
            "title": "Restored workflow",
            "description": "Restored description",
            "entrypoint": {
                "ref": "start",
                "expects": {
                    "report_url": {"type": "str", "optional": True},
                    "report_text": {"type": "str", "optional": True},
                },
            },
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "ok"},
                }
            ],
        },
    )
    actions = [
        Action(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            type="core.transform.reshape",
            title="start",
            description="",
            inputs="",
            control_flow={},
        )
    ]
    fake_session = _FakeSession(graph_version=workflow.graph_version, actions=actions)
    service = WorkflowsManagementService(cast(Any, fake_session), role=role)

    async def create_actions_from_dsl(
        self: WorkflowsManagementService,
        dsl: DSLInput,
        workflow_id: uuid.UUID,
    ) -> None:
        return None

    monkeypatch.setattr(
        WorkflowsManagementService,
        "create_actions_from_dsl",
        create_actions_from_dsl,
    )
    monkeypatch.setattr(management, "WorkflowGraphService", _FakeWorkflowGraphService)

    restored = await service.restore_workflow_definition(workflow, definition)

    assert restored.expects == {
        "report_url": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
        "report_text": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
    }
    json.dumps(restored.expects)
