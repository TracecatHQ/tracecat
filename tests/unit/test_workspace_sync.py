"""Tests for workspace Git sync projection primitives."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CaseEventType
from tracecat.db.models import Workflow
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.workflow.store.schemas import RemoteWorkflowDefinition
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import WorkspaceManifest
from tracecat.workspace_sync.serialization import canonical_json_text
from tracecat.workspace_sync.service import WorkspaceGitSyncService
from tracecat.workspace_sync.workflow import (
    parse_workflow_spec,
    serialize_workflow_spec,
    workflow_spec_from_orm,
)


@pytest.fixture
def sample_dsl() -> DSLInput:
    return DSLInput(
        title="Detect Okta Risk",
        description="Detects suspicious Okta activity",
        entrypoint=DSLEntrypoint(ref="start", expects={}),
        actions=[
            ActionStatement(
                ref="start",
                action="core.transform.passthrough",
                args={"value": "test"},
            )
        ],
    )


def test_manifest_serializes_as_canonical_json() -> None:
    text = canonical_json_text(WorkspaceManifest())

    assert (
        text
        == '{\n  "resources": {\n    "workflows": "workflows/"\n  },\n  "version": 1\n}\n'
    )


def test_workflow_spec_does_not_serialize_local_uuid(sample_dsl: DSLInput) -> None:
    local_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=local_id,
        alias="okta-risk",
        tags=[],
        folder=None,
        schedules=[],
        webhook=SimpleNamespace(methods=["POST"], status="online"),
        case_trigger=SimpleNamespace(
            status="offline",
            event_types=[],
            tag_filters=[],
        ),
    )

    spec = workflow_spec_from_orm(
        cast(Workflow, workflow),
        dsl=sample_dsl,
        source_id="detect-okta-risk",
    )
    content = serialize_workflow_spec(spec)

    assert "detect-okta-risk" in content
    assert str(local_id) not in content
    assert "wf_" not in content


def test_workflow_spec_includes_configured_case_trigger(sample_dsl: DSLInput) -> None:
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        alias="okta-risk",
        tags=[],
        folder=None,
        schedules=[],
        webhook=SimpleNamespace(methods=["POST"], status="online"),
        case_trigger=SimpleNamespace(
            status="online",
            event_types=[CaseEventType.CASE_CREATED.value],
            tag_filters=["phishing"],
        ),
    )

    spec = workflow_spec_from_orm(
        cast(Workflow, workflow),
        dsl=sample_dsl,
        source_id="detect-okta-risk",
    )

    assert spec.case_trigger is not None
    assert spec.case_trigger.status == "online"
    assert spec.case_trigger.event_types == [CaseEventType.CASE_CREATED]
    assert spec.case_trigger.tag_filters == ["phishing"]


def test_legacy_workflow_file_dual_reads_to_source_id(
    sample_dsl: DSLInput,
) -> None:
    legacy = RemoteWorkflowDefinition(
        id="wf_0000000000000000000001",
        alias="legacy-workflow",
        definition=sample_dsl,
    )
    content = yaml.safe_dump(
        legacy.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
    )

    spec, diagnostic = parse_workflow_spec(
        "workflows/legacy-source/definition.yml",
        content,
    )

    assert diagnostic is None
    assert spec is not None
    assert spec.id == "legacy-source"
    assert spec.alias == "legacy-workflow"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_resource_mapping_stores_source_id_to_local_uuid(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = WorkspaceGitSyncService(session=session, role=svc_role)
    local_id = uuid.uuid4()

    mapping = await service._ensure_resource_mapping(
        resource_type=SyncResourceType.WORKFLOW.value,
        local_id=local_id,
        preferred_source_id="detect-okta-risk",
        source_path="workflows/detect-okta-risk/definition.yml",
        create=True,
        reserved_source_ids=set(),
    )

    assert mapping is not None
    assert mapping.source_id == "detect-okta-risk"
    assert mapping.local_id == local_id
    assert mapping.workspace_id == svc_role.workspace_id
