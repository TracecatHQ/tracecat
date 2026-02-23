"""Large collection regression tests for looped subflows and scatter/gather.

These tests validate two historical regressions:
1. Looped subflow indexed trigger input retrieval previously depended on S3 Select
   over chunk JSON and could fail with OverMaxRecordSize for >1MB records.
2. Scatter/gather previously stored raw values in chunk pages and could drive
   expensive data movement for large payloads.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable
from typing import Any

import orjson
import pytest
from botocore.exceptions import ClientError

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures("registry_version_with_manifest"),
]

from temporalio.client import Client
from temporalio.worker import Worker

from tests.shared import TEST_WF_ID, generate_test_exec_id, to_data
from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.dsl.common import RETRY_POLICIES, DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.enums import WaitStrategy
from tracecat.dsl.schemas import ActionStatement, GatherArgs, ScatterArgs
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage import blob
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    StoredObjectValidator,
    get_object_storage,
)
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowUpdate

type WorkerFactory = Callable[[Client], Worker]

ITEM_COUNT = 25
MASSIVE_ITEM_COUNT = 50
ITEM_SIZE_BYTES = 2 * 1024 * 1024


async def _create_and_commit_workflow(
    dsl: DSLInput,
    role: Role,
    *,
    alias: str | None = None,
):
    async with get_async_session_context_manager() as session:
        mgmt_service = WorkflowsManagementService(session, role=role)
        res = await mgmt_service.create_workflow_from_dsl(
            dsl.model_dump(), skip_secret_validation=True
        )
        workflow = res.workflow
        if not workflow:
            pytest.fail("Workflow wasn't created")
        workflow_id = WorkflowUUID.new(workflow.id)
        if alias:
            await mgmt_service.update_workflow(workflow_id, WorkflowUpdate(alias=alias))
        committed_dsl = await mgmt_service.build_dsl_from_workflow(workflow)

        defn_service = WorkflowDefinitionsService(session, role=role)
        await defn_service.create_workflow_definition(
            workflow_id=workflow_id, dsl=committed_dsl, alias=alias
        )
        return workflow


async def _run_workflow(
    *,
    temporal_client: Client,
    worker_factory: WorkerFactory,
    executor_worker_factory: WorkerFactory,
    wf_exec_id: str,
    run_args: DSLRunArgs,
):
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    async with (
        worker_factory(temporal_client),
        executor_worker_factory(temporal_client),
    ):
        return await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )


def _build_large_items(
    *,
    item_count: int,
    item_size_bytes: int,
    sentinel: str,
) -> list[dict[str, Any]]:
    payload_padding = item_size_bytes - len(sentinel)
    if payload_padding <= 0:
        raise ValueError("item_size_bytes must exceed sentinel length")
    payload = sentinel + ("x" * payload_padding)
    return [{"idx": i, "payload": payload} for i in range(item_count)]


def _configure_storage_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    access_key = (
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ROOT_USER")
        or "minio"
    )
    secret_key = (
        os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
        or "password"
    )
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", access_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret_key)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("TRACECAT__DB_ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setenv("TRACECAT__LOCAL_REPOSITORY_ENABLED", "1")
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)


async def _assert_refs_layout(
    *,
    collection: CollectionObject | None = None,
    manifest_key: str | None = None,
    bucket: str | None = None,
    item_count: int,
    sentinel: str,
) -> None:
    if collection is not None:
        manifest_key = collection.manifest_ref.key
        bucket = collection.manifest_ref.bucket
    if manifest_key is None or bucket is None:
        raise ValueError("collection or (manifest_key and bucket) must be provided")

    manifest_bytes = await blob.download_file(
        key=manifest_key,
        bucket=bucket,
    )
    manifest = orjson.loads(manifest_bytes)
    assert manifest["kind"] == "tracecat.collection_manifest"
    assert manifest["element_kind"] == "stored_object"
    assert manifest["count"] == item_count

    prefix = manifest_key.removesuffix("/manifest.json")
    key_pattern = re.compile(rf"^{re.escape(prefix)}/items/\d+\.json$")

    for chunk_ref in manifest["chunks"]:
        chunk_bytes = await blob.download_file(
            key=chunk_ref["key"],
            bucket=chunk_ref["bucket"],
        )
        assert sentinel.encode() not in chunk_bytes
        chunk = orjson.loads(chunk_bytes)
        assert chunk["kind"] == "tracecat.collection_chunk"
        for item in chunk["items"]:
            stored = StoredObjectValidator.validate_python(item)
            assert isinstance(stored, ExternalObject)
            assert key_pattern.match(stored.ref.key), (
                f"Expected key pattern {key_pattern.pattern}, got {stored.ref.key}"
            )


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.regression
async def test_looped_subflow_large_payload_no_s3_select_dependency(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: looped subflow indexed retrieval must not depend on S3 Select.

    Historical failure mode:
    - Indexed retrieval for CollectionObject trigger inputs depended on S3 Select.
    - Large JSON records (>1MB) could fail with OverMaxRecordSize.

    This test verifies:
    - A looped subflow over 25x2MB payloads succeeds end-to-end.
    - Execution does not regress to the old S3 Select failure path.
    """

    async def over_max_record_size(key: str, bucket: str, expression: str) -> bytes:
        del key, bucket, expression
        raise ClientError(
            {
                "Error": {
                    "Code": "OverMaxRecordSize",
                    "Message": (
                        "The character number in one record is more than our max "
                        "threshold, maxCharsPerRecord: 1,048,576"
                    ),
                }
            },
            "SelectObjectContent",
        )

    monkeypatch.setattr(blob, "select_object_content", over_max_record_size)
    _configure_storage_credentials(monkeypatch)

    sentinel = f"__looped-subflow-sentinel-{uuid.uuid4().hex}__"
    items = _build_large_items(
        item_count=ITEM_COUNT,
        item_size_bytes=ITEM_SIZE_BYTES,
        sentinel=sentinel,
    )
    storage = get_object_storage()
    trigger_inputs = await storage.store(
        f"tests/regression/looped-subflow/{uuid.uuid4().hex}/trigger.json",
        {"items": items},
    )

    child_alias = f"large-child-{uuid.uuid4().hex[:8]}"
    child_dsl = DSLInput(
        title="Child passthrough",
        description="Returns trigger payload unchanged",
        entrypoint=DSLEntrypoint(ref="identity"),
        actions=[
            ActionStatement(
                ref="identity",
                action="core.transform.reshape",
                args={"value": "${{ TRIGGER }}"},
            )
        ],
        returns="${{ ACTIONS.identity.result }}",
    )
    _ = await _create_and_commit_workflow(child_dsl, test_role, alias=child_alias)

    parent_dsl = DSLInput(
        title="Looped subflow with large payloads",
        description="Execute child with for_each over externalized trigger items",
        entrypoint=DSLEntrypoint(ref="call_child"),
        actions=[
            ActionStatement(
                ref="call_child",
                action="core.workflow.execute",
                for_each="${{ for var.item in TRIGGER.items }}",
                args={
                    "workflow_alias": child_alias,
                    "trigger_inputs": "${{ var.item }}",
                    "wait_strategy": WaitStrategy.WAIT.value,
                },
            ),
        ],
        returns="${{ ACTIONS.call_child.result }}",
    )

    wf_exec_id = generate_test_exec_id(
        f"{test_looped_subflow_large_payload_no_s3_select_dependency.__name__}-{uuid.uuid4().hex[:8]}"
    )
    run_args = DSLRunArgs(dsl=parent_dsl, role=test_role, wf_id=TEST_WF_ID)
    run_args.trigger_inputs = trigger_inputs
    result = await _run_workflow(
        temporal_client=temporal_client,
        worker_factory=test_worker_factory,
        executor_worker_factory=test_executor_worker_factory,
        wf_exec_id=wf_exec_id,
        run_args=run_args,
    )
    data = await to_data(result)
    assert isinstance(data, list)
    assert len(data) == ITEM_COUNT
    first = data[0]
    last = data[-1]
    if "item" in first and isinstance(first["item"], dict):
        first = first["item"]
    if "item" in last and isinstance(last["item"], dict):
        last = last["item"]
    assert first["payload"].startswith(sentinel)
    assert last["payload"].startswith(sentinel)


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.regression
async def test_scatter_gather_large_payload_stores_refs_not_raw_chunk_values(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: scatter/gather collection chunks must store refs, not payloads.

    Historical performance/pathology:
    - Scatter/gather chunk pages stored raw values (element_kind='value').
    - Large page payloads increased data movement and memory pressure.
    - Indexed scatter item materialization could effectively materialize full
      collections instead of only the indexed item path.

    This test verifies:
    - Scatter and gather outputs are CollectionObject handles.
    - Manifest/chunks use element_kind='stored_object' (refs-only chunks).
    - Chunk JSON does not embed the large payload sentinel.
    """
    _configure_storage_credentials(monkeypatch)
    sentinel = f"__scatter-gather-sentinel-{uuid.uuid4().hex}__"
    items = _build_large_items(
        item_count=ITEM_COUNT,
        item_size_bytes=ITEM_SIZE_BYTES,
        sentinel=sentinel,
    )
    storage = get_object_storage()
    trigger_inputs = await storage.store(
        f"tests/regression/scatter-gather/{uuid.uuid4().hex}/trigger.json",
        {"items": items},
    )

    dsl = DSLInput(
        title="Scatter gather large refs layout",
        description="Scatter/gather over large externalized trigger payload",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(collection="${{ TRIGGER.items }}").model_dump(),
            ),
            ActionStatement(
                ref="reshape",
                action="core.transform.reshape",
                depends_on=["scatter"],
                args={"value": "${{ ACTIONS.scatter.result }}"},
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["reshape"],
                args=GatherArgs(items="${{ ACTIONS.reshape.result }}").model_dump(),
            ),
        ],
    )

    wf_exec_id = generate_test_exec_id(
        f"{test_scatter_gather_large_payload_stores_refs_not_raw_chunk_values.__name__}-{uuid.uuid4().hex[:8]}"
    )
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    run_args.trigger_inputs = trigger_inputs
    result = await _run_workflow(
        temporal_client=temporal_client,
        worker_factory=test_worker_factory,
        executor_worker_factory=test_executor_worker_factory,
        wf_exec_id=wf_exec_id,
        run_args=run_args,
    )

    raw_context = await to_data(result)
    actions = raw_context["ACTIONS"]
    assert "gather" in actions, (
        f"Expected gather action in context, got: {actions.keys()}"
    )
    gather_stored = StoredObjectValidator.validate_python(actions["gather"]["result"])
    assert isinstance(gather_stored, CollectionObject)
    await _assert_refs_layout(
        collection=gather_stored,
        item_count=ITEM_COUNT,
        sentinel=sentinel,
    )

    # Depending on return/context shape, intermediate actions may be omitted from
    # the final ACTIONS map. Scatter output location is deterministic per stream.
    scatter_manifest_key = (
        f"{test_role.workspace_id}/{wf_exec_id}/actions/<root>:0/scatter/manifest.json"
    )
    await _assert_refs_layout(
        manifest_key=scatter_manifest_key,
        bucket=gather_stored.manifest_ref.bucket,
        item_count=ITEM_COUNT,
        sentinel=sentinel,
    )

    first = await storage.retrieve(gather_stored.at(0))
    last = await storage.retrieve(gather_stored.at(ITEM_COUNT - 1))
    assert first["idx"] == 0
    assert last["idx"] == ITEM_COUNT - 1
    assert first["payload"].startswith(sentinel)
    assert last["payload"].startswith(sentinel)


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.regression
async def test_scatter_gather_massive_payload_50x2mb_e2e(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E plumbing test for a massive payload (50 items x 2MB each).

    This validates that a ~100MB logical payload can flow through scatter/gather
    without hanging and with refs-only collection chunk layout.
    """
    _configure_storage_credentials(monkeypatch)
    sentinel = f"__massive-scatter-gather-sentinel-{uuid.uuid4().hex}__"
    items = _build_large_items(
        item_count=MASSIVE_ITEM_COUNT,
        item_size_bytes=ITEM_SIZE_BYTES,
        sentinel=sentinel,
    )
    storage = get_object_storage()
    trigger_inputs = await storage.store(
        f"tests/regression/scatter-gather-massive/{uuid.uuid4().hex}/trigger.json",
        {"items": items},
    )

    dsl = DSLInput(
        title="Massive scatter gather e2e",
        description="Plumb 50x2MB payload through scatter/gather",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(collection="${{ TRIGGER.items }}").model_dump(),
            ),
            ActionStatement(
                ref="reshape",
                action="core.transform.reshape",
                depends_on=["scatter"],
                args={"value": "${{ ACTIONS.scatter.result }}"},
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["reshape"],
                args=GatherArgs(items="${{ ACTIONS.reshape.result }}").model_dump(),
            ),
        ],
    )

    wf_exec_id = generate_test_exec_id(
        f"{test_scatter_gather_massive_payload_50x2mb_e2e.__name__}-{uuid.uuid4().hex[:8]}"
    )
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    run_args.trigger_inputs = trigger_inputs
    result = await _run_workflow(
        temporal_client=temporal_client,
        worker_factory=test_worker_factory,
        executor_worker_factory=test_executor_worker_factory,
        wf_exec_id=wf_exec_id,
        run_args=run_args,
    )

    raw_context = await to_data(result)
    actions = raw_context["ACTIONS"]
    assert "gather" in actions, (
        f"Expected gather action in context, got: {actions.keys()}"
    )
    gather_stored = StoredObjectValidator.validate_python(actions["gather"]["result"])
    assert isinstance(gather_stored, CollectionObject)
    await _assert_refs_layout(
        collection=gather_stored,
        item_count=MASSIVE_ITEM_COUNT,
        sentinel=sentinel,
    )

    first = await storage.retrieve(gather_stored.at(0))
    last = await storage.retrieve(gather_stored.at(MASSIVE_ITEM_COUNT - 1))
    assert first["idx"] == 0
    assert last["idx"] == MASSIVE_ITEM_COUNT - 1
    assert first["payload"].startswith(sentinel)
    assert last["payload"].startswith(sentinel)
