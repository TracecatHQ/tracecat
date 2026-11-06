"""Durable pushes write sync mappings with the path each resource lands at.

New mappings are inserted. Existing ones keep their identity and get the new
path, which later pushes use to keep a resource in its folder.
"""

import uuid

import pytest
from sqlalchemy import select

from tracecat.db.models import WorkspaceSyncResourceMapping
from tracecat.workspace_sync.operations.domain import DurableSyncService
from tracecat.workspace_sync.types import SyncMappingTarget


@pytest.mark.anyio
async def test_write_inserts_new_and_refreshes_only_same_identity(session, svc_role):
    assert svc_role.workspace_id is not None
    sync = DurableSyncService(session, svc_role)
    moved_local_id = uuid.uuid4()
    other_local_id = uuid.uuid4()
    session.add_all(
        [
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=sync._mapping_provider_value,
                resource_type="workflow",
                source_id="wf_moved",
                source_path="workflows/wf_moved/definition.yml",
                local_id=moved_local_id,
            ),
            WorkspaceSyncResourceMapping(
                workspace_id=svc_role.workspace_id,
                provider=sync._mapping_provider_value,
                resource_type="workflow",
                source_id="wf_repointed",
                source_path="workflows/Old/definition.yml",
                local_id=other_local_id,
            ),
        ]
    )
    await session.flush()

    await sync._write_mapping_targets(
        [
            SyncMappingTarget(
                resource_type="workflow",
                source_id="wf_moved",
                source_path="workflows/Triage/definition.yml",
                local_id=moved_local_id,
            ),
            # Same source id but a different local row: never re-pointed.
            SyncMappingTarget(
                resource_type="workflow",
                source_id="wf_repointed",
                source_path="workflows/New/definition.yml",
                local_id=uuid.uuid4(),
            ),
            SyncMappingTarget(
                resource_type="workflow",
                source_id="wf_new",
                source_path="workflows/Fresh/definition.yml",
                local_id=uuid.uuid4(),
            ),
        ]
    )

    rows = dict(
        (
            await session.execute(
                select(
                    WorkspaceSyncResourceMapping.source_id,
                    WorkspaceSyncResourceMapping.source_path,
                ).where(
                    WorkspaceSyncResourceMapping.workspace_id == svc_role.workspace_id
                )
            )
        )
        .tuples()
        .all()
    )
    assert rows == {
        "wf_moved": "workflows/Triage/definition.yml",
        "wf_repointed": "workflows/Old/definition.yml",
        "wf_new": "workflows/Fresh/definition.yml",
    }
