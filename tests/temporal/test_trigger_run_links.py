"""``TracecatTriggerId`` against a real Temporal server (the dev server's SQL
visibility).

Proves the assumptions the setter's gate rests on: a start with the attribute
fails while the namespace lacks it, the probe query fails then too and passes
once it is registered, and the queries the read side sends find the run.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.client import Client
from temporalio.common import TypedSearchAttributes
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment

from tracecat.workflow.executions import trigger_link
from tracecat.workflow.executions.common import build_query
from tracecat.workflow.executions.enums import TemporalSearchAttr

pytestmark = [pytest.mark.temporal, pytest.mark.integration]

_OTHER_ATTRS = [a for a in TemporalSearchAttr if a is not TemporalSearchAttr.TRIGGER_ID]


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    """A namespace with every attribute but ``TracecatTriggerId``, as before a
    deployment registers it."""
    async with await WorkflowEnvironment.start_local(
        search_attributes=[a.key for a in _OTHER_ATTRS],
        dev_server_log_level="error",
    ) as environment:
        yield environment


async def _register_trigger_id(client: Client) -> None:
    await client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace=client.namespace,
            search_attributes={
                TemporalSearchAttr.TRIGGER_ID.value: (
                    IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
                )
            },
        )
    )


async def _start(
    client: Client, workspace_id: uuid.UUID, trigger_id: uuid.UUID | None
) -> str:
    """Start a run no worker picks up; it stays open and listed."""
    pairs = [TemporalSearchAttr.WORKSPACE_ID.create_pair(str(workspace_id))]
    if trigger_id is not None:
        pairs.append(TemporalSearchAttr.TRIGGER_ID.create_pair(str(trigger_id)))
    workflow_id = f"trigger-link-{uuid.uuid4()}"
    await client.start_workflow(
        "DSLWorkflow",
        id=workflow_id,
        task_queue=f"no-worker-{uuid.uuid4()}",
        search_attributes=TypedSearchAttributes(search_attributes=pairs),
    )
    return workflow_id


async def _ids(client: Client, query: str, expected: int) -> set[str]:
    """Workflow IDs matching a query, waiting out visibility lag."""
    ids: set[str] = set()
    for _ in range(50):
        ids = {e.id async for e in client.list_workflows(query)}
        if len(ids) >= expected:
            break
        await asyncio.sleep(0.2)
    return ids


@pytest.mark.anyio
async def test_the_gate_holds_until_registration_then_runs_are_found(
    env: WorkflowEnvironment,
) -> None:
    client = env.client
    workspace_id = uuid.uuid4()
    trigger_id = uuid.uuid4()

    with patch.object(trigger_link, "_probe", trigger_link._ProbeState()):
        # Unregistered: a start that sets it fails, and so does the probe.
        with pytest.raises(RPCError) as e:
            await _start(client, workspace_id, trigger_id)
        assert e.value.status == RPCStatusCode.INVALID_ARGUMENT
        assert await trigger_link.trigger_id_pairs(client, trigger_id) == []

        await _register_trigger_id(client)
        # The next probe after the retry window passes.
        trigger_link._probe.next_probe_at = 0.0
        for _ in range(50):
            if await trigger_link.trigger_id_registered(client):
                break
            trigger_link._probe.next_probe_at = 0.0
            await asyncio.sleep(0.2)
        pairs = await trigger_link.trigger_id_pairs(client, trigger_id)
        assert [p.value for p in pairs] == [str(trigger_id)]

    linked = await _start(client, workspace_id, trigger_id)
    unlinked = await _start(client, workspace_id, None)
    other_workspace = await _start(client, uuid.uuid4(), trigger_id)

    assert await _ids(client, build_query(workspace_id=workspace_id), 2) == {
        linked,
        unlinked,
    }
    # The recent runs query: the trigger's runs in this workspace only, so
    # neither the unlinked run nor the other workspace's run.
    assert other_workspace != linked
    by_trigger = build_query(
        workspace_id=workspace_id,
        trigger_id=trigger_id,
        workflow_types={"DSLWorkflow"},
    )
    assert await _ids(client, by_trigger, 1) == {linked}
    # The session origin query's attribute test.
    has_trigger = " AND ".join(
        (
            build_query(workspace_id=workspace_id),
            f"{TemporalSearchAttr.TRIGGER_ID.value} IS NOT NULL",
        )
    )
    assert await _ids(client, has_trigger, 1) == {linked}
