from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from botocore.exceptions import HTTPClientError
from pydantic import ValidationError
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError

from tests.shared import capture_application_error as _capture_application_error
from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.action import (
    DSLActivities,
    EvaluateLoopedSubflowInputActivityInput,
    EvaluateTemplatedObjectActivityInput,
    FinalizeGatherActivityInput,
    NormalizeTriggerInputsActivityInputs,
    PrepareSubflowActivityInput,
    ResolveSubflowBatchActivityInput,
    SubflowDefinitionNotFoundError,
    SynchronizeCollectionObjectActivityInput,
    _agent_preparation_error_classification,
    _expression_error_classification,
    _materialization_error_classification,
    _result_persistence_error_classification,
)
from tracecat.dsl.common import RETRY_POLICIES, DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.enums import PlatformAction, StreamErrorHandlingStrategy
from tracecat.dsl.error_transport import (
    ActionErrorTransportDetail,
    parse_classified_action_error_payload,
)
from tracecat.dsl.scheduler import _classified_action_error_info
from tracecat.dsl.schemas import ROOT_STREAM, ActionStatement, ExecutionContext
from tracecat.dsl.types import (
    ActionErrorInfo,
)
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.exceptions import TracecatExpressionError, TracecatValidationError
from tracecat.expressions.schemas import ExpectedField
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.storage.backends.inline import InlineObjectStorage
from tracecat.storage.object import (
    CollectionObject,
    InlineObject,
    ObjectRef,
    StoredObject,
)
from tracecat.temporal.errors import (
    build_error_transport_detail,
    extract_error_classification,
)
from tracecat.temporal.exceptions import UserError
from tracecat.temporal.patches import WorkflowPatch
from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType


@dataclass(frozen=True, slots=True)
class _BoundaryPolicyExpectation:
    boundary: str
    classify: Callable[[Exception], RuntimeErrorClassification]
    error_factory: Callable[[], Exception]
    owner: RuntimeErrorOwner
    kind: RuntimeErrorKind
    retry_disposition: RetryDisposition
    retry_policy: str


def _transport_error() -> HTTPClientError:
    return HTTPClientError(error=RuntimeError("transport diagnostic"))


_REMAINING_BOUNDARY_POLICIES: tuple[_BoundaryPolicyExpectation, ...] = (
    _BoundaryPolicyExpectation(
        "return.materialization",
        _materialization_error_classification,
        _transport_error,
        RuntimeErrorOwner.PLATFORM,
        RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        RetryDisposition.RETRYABLE,
        "activity:fail_fast",
    ),
    _BoundaryPolicyExpectation(
        "return.expression",
        _expression_error_classification,
        lambda: TracecatExpressionError("invalid return expression"),
        RuntimeErrorOwner.USER,
        RuntimeErrorKind.WORKFLOW_EXPRESSION_INVALID,
        RetryDisposition.NON_RETRYABLE,
        "activity:fail_fast",
    ),
    _BoundaryPolicyExpectation(
        "return.persistence",
        _result_persistence_error_classification,
        _transport_error,
        RuntimeErrorOwner.PLATFORM,
        RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE,
        RetryDisposition.RETRYABLE,
        "activity:fail_fast",
    ),
    _BoundaryPolicyExpectation(
        "child_result.materialization",
        _materialization_error_classification,
        lambda: ValueError("invalid stored child result"),
        RuntimeErrorOwner.PLATFORM,
        RuntimeErrorKind.STORAGE_MATERIALIZATION_INVALID_DATA,
        RetryDisposition.NON_RETRYABLE,
        "activity:fail_fast",
    ),
    _BoundaryPolicyExpectation(
        "child_result.persistence",
        _result_persistence_error_classification,
        _transport_error,
        RuntimeErrorOwner.PLATFORM,
        RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE,
        RetryDisposition.RETRYABLE,
        "activity:fail_fast",
    ),
    _BoundaryPolicyExpectation(
        "gather.materialization",
        _materialization_error_classification,
        _transport_error,
        RuntimeErrorOwner.PLATFORM,
        RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        RetryDisposition.RETRYABLE,
        "activity:fail_fast",
    ),
    _BoundaryPolicyExpectation(
        "gather.persistence",
        _result_persistence_error_classification,
        lambda: ValueError("invalid gather result"),
        RuntimeErrorOwner.PLATFORM,
        RuntimeErrorKind.WORKFLOW_RUNTIME_INVARIANT_VIOLATION,
        RetryDisposition.NON_RETRYABLE,
        "activity:fail_fast",
    ),
    _BoundaryPolicyExpectation(
        "agent.input",
        _agent_preparation_error_classification,
        lambda: TracecatValidationError("invalid agent input"),
        RuntimeErrorOwner.USER,
        RuntimeErrorKind.WORKFLOW_AGENT_INPUT_INVALID,
        RetryDisposition.NON_RETRYABLE,
        "activity:fail_fast",
    ),
    _BoundaryPolicyExpectation(
        "agent.preparation",
        _agent_preparation_error_classification,
        lambda: RuntimeError("agent dependency unavailable"),
        RuntimeErrorOwner.PLATFORM,
        RuntimeErrorKind.WORKFLOW_AGENT_PREPARATION_FAILED,
        RetryDisposition.RETRYABLE,
        "activity:fail_fast",
    ),
)


@pytest.mark.parametrize(
    "expectation",
    _REMAINING_BOUNDARY_POLICIES,
    ids=lambda expectation: expectation.boundary,
)
def test_remaining_runtime_boundary_policy_inventory(
    expectation: _BoundaryPolicyExpectation,
) -> None:
    """Keep owner, kind, retryability, and attempt budget reviewable together."""
    classification = expectation.classify(expectation.error_factory())

    assert classification.owner is expectation.owner
    assert classification.kind is expectation.kind
    assert classification.retry_disposition is expectation.retry_disposition
    assert RETRY_POLICIES[expectation.retry_policy].maximum_attempts == 1


@pytest.mark.parametrize(
    ("stage", "error", "owner", "kind", "retry_disposition"),
    [
        (
            "materialization",
            HTTPClientError(error=RuntimeError("materialization diagnostic")),
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
            RetryDisposition.RETRYABLE,
        ),
        (
            "expression",
            TracecatExpressionError("expression diagnostic"),
            RuntimeErrorOwner.USER,
            RuntimeErrorKind.WORKFLOW_EXPRESSION_INVALID,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            "persistence",
            HTTPClientError(error=RuntimeError("persistence diagnostic")),
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE,
            RetryDisposition.RETRYABLE,
        ),
        (
            "persistence",
            ValueError("invalid stored return diagnostic"),
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.WORKFLOW_RUNTIME_INVARIANT_VIOLATION,
            RetryDisposition.NON_RETRYABLE,
        ),
    ],
    ids=[
        "materialization-transport",
        "expression-user",
        "persistence-transport",
        "persistence-invalid-data",
    ],
)
def test_resolve_return_expression_classifies_each_failure_stage(
    stage: str,
    error: Exception,
    owner: RuntimeErrorOwner,
    kind: RuntimeErrorKind,
    retry_disposition: RetryDisposition,
) -> None:
    input = EvaluateTemplatedObjectActivityInput(
        obj="${{ TRIGGER.value }}",
        operand=ExecutionContext(ACTIONS={}, TRIGGER=InlineObject(data={"value": 1})),
        key="test/return",
    )
    storage = MagicMock()
    storage.store = AsyncMock(return_value=InlineObject(data=1))
    materialize = AsyncMock(return_value={"ACTIONS": {}, "TRIGGER": {"value": 1}})
    evaluate = MagicMock(return_value=1)
    if stage == "materialization":
        materialize.side_effect = error
    elif stage == "expression":
        evaluate.side_effect = error
    else:
        storage.store.side_effect = error

    with (
        patch("tracecat.dsl.action.materialize_context", new=materialize),
        patch("tracecat.dsl.action.eval_templated_object", new=evaluate),
        patch("tracecat.dsl.action.get_object_storage", return_value=storage),
        pytest.raises(ApplicationError) as exc_info,
    ):
        DSLActivities.resolve_return_expression_activity(input)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is owner
    assert classification.kind is kind
    assert classification.retry_disposition is retry_disposition
    assert str(error) not in str(exc_info.value)


@pytest.mark.anyio
async def test_store_workflow_payload_classifies_persistence_failure() -> None:
    diagnostic = "workflow payload persistence diagnostic"
    storage = MagicMock()
    storage.store = AsyncMock(
        side_effect=HTTPClientError(error=RuntimeError(diagnostic))
    )

    with (
        patch("tracecat.dsl.action.get_object_storage", return_value=storage),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.store_workflow_payload_activity(
            key="test/payload",
            data={"value": 1},
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert (
        classification.kind
        is RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE
    )
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert diagnostic not in str(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("activity_name", "failure_stage", "kind"),
    [
        (
            "synchronize_collection_object_activity",
            "retrieve",
            RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        ),
        (
            "synchronize_collection_object_activity",
            "store",
            RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE,
        ),
        (
            "synchronize_collection_object_activity",
            "manifest",
            RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE,
        ),
        (
            "finalize_gather_activity",
            "retrieve",
            RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        ),
        (
            "finalize_gather_activity",
            "store",
            RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE,
        ),
    ],
)
async def test_collection_boundaries_classify_storage_failures(
    monkeypatch: pytest.MonkeyPatch,
    activity_name: str,
    failure_stage: str,
    kind: RuntimeErrorKind,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__RESULT_EXTERNALIZATION_ENABLED", True)
    diagnostic = f"{activity_name} {failure_stage} diagnostic"
    error = HTTPClientError(error=RuntimeError(diagnostic))
    storage = MagicMock()
    storage.retrieve = AsyncMock(return_value="value")
    storage.store = AsyncMock(return_value=InlineObject(data="value"))
    store_list = AsyncMock(return_value=InlineObject(data=["value"]))
    store_collection = AsyncMock(return_value=InlineObject(data=["value"]))
    if failure_stage == "retrieve":
        storage.retrieve.side_effect = error
    elif failure_stage == "manifest":
        store_collection.side_effect = error
    elif activity_name == "synchronize_collection_object_activity":
        storage.store.side_effect = error
    else:
        store_list.side_effect = error

    with (
        patch("tracecat.dsl.action.get_object_storage", return_value=storage),
        patch("tracecat.dsl.action._store_list_result", new=store_list),
        patch("tracecat.dsl.action.store_collection", new=store_collection),
        pytest.raises(ApplicationError) as exc_info,
    ):
        if activity_name == "synchronize_collection_object_activity":
            await DSLActivities.synchronize_collection_object_activity(
                SynchronizeCollectionObjectActivityInput(
                    collection=[InlineObject(data="value")],
                    key="test/synchronized",
                )
            )
        else:
            await DSLActivities.finalize_gather_activity(
                FinalizeGatherActivityInput(
                    collection=[InlineObject(data="value")],
                    key="test/gather",
                    error_strategy=StreamErrorHandlingStrategy.INCLUDE,
                )
            )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is kind
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert diagnostic not in str(exc_info.value)


def test_invalid_loop_expression_is_user_attributed() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        DSLActivities.handle_looped_subflow_input_activity(
            EvaluateLoopedSubflowInputActivityInput(
                for_each="not-an-iterable-expression",
                operand=ExecutionContext(ACTIONS={}, TRIGGER=None),
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.WORKFLOW_EXPRESSION_INVALID
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE


def test_subflow_batch_storage_failure_keeps_persistence_classification() -> None:
    storage = MagicMock()
    storage.store = AsyncMock(side_effect=HTTPClientError(error="connection reset"))
    expression = "$" + "{{ for var.item in [1] }}"
    trigger_expression = "$" + "{{ var.item }}"
    inputs = ResolveSubflowBatchActivityInput(
        task=ActionStatement(
            ref="child",
            action="core.workflow.execute",
            for_each=expression,
            args={"trigger_inputs": {"value": trigger_expression}},
        ),
        operand=ExecutionContext(ACTIONS={}, TRIGGER=None),
        batch_start=0,
        batch_size=1,
        key="test/subflow",
    )

    with (
        patch("tracecat.dsl.action.get_object_storage", return_value=storage),
        pytest.raises(ApplicationError) as exc_info,
    ):
        DSLActivities.resolve_subflow_batch_activity(inputs)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert (
        classification.kind
        is RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE
    )
    assert classification.retry_disposition is RetryDisposition.RETRYABLE


@pytest.mark.anyio
async def test_synchronize_collection_streams_externalized_child_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__RESULT_EXTERNALIZATION_ENABLED", True)
    events: list[tuple[str, object]] = []
    inputs: list[StoredObject] = [
        InlineObject(data="first"),
        InlineObject(data="second"),
    ]
    result = CollectionObject(
        manifest_ref=ObjectRef(
            bucket="test-bucket",
            key="test/synchronized/manifest.json",
            size_bytes=1,
            sha256="manifest-hash",
        ),
        count=2,
        chunk_size=2,
        element_kind="stored_object",
    )

    async def retrieve(obj: InlineObject) -> object:
        events.append(("retrieve", obj.data))
        return obj.data

    async def store(key: str, value: object) -> InlineObject:
        events.append(("store", value))
        return InlineObject(data=value)

    async def store_manifest(
        prefix: str,
        refs: list[dict[str, Any]],
        *,
        element_kind: Literal["value", "stored_object"],
    ) -> CollectionObject:
        assert prefix == "test/synchronized"
        assert len(refs) == 2
        assert element_kind == "stored_object"
        events.append(("manifest", len(refs)))
        return result

    storage = MagicMock()
    storage.retrieve = AsyncMock(side_effect=retrieve)
    storage.store = AsyncMock(side_effect=store)
    with (
        patch("tracecat.dsl.action.get_object_storage", return_value=storage),
        patch(
            "tracecat.dsl.action.store_collection",
            new=AsyncMock(side_effect=store_manifest),
        ),
    ):
        actual = await DSLActivities.synchronize_collection_object_activity(
            SynchronizeCollectionObjectActivityInput(
                collection=inputs,
                key="test/synchronized",
            )
        )

    assert actual == result
    assert events == [
        ("retrieve", "first"),
        ("store", "first"),
        ("retrieve", "second"),
        ("store", "second"),
        ("manifest", 2),
    ]


def _prepare_subflow_input() -> PrepareSubflowActivityInput:
    return PrepareSubflowActivityInput(
        role=Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        ),
        task=ActionStatement(
            ref="call_child",
            action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
            args={"workflow_alias": "child"},
        ),
        operand=ExecutionContext(ACTIONS={}, TRIGGER=None),
        key="test/subflow",
    )


def _action_error_info(
    classification: RuntimeErrorClassification, *, ref: str
) -> ActionErrorInfo:
    """Build the classification-free payload for a classified failure."""
    return ActionErrorInfo(
        ref=ref,
        message=classification.message,
        type=classification.cause_type or "ApplicationError",
    )


def _error_transport_detail(
    classification: RuntimeErrorClassification, *, ref: str
) -> dict[str, Any]:
    """Serialize the detail transporting a classification and action diagnostics."""
    return build_error_transport_detail(
        classification, _action_error_info(classification, ref=ref)
    ).model_dump(mode="json")


def _error_handler_workflow() -> tuple[DSLWorkflow, DSLRunArgs]:
    instance = object.__new__(DSLWorkflow)
    role = _prepare_subflow_input().role
    dsl = DSLInput(
        title="Error handler source",
        description="Error handler ownership test",
        entrypoint=DSLEntrypoint(ref="noop"),
        actions=[ActionStatement(ref="noop", action="core.noop")],
    )
    args = DSLRunArgs(role=role, dsl=dsl, wf_id=WorkflowUUID.new_uuid4())
    instance.logger = MagicMock()
    instance.dsl = dsl
    instance.wf_exec_id = f"{args.wf_id.short()}/exec_test"
    return instance, args


def test_scheduler_adapts_non_action_classification_to_error_info() -> None:
    classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        message="Tracecat could not retrieve stored workflow data",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=RuntimeError("storage transport unavailable"),
    )
    error = _capture_application_error(classification)

    adapted = _classified_action_error_info(
        error,
        ref="fetch_data",
        stream_id=ROOT_STREAM,
    )

    assert adapted is not None
    detail, adapted_classification = adapted
    assert detail.ref == "fetch_data"
    assert detail.message == classification.message
    assert detail.type == classification.kind.value
    assert adapted_classification == classification


def test_trigger_input_validation_is_classified_and_history_safe() -> None:
    sensitive = "rejected-value-must-not-enter-history"

    with (
        patch(
            "tracecat.dsl.action.get_object_storage",
            return_value=InlineObjectStorage(),
        ),
        patch("tracecat.dsl.action.logger.info") as logger_info_mock,
        pytest.raises(ApplicationError) as exc_info,
    ):
        DSLActivities.normalize_trigger_inputs_activity(
            NormalizeTriggerInputsActivityInputs(
                input_schema={"number": ExpectedField(type="int")},
                trigger_inputs=InlineObject(data={"number": sensitive}),
                key="test/normalized-trigger",
            )
        )

    error = exc_info.value
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.WORKFLOW_TRIGGER_INPUT_INVALID
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert classification.cause_type == ValidationError.__name__
    assert error.non_retryable is True

    payload = parse_classified_action_error_payload(error.details[0])
    assert isinstance(payload, dict)
    detail = payload["__workflow_trigger__"]
    assert detail.classification == classification
    assert detail.diagnostic is not None
    assert detail.diagnostic.ref == "__workflow_trigger__"

    failure = Failure()
    asyncio.run(DataConverter.default.encode_failure(error, failure))
    assert sensitive not in str(failure)
    logger_info_mock.assert_called_once()
    assert "error" not in logger_info_mock.call_args.kwargs
    assert sensitive not in str(logger_info_mock.call_args)


@pytest.mark.parametrize(
    "fault_point",
    ["initialize-storage", "normalize", "persist-invalid-data"],
)
def test_trigger_input_invariant_failures_are_precise_and_history_safe(
    fault_point: str,
) -> None:
    diagnostic = f"trigger {fault_point} diagnostic must not enter history"
    storage = MagicMock()
    storage.store = AsyncMock(return_value=InlineObject(data={}))
    get_storage = MagicMock(return_value=storage)
    normalize = MagicMock(return_value={})
    if fault_point == "initialize-storage":
        get_storage.side_effect = RuntimeError(diagnostic)
    elif fault_point == "normalize":
        normalize.side_effect = RuntimeError(diagnostic)
    else:
        storage.store.side_effect = ValueError(diagnostic)

    with (
        patch("tracecat.dsl.action.get_object_storage", new=get_storage),
        patch("tracecat.dsl.action.normalize_trigger_inputs", new=normalize),
        pytest.raises(ApplicationError) as exc_info,
    ):
        DSLActivities.normalize_trigger_inputs_activity(
            NormalizeTriggerInputsActivityInputs(
                input_schema={},
                trigger_inputs=None,
                key="test/trigger",
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_RUNTIME_INVARIANT_VIOLATION
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert diagnostic not in str(exc_info.value)


@pytest.mark.parametrize(
    ("fault_point", "expected_kind"),
    [
        (
            "retrieve",
            RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        ),
        (
            "store",
            RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE,
        ),
    ],
)
def test_trigger_input_storage_failure_is_platform_owned_and_history_safe(
    fault_point: Literal["retrieve", "store"],
    expected_kind: RuntimeErrorKind,
) -> None:
    sensitive = "trigger storage diagnostic must not enter history"
    transport_error = HTTPClientError(error=RuntimeError(sensitive))
    storage = MagicMock()
    storage.retrieve = AsyncMock(return_value={"number": 1})
    storage.store = AsyncMock(return_value=InlineObject(data={"number": 1}))
    match fault_point:
        case "retrieve":
            storage.retrieve.side_effect = transport_error
        case "store":
            storage.store.side_effect = transport_error

    with (
        patch("tracecat.dsl.action.get_object_storage", return_value=storage),
        pytest.raises(ApplicationError) as exc_info,
    ):
        DSLActivities.normalize_trigger_inputs_activity(
            NormalizeTriggerInputsActivityInputs(
                input_schema={"number": ExpectedField(type="int")},
                trigger_inputs=InlineObject(data={"number": 1}),
                key="test/normalized-trigger",
            )
        )

    error = exc_info.value
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is expected_kind
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert classification.cause_type == HTTPClientError.__name__
    assert error.non_retryable is False

    failure = Failure()
    asyncio.run(DataConverter.default.encode_failure(error, failure))
    assert sensitive not in str(failure)


@pytest.mark.anyio
async def test_prepare_subflow_platform_failure_is_classified_and_history_safe() -> (
    None
):
    """The failure's classification keeps diagnostics out of durable history."""
    sensitive = RuntimeError("postgresql://user:secret@example.invalid/database")

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=sensitive),
        ),
        patch("tracecat.dsl.action.logger.error") as logger_error_mock,
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    error = exc_info.value
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_SUBFLOW_PREPARATION_FAILED
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert classification.cause_type == "RuntimeError"
    assert error.non_retryable is False
    assert error.type == classification.kind.value
    assert error.__cause__ is None

    detail = parse_classified_action_error_payload(error.details[0])
    assert isinstance(detail, ActionErrorTransportDetail)
    assert detail.classification == classification
    assert detail.diagnostic == ActionErrorInfo(
        ref="call_child",
        message=classification.message,
        type="RuntimeError",
    )

    failure = Failure()
    await DataConverter.default.encode_failure(error, failure)
    serialized_failure = str(failure)
    assert "secret" not in serialized_failure
    assert "example.invalid" not in serialized_failure
    logger_error_mock.assert_called_once()
    log_fields = logger_error_mock.call_args.kwargs
    assert "error" not in log_fields
    assert log_fields["error_type"] == "RuntimeError"
    assert log_fields["error_kind"] == classification.kind.value
    assert "secret" not in str(logger_error_mock.call_args)


@pytest.mark.anyio
async def test_prepare_subflow_input_failure_keeps_user_semantics() -> None:
    user_error = UserError("Invalid child workflow arguments")

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=user_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    error = exc_info.value
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.WORKFLOW_SUBFLOW_INPUT_INVALID
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert classification.message == user_error.message
    assert error.non_retryable is True
    assert error.type == classification.kind.value


@pytest.mark.anyio
async def test_prepare_subflow_missing_definition_uses_not_found_kind() -> None:
    user_error = SubflowDefinitionNotFoundError(
        "The child workflow definition could not be found"
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=user_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.WORKFLOW_DEFINITION_NOT_FOUND


def test_subflow_user_classification_control_flow_is_replay_gated() -> None:
    """A wholly user-owned classified failure only steers control flow behind the patch."""
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.WORKFLOW_DEFINITION_NOT_FOUND,
        message="The child workflow could not be found",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(
        classification,
        _error_transport_detail(classification, ref="call_child"),
    )

    with patch(
        "tracecat.dsl.workflow.workflow.patched",
        return_value=False,
    ) as patched_mock:
        assert DSLWorkflow._has_user_error_cause(error) is False
        patched_mock.assert_called_once_with(WorkflowPatch.ERROR_OWNER_CONTROL_FLOW)

    with patch(
        "tracecat.dsl.workflow.workflow.patched",
        return_value=True,
    ) as patched_mock:
        assert DSLWorkflow._has_user_error_cause(error) is True
        patched_mock.assert_called_once_with(WorkflowPatch.ERROR_OWNER_CONTROL_FLOW)


@pytest.mark.anyio
async def test_prepare_subflow_keeps_existing_classification_authoritative() -> None:
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    original_error = _capture_application_error(original_classification)

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    error = exc_info.value
    assert extract_error_classification(error) == original_classification
    assert error.non_retryable is False
    assert error.type == original_classification.kind.value


@pytest.mark.anyio
async def test_prepare_subflow_keeps_existing_non_retryable_semantics() -> None:
    original_error = ApplicationError(
        "Do not retry this operation",
        type="DependencyRejectedRequest",
        non_retryable=True,
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    error = exc_info.value
    classification = extract_error_classification(error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert error.non_retryable is True
    assert error.type == classification.kind.value


@pytest.mark.anyio
async def test_prepare_subflow_drops_unclassified_application_error_details() -> None:
    sensitive = "postgresql://user:secret@example.invalid/database"
    original_error = ApplicationError(
        "Dependency failed",
        {"diagnostic": sensitive},
        type="DependencyError",
        non_retryable=True,
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        patch("tracecat.dsl.action.logger.error"),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    failure = Failure()
    await DataConverter.default.encode_failure(exc_info.value, failure)
    assert sensitive not in str(failure)


@pytest.mark.anyio
async def test_prepare_subflow_filters_unclassified_sibling_details() -> None:
    sensitive = "SENSITIVE_MARKER"
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    classified = _capture_application_error(classification)
    original_error = ApplicationError(
        classified.message,
        *classified.details,
        {"diagnostic": sensitive},
        type=classified.type,
        non_retryable=classified.non_retryable,
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        patch("tracecat.dsl.action.logger.error"),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    failure = Failure()
    await DataConverter.default.encode_failure(exc_info.value, failure)
    assert extract_error_classification(exc_info.value) == classification
    assert sensitive not in str(exc_info.value.details)
    assert sensitive not in str(failure)


@pytest.mark.anyio
async def test_prepare_subflow_clears_retry_delay_for_non_retryable_classification() -> (
    None
):
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    classified = _capture_application_error(classification)
    original_error = ApplicationError(
        classified.message,
        *classified.details,
        type=classified.type,
        non_retryable=True,
        next_retry_delay=timedelta(seconds=5),
    )

    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=original_error),
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())

    assert exc_info.value.non_retryable is True
    assert exc_info.value.next_retry_delay is None
    assert extract_error_classification(exc_info.value) == classification


@pytest.mark.anyio
async def test_prepare_subflow_cancellation_keeps_native_semantics() -> None:
    with (
        patch(
            "tracecat.dsl.action._prepare_subflow",
            new=AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await DSLActivities.prepare_subflow_activity(_prepare_subflow_input())


@pytest.mark.anyio
async def test_error_handler_runs_before_original_owner_is_stamped() -> None:
    """The handler completes before the original error's owner is stamped."""
    instance, args = _error_handler_workflow()
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(
        classification,
        {"action": _error_transport_detail(classification, ref="action")},
    )
    events: list[str] = []

    async def run_handler(_: DSLRunArgs) -> None:
        events.append("handler")

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(return_value=args.wf_id),
        ),
        patch.object(
            instance,
            "_prepare_error_handler_workflow",
            new=AsyncMock(return_value=args),
        ),
        patch.object(
            instance,
            "_run_error_handler_workflow",
            new=AsyncMock(side_effect=run_handler),
        ) as run_handler_mock,
        patch.object(
            DSLWorkflow,
            "_upsert_terminal_error_owner",
            side_effect=lambda _: events.append("upsert"),
        ) as upsert_mock,
        patch("tracecat.dsl.workflow.workflow.info"),
        patch(
            "tracecat.dsl.workflow.get_trigger_type",
            return_value=TriggerType.MANUAL,
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await instance._handle_application_error(
            args,
            error,
            stamp_terminal_owner=True,
        )

    assert exc_info.value is error
    run_handler_mock.assert_awaited_once_with(args)
    upsert_mock.assert_called_once_with(error)
    assert events == ["handler", "upsert"]


@pytest.mark.anyio
async def test_error_handler_failure_preserves_original_terminal_owner() -> None:
    """A failing handler stays secondary to the original classified failure."""
    instance, args = _error_handler_workflow()
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    handler_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not run the error handler",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    original_error = _capture_application_error(
        original_classification,
        {"action": _error_transport_detail(original_classification, ref="action")},
    )
    handler_error = _capture_application_error(handler_classification)

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(return_value=args.wf_id),
        ),
        patch.object(
            instance,
            "_prepare_error_handler_workflow",
            new=AsyncMock(return_value=args),
        ),
        patch.object(
            instance,
            "_run_error_handler_workflow",
            new=AsyncMock(side_effect=handler_error),
        ),
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=True,
        ) as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
        patch("tracecat.dsl.workflow.workflow.info"),
        patch(
            "tracecat.dsl.workflow.get_trigger_type",
            return_value=TriggerType.MANUAL,
        ),
    ):
        try:
            raise original_error
        except ApplicationError as active_error:
            with pytest.raises(ApplicationError) as exc_info:
                await instance._handle_application_error(
                    args,
                    active_error,
                    stamp_terminal_owner=True,
                )

    assert exc_info.value is original_error
    assert handler_error.__context__ is original_error
    assert patched_mock.call_args_list == [
        call(WorkflowPatch.PRESERVE_ORIGINAL_ERROR_AFTER_HANDLER_FAILURE),
        call(WorkflowPatch.ERROR_OWNER_SEARCH_ATTRIBUTE),
    ]
    upsert_mock.assert_called_once()
    updates = upsert_mock.call_args.args[0]
    assert len(updates) == 1
    assert updates[0].key.name == TemporalSearchAttr.ERROR_OWNER.value
    assert updates[0].value == RuntimeErrorOwner.USER.value


@pytest.mark.anyio
async def test_unclassified_handler_failure_preserves_original_owner() -> None:
    """An unclassified handler failure stays secondary to the original error."""
    instance, args = _error_handler_workflow()
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    original_error = _capture_application_error(original_classification)
    handler_error = RuntimeError("Handler lookup failed")

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(side_effect=handler_error),
        ),
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=True,
        ) as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
    ):
        try:
            raise original_error
        except ApplicationError as active_error:
            with pytest.raises(ApplicationError) as exc_info:
                await instance._handle_application_error(
                    args,
                    active_error,
                    stamp_terminal_owner=True,
                )

    assert exc_info.value is original_error
    assert handler_error.__context__ is original_error
    assert patched_mock.call_args_list == [
        call(WorkflowPatch.PRESERVE_ORIGINAL_ERROR_AFTER_HANDLER_FAILURE),
        call(WorkflowPatch.ERROR_OWNER_SEARCH_ATTRIBUTE),
    ]
    upsert_mock.assert_called_once()
    updates = upsert_mock.call_args.args[0]
    assert len(updates) == 1
    assert updates[0].value == RuntimeErrorOwner.USER.value


@pytest.mark.anyio
async def test_error_handler_lookup_failure_stamps_original_error() -> None:
    instance, args = _error_handler_workflow()
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    lookup_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not resolve the error handler",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    original_error = _capture_application_error(original_classification)
    lookup_error = _capture_application_error(lookup_classification)

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(side_effect=lookup_error),
        ),
        patch.object(
            DSLWorkflow,
            "_upsert_terminal_error_owner",
        ) as upsert_mock,
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=True,
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await instance._handle_application_error(
            args,
            original_error,
            stamp_terminal_owner=True,
        )

    assert exc_info.value is original_error
    upsert_mock.assert_called_once_with(original_error)


@pytest.mark.anyio
async def test_error_handler_detail_adaptation_failure_stamps_escaping_error() -> None:
    instance, args = _error_handler_workflow()
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    original_error = _capture_application_error(
        original_classification,
        {"action": {"invalid": "detail"}},
    )

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(return_value=args.wf_id),
        ),
        patch.object(
            DSLWorkflow,
            "_upsert_terminal_error_owner",
        ) as upsert_mock,
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=True,
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await instance._handle_application_error(
            args,
            original_error,
            stamp_terminal_owner=True,
        )

    assert exc_info.value is original_error
    upsert_mock.assert_called_once_with(original_error)


@pytest.mark.anyio
async def test_error_handler_failure_replays_legacy_terminal_behavior() -> None:
    """Marker-free histories keep the handler failure as their terminal error."""
    instance, args = _error_handler_workflow()
    original_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    handler_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not run the error handler",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    original_error = _capture_application_error(original_classification)
    handler_error = _capture_application_error(handler_classification)

    with (
        patch.object(
            instance,
            "_get_error_handler_workflow_id",
            new=AsyncMock(side_effect=handler_error),
        ),
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=False,
        ) as patched_mock,
        patch.object(
            DSLWorkflow,
            "_upsert_terminal_error_owner",
        ) as upsert_mock,
        pytest.raises(ApplicationError) as exc_info,
    ):
        await instance._handle_application_error(
            args,
            original_error,
            stamp_terminal_owner=True,
        )

    assert exc_info.value is handler_error
    patched_mock.assert_called_once_with(
        WorkflowPatch.PRESERVE_ORIGINAL_ERROR_AFTER_HANDLER_FAILURE
    )
    upsert_mock.assert_called_once_with(handler_error)


def test_terminal_platform_owner_wins_for_alert_attribution() -> None:
    """One platform classification in the terminal map stamps platform ownership."""
    user_classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    platform_classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
        message="Tracecat could not execute the action",
        retry_disposition=RetryDisposition.RETRYABLE,
    )
    details = {
        "user_action": _error_transport_detail(user_classification, ref="user_action"),
        "platform_action": _error_transport_detail(
            platform_classification, ref="platform_action"
        ),
    }
    error = ApplicationError("Workflow failed", details, non_retryable=True)

    with (
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=True,
        ) as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
    ):
        DSLWorkflow._upsert_terminal_error_owner(error)

    patched_mock.assert_called_once_with(WorkflowPatch.ERROR_OWNER_SEARCH_ATTRIBUTE)
    upsert_mock.assert_called_once()
    updates = upsert_mock.call_args.args[0]
    assert len(updates) == 1
    assert updates[0].key.name == TemporalSearchAttr.ERROR_OWNER.value
    assert updates[0].value == RuntimeErrorOwner.PLATFORM.value


def test_terminal_owner_upsert_is_replay_gated() -> None:
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="The action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = _capture_application_error(classification)

    with (
        patch(
            "tracecat.dsl.workflow.workflow.patched",
            return_value=False,
        ) as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
    ):
        DSLWorkflow._upsert_terminal_error_owner(error)

    patched_mock.assert_called_once_with(WorkflowPatch.ERROR_OWNER_SEARCH_ATTRIBUTE)
    upsert_mock.assert_not_called()


def test_error_handler_owner_timing_has_distinct_replay_patch() -> None:
    assert WorkflowPatch.ERROR_OWNER_AFTER_HANDLER not in {
        WorkflowPatch.ERROR_OWNER_SEARCH_ATTRIBUTE,
        WorkflowPatch.ERROR_OWNER_CONTROL_FLOW,
        WorkflowPatch.PRESERVE_ORIGINAL_ERROR_AFTER_HANDLER_FAILURE,
    }


def test_legacy_terminal_error_does_not_add_patch_marker_or_owner() -> None:
    error = ApplicationError("Legacy failure", non_retryable=True)

    with (
        patch("tracecat.dsl.workflow.workflow.patched") as patched_mock,
        patch("tracecat.dsl.workflow.workflow.upsert_search_attributes") as upsert_mock,
    ):
        DSLWorkflow._upsert_terminal_error_owner(error)

    patched_mock.assert_not_called()
    upsert_mock.assert_not_called()
