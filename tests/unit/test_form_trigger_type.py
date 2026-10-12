"""Tests for the ``form`` workflow trigger type."""

import datetime
import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.client import Client

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.dsl.init_activities import (
    ResolveTimeAnchorActivityInputs,
    resolve_time_anchor_activity,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType
from tracecat.workflow.executions.service import WorkflowExecutionsService


@pytest.fixture
def runner_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-runner"],
    )


@pytest.fixture
def dsl() -> DSLInput:
    return DSLInput.model_validate(
        {
            "title": "Form trigger workflow",
            "description": "Test workflow",
            "entrypoint": {"ref": "start"},
            "actions": [{"ref": "start", "action": "core.noop"}],
        }
    )


def test_trigger_type_values() -> None:
    assert [member.value for member in TriggerType] == [
        "manual",
        "scheduled",
        "webhook",
        "case",
        "form",
    ]
    assert TriggerType("form") is TriggerType.FORM


def test_form_trigger_search_attribute_pair() -> None:
    pair = TriggerType.FORM.to_temporal_search_attr_pair()

    assert pair.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
    assert pair.value == "form"


def test_form_trigger_uses_workflow_start_time_as_anchor() -> None:
    start_time = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
    scheduled_start_time = start_time - datetime.timedelta(hours=1)

    anchor = resolve_time_anchor_activity(
        ResolveTimeAnchorActivityInputs(
            trigger_type=TriggerType.FORM,
            start_time=start_time,
            scheduled_start_time=scheduled_start_time,
        )
    )

    assert anchor == start_time


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method_name", "temporal_method"),
    [
        ("create_workflow_execution", "execute_workflow"),
        ("create_workflow_execution_wait_for_start", "start_workflow"),
    ],
)
async def test_form_trigger_mints_time_anchor_and_search_attribute(
    runner_role: Role,
    dsl: DSLInput,
    method_name: str,
    temporal_method: str,
) -> None:
    client = Mock(spec=Client)
    client.execute_workflow = AsyncMock(return_value=None)
    client.start_workflow = AsyncMock(return_value=Mock())
    service = WorkflowExecutionsService(client=client, role=runner_role)
    create_execution = {
        "create_workflow_execution": service.create_workflow_execution,
        "create_workflow_execution_wait_for_start": (
            service.create_workflow_execution_wait_for_start
        ),
    }[method_name]
    before = datetime.datetime.now(datetime.UTC)

    with patch.object(
        service, "_resolve_execution_timeout", AsyncMock(return_value=None)
    ):
        await create_execution(
            dsl=dsl,
            wf_id=WorkflowUUID.new_uuid4(),
            payload=None,
            trigger_type=TriggerType.FORM,
        )

    temporal_call = {
        "execute_workflow": client.execute_workflow,
        "start_workflow": client.start_workflow,
    }[temporal_method]
    temporal_call.assert_awaited_once()
    run_args = temporal_call.await_args.args[1]
    assert isinstance(run_args, DSLRunArgs)
    assert run_args.time_anchor is not None
    assert run_args.time_anchor >= before

    pairs = temporal_call.await_args.kwargs["search_attributes"].search_attributes
    values = {pair.key.name: pair.value for pair in pairs}
    assert values[TemporalSearchAttr.TRIGGER_TYPE.value] == "form"
    assert values[TemporalSearchAttr.TRIGGERED_BY_USER_ID.value] == str(
        runner_role.user_id
    )
