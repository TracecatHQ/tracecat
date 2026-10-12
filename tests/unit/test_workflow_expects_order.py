"""Tests for preserving the author's key order of workflow inputs.

PostgreSQL JSONB does not keep object key order, so the order is stored as a
list next to ``expects`` and reapplied on read. The list is internal: it must
never appear in dumps, exports, Temporal payloads or the OpenAPI schema.
"""

import uuid
from typing import Any

import orjson
import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.db.models import Workflow, WorkflowDefinition
from tracecat.dsl._converter import PydanticPayloadConverter
from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    DSLRunArgs,
    apply_expects_order,
    expects_key_order,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.draft import (
    build_workflow_edit_document,
    replace_workflow_definition_from_dsl,
)
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.router import _build_workflow_read
from tracecat.workflow.management.schemas import (
    ExternalWorkflowDefinition,
    WorkflowCreate,
    WorkflowUpdate,
)
from tracecat.workspace_sync.serialization import serialize_yaml_model

pytestmark = pytest.mark.usefixtures("db")

# JSONB orders object keys by length and then bytewise: a, mm, zeta.
AUTHOR_ORDER = ["zeta", "mm", "a"]
JSONB_ORDER = ["a", "mm", "zeta"]
EXPECTS: dict[str, dict[str, Any]] = {
    "zeta": {"type": "str"},
    "mm": {"type": "int", "default": 1},
    "a": {"type": "bool", "default": False},
}


def _dsl_data(expects: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "title": "Input order workflow",
        "description": "Test workflow",
        "entrypoint": {"ref": "start", "expects": expects or EXPECTS},
        "actions": [{"ref": "start", "action": "core.transform.reshape"}],
    }


def _stored_entrypoint() -> dict[str, Any]:
    """An entrypoint as it comes back from JSONB: sorted keys plus the order."""
    return {
        "ref": "start",
        "expects": {name: EXPECTS[name] for name in JSONB_ORDER},
        "expects_order": AUTHOR_ORDER,
    }


def _stored_dsl() -> DSLInput:
    return DSLInput.model_validate({**_dsl_data(), "entrypoint": _stored_entrypoint()})


def test_apply_expects_order_handles_missing_and_stale_names() -> None:
    expects = {"a": 1, "b": 2, "c": 3}

    assert apply_expects_order(None, ["a"]) is None
    assert list(apply_expects_order(expects, None) or {}) == ["a", "b", "c"]
    # Names no longer in the schema are ignored; unlisted names keep their place
    # after the listed ones.
    assert list(apply_expects_order(expects, ["c", "gone", "a"]) or {}) == [
        "c",
        "a",
        "b",
    ]


def test_expects_key_order() -> None:
    assert expects_key_order(EXPECTS) == AUTHOR_ORDER
    assert expects_key_order({}) is None
    assert expects_key_order(None) is None


def test_entrypoint_reorders_expects_on_validation() -> None:
    entrypoint = DSLEntrypoint.model_validate(_stored_entrypoint())

    assert list(entrypoint.expects or {}) == AUTHOR_ORDER
    assert entrypoint.expects_order is None
    assert entrypoint == DSLEntrypoint.model_validate(
        {"ref": "start", "expects": EXPECTS}
    )


@pytest.mark.parametrize(
    "dump_kwargs",
    [
        {},
        {"mode": "json"},
        {"exclude_unset": True},
        {"exclude_none": True},
        {"by_alias": True, "exclude_defaults": True},
    ],
)
def test_expects_order_is_not_in_model_dump(dump_kwargs: dict[str, Any]) -> None:
    dsl = _stored_dsl()

    dumped = dsl.model_dump(**dump_kwargs)

    assert "expects_order" not in dumped["entrypoint"]
    assert list(dumped["entrypoint"]["expects"]) == AUTHOR_ORDER

    json_kwargs = {k: v for k, v in dump_kwargs.items() if k != "mode"}
    assert "expects_order" not in dsl.model_dump_json(**json_kwargs)


def test_expects_order_is_not_in_model_dump_when_constructed_directly() -> None:
    entrypoint = DSLEntrypoint.model_construct(
        ref="start", expects=None, expects_order=AUTHOR_ORDER
    )

    assert "expects_order" not in entrypoint.model_dump()
    assert "expects_order" not in entrypoint.model_dump_json()


def test_expects_order_is_not_in_yaml_exports() -> None:
    dsl = _stored_dsl()
    external = ExternalWorkflowDefinition(definition=dsl)

    exports = [
        dsl.dump_yaml(),
        # Same calls as the workflow export route.
        yaml.dump(external.model_dump(mode="json", exclude_none=True), indent=2),
        external.model_dump_json(indent=2, exclude_none=True),
        # Workspace sync serializer.
        serialize_yaml_model(dsl),
    ]

    for exported in exports:
        assert "expects_order" not in exported
    synced = yaml.safe_load(exports[-1])
    assert list(synced["entrypoint"]["expects"]) == AUTHOR_ORDER


def test_expects_order_is_not_in_temporal_payloads() -> None:
    args = DSLRunArgs(
        role=Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid.uuid4(),
        ),
        dsl=_stored_dsl(),
        wf_id=WorkflowUUID.new_uuid4(),
    )
    converter = PydanticPayloadConverter()

    [payload] = converter.to_payloads([args])

    assert b"expects_order" not in payload.data
    # The converter sorts keys, so a run sees inputs alphabetically. Readers
    # that need the author's order load the definition, not the run payload.
    [decoded] = converter.from_payloads([payload], [DSLRunArgs])
    assert decoded.dsl is not None
    assert set(decoded.dsl.entrypoint.expects or {}) == set(AUTHOR_ORDER)


def test_expects_order_is_not_in_json_schema_or_openapi() -> None:
    for mode in ("validation", "serialization"):
        schema = DSLInput.model_json_schema(mode=mode)
        assert b"expects_order" not in orjson.dumps(schema)

    assert b"expects_order" not in orjson.dumps(app.openapi())


@pytest.fixture
async def workflow(session: AsyncSession, svc_role: Role) -> Workflow:
    service = WorkflowsManagementService(session, role=svc_role)
    return await service.create_workflow(WorkflowCreate(title="Input order workflow"))


@pytest.mark.anyio
async def test_definition_round_trip_keeps_input_order(
    session: AsyncSession, svc_role: Role, workflow: Workflow
) -> None:
    service = WorkflowDefinitionsService(session, role=svc_role)
    dsl = DSLInput.model_validate(_dsl_data())

    created = await service.create_workflow_definition(
        WorkflowUUID.new(workflow.id), dsl
    )
    defn_id = created.id
    session.expire_all()

    result = await session.execute(
        select(WorkflowDefinition).where(WorkflowDefinition.id == defn_id)
    )
    defn = result.scalar_one()
    # The stored object lost the order; the list beside it kept it.
    assert list(defn.content["entrypoint"]["expects"]) == JSONB_ORDER
    assert defn.content["entrypoint"]["expects_order"] == AUTHOR_ORDER

    loaded = DSLInput.model_validate(defn.content)
    assert list(loaded.entrypoint.expects or {}) == AUTHOR_ORDER
    assert loaded.entrypoint == dsl.entrypoint
    assert "expects_order" not in loaded.model_dump()["entrypoint"]


@pytest.mark.anyio
async def test_definition_without_expects_stores_no_order(
    session: AsyncSession, svc_role: Role, workflow: Workflow
) -> None:
    service = WorkflowDefinitionsService(session, role=svc_role)
    data = _dsl_data()
    data["entrypoint"] = {"ref": "start"}

    defn = await service.create_workflow_definition(
        WorkflowUUID.new(workflow.id), DSLInput.model_validate(data)
    )

    assert "expects_order" not in defn.content["entrypoint"]


@pytest.mark.anyio
async def test_workflow_read_path_keeps_input_order(
    session: AsyncSession, svc_role: Role, workflow: Workflow
) -> None:
    service = WorkflowsManagementService(session, role=svc_role)
    workflow_id = WorkflowUUID.new(workflow.id)

    await service.update_workflow(
        workflow_id, WorkflowUpdate.model_validate({"expects": EXPECTS})
    )

    # update_workflow refreshes the row, so these values come from the database.
    loaded = await service.get_workflow(workflow_id)
    assert loaded is not None
    assert list(loaded.expects) == JSONB_ORDER
    assert loaded.expects_order == AUTHOR_ORDER

    read = _build_workflow_read(loaded)
    assert list(read.expects or {}) == AUTHOR_ORDER
    assert read.expects_schema is not None
    assert list(read.expects_schema["properties"]) == AUTHOR_ORDER
    assert "expects_order" not in read.model_dump()

    document = build_workflow_edit_document(loaded)
    assert list(document.definition.entrypoint.expects or {}) == AUTHOR_ORDER

    await service.update_workflow(workflow_id, WorkflowUpdate(expects=None))
    assert loaded.expects_order is None


@pytest.mark.anyio
async def test_draft_save_from_dsl_keeps_input_order(
    session: AsyncSession, svc_role: Role, workflow: Workflow
) -> None:
    service = WorkflowsManagementService(session, role=svc_role)
    workflow_id = WorkflowUUID.new(workflow.id)
    draft = await service.get_workflow(workflow_id)
    assert draft is not None

    await replace_workflow_definition_from_dsl(
        service=service, workflow=draft, dsl=DSLInput.model_validate(_dsl_data())
    )
    await session.commit()
    session.expire_all()

    loaded = await service.get_workflow(workflow_id)
    assert loaded is not None
    assert loaded.expects_order == AUTHOR_ORDER
    dsl = await service.build_dsl_from_workflow(loaded)
    assert list(dsl.entrypoint.expects or {}) == AUTHOR_ORDER


def test_workflow_fields_from_dsl_capture_input_order() -> None:
    fields = WorkflowsManagementService._workflow_fields_from_dsl(_stored_dsl())

    assert list(fields["expects"]) == AUTHOR_ORDER
    assert fields["expects_order"] == AUTHOR_ORDER
