from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, cast

import dateparser
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from temporalio import activity
from temporalio.exceptions import ApplicationError
from tracecat_ee.agent.schemas import AgentActionArgs, PresetAgentActionArgs

from tracecat import config
from tracecat.auth.types import Role
from tracecat.common import is_iterable
from tracecat.dsl.common import (
    MAX_LOOP_ITERATIONS,
    DSLInput,
    ExecuteSubflowArgs,
    PreparedSubflowResult,
    ResolvedSubflowBatch,
    ResolvedSubflowConfig,
)
from tracecat.dsl.enums import StreamErrorHandlingStrategy
from tracecat.dsl.schemas import (
    ActionStatement,
    DSLConfig,
    ExecutionContext,
    MaterializedExecutionContext,
    MaterializedTaskResult,
    RunActionInput,
    StreamID,
    TaskResult,
)
from tracecat.dsl.types import ActionErrorInfo, ActionErrorInfoAdapter
from tracecat.dsl.validation import normalize_trigger_inputs
from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions.common import ExprContext
from tracecat.expressions.core import TemplateExpression
from tracecat.expressions.eval import (
    eval_templated_object,
    get_iterables_from_expression,
)
from tracecat.expressions.schemas import ExpectedField
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.collection import (
    materialize_collection_values,
    store_collection,
)
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    InlineObject,
    StoredObject,
    StoredObjectValidator,
    collection_item_key,
    get_object_storage,
)
from tracecat.validation.schemas import ValidationDetail

_thread_local = threading.local()


class ScatterActionInput(BaseModel):
    """Input for the scatter action activity.

    This schema allows the scatter activity to be associated with an action_ref
    in the workflow execution event history.
    """

    task: ActionStatement
    """The scatter action statement (includes ref, title, action)."""

    stream_id: StreamID | None = None
    """Current stream ID for nested scatters."""

    collection: Any
    """The collection expression to evaluate."""

    operand: ExecutionContext
    """Context for expression evaluation."""

    key: str
    """Storage key prefix for the collection."""


class EvaluateLoopedSubflowInputActivityInput(BaseModel):
    """Input for evaluating looped subflow for_each expression."""

    for_each: str | list[str]
    """The for_each expression list."""

    operand: ExecutionContext
    """Context for expression evaluation."""


class SynchronizeCollectionObjectActivityInput(BaseModel):
    collection: list[StoredObject]
    key: str
    """Storage key prefix for the collection."""


class FinalizeGatherActivityInput(BaseModel):
    collection: list[StoredObject]
    key: str
    drop_nulls: bool = False
    error_strategy: StreamErrorHandlingStrategy = StreamErrorHandlingStrategy.PARTITION


class FinalizeGatherActivityResult(BaseModel):
    result: StoredObject
    """Result collection. CollectionObject if externalized, else InlineObject."""
    errors: list[ActionErrorInfo] = Field(default_factory=list)


class BuildAgentArgsActivityInput(BaseModel):
    args: dict[str, Any]
    operand: ExecutionContext


class BuildPresetAgentArgsActivityInput(BaseModel):
    args: dict[str, Any]
    operand: ExecutionContext


class EvaluateTemplatedObjectActivityInput(BaseModel):
    obj: Any
    operand: ExecutionContext
    key: str


class EvaluateForEachActivityInput(BaseModel):
    """Input for evaluating for_each loop iterations with materialized context."""

    task: ActionStatement
    operand: ExecutionContext


class NormalizeTriggerInputsActivityInputs(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    input_schema: dict[str, ExpectedField]
    trigger_inputs: StoredObject | None = None
    key: str


class ResolveSubflowBatchActivityInput(BaseModel):
    """Input for resolving subflow args per batch iteration."""

    task: ActionStatement
    """Full ActionStatement with environment override and args."""

    operand: ExecutionContext
    """Context for expression evaluation."""

    batch_start: int
    """Starting index for this batch."""

    batch_size: int
    """Number of iterations in this batch."""

    key: str
    """Storage key prefix for trigger_inputs CollectionObject."""


class PrepareSubflowActivityInput(BaseModel):
    """Input for prepare_subflow_activity that consolidates all subflow preparation."""

    role: Role
    """Role for service calls."""

    task: ActionStatement
    """Full ActionStatement with args (workflow_alias/id) and optional for_each."""

    operand: ExecutionContext
    """Context for expression evaluation."""

    key: str
    """Storage key prefix for CollectionObject."""

    use_committed: bool = True
    """Use committed WorkflowDefinition alias (True) or draft Workflow alias (False)."""


def run_sync[T: Any](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine in the current thread."""
    runner = getattr(_thread_local, "runner", None)
    if runner is None:
        runner = asyncio.Runner()
        runner.__enter__()  # create & keep loop
        _thread_local.runner = runner
    return runner.run(coro)


async def _store_collection_as_refs(prefix: str, items: list[Any]) -> CollectionObject:
    """Store collection items as StoredObject handles and persist refs in chunks."""
    storage = get_object_storage()
    refs: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        stored = await storage.store(collection_item_key(prefix, i), item)
        refs.append(stored.model_dump())
    return await store_collection(prefix, refs, element_kind="stored_object")


async def _materialize_task_result(task_result: TaskResult) -> MaterializedTaskResult:
    """Materialize a TaskResult's StoredObject result to raw value.

    Handles collection_index for scatter items. When set, CollectionObject
    retrieval resolves a single item directly via CollectionObject.at(index).

    Args:
        task_result: A TaskResult
        cache: Shared cache for ExternalObject retrievals

    Returns:
        MaterializedTaskResult with raw result value
    """
    # Handle Pydantic TaskResult instance
    storage = get_object_storage()
    match task_result.result:
        case InlineObject():
            raw_result = task_result.result.data
        case ExternalObject():
            raw_result = await storage.retrieve(task_result.result)
        case CollectionObject() as collection:
            if task_result.collection_index is not None:
                raw_result = await storage.retrieve(
                    collection.at(task_result.collection_index)
                )
            else:
                raw_result = await materialize_collection_values(collection)
        case _:
            raise TypeError(
                "Expected TaskResult.result to be a StoredObject, "
                f"got {type(task_result.result).__name__}"
            )

    return MaterializedTaskResult(
        result=raw_result,
        result_typename=task_result.result_typename,
        error=task_result.error,
        error_typename=task_result.error_typename,
        interaction=task_result.interaction,
        interaction_id=task_result.interaction_id,
        interaction_type=task_result.interaction_type,
    )


async def materialize_context(ctx: ExecutionContext) -> MaterializedExecutionContext:
    """Retrieve StoredObjects and replace with raw values in context copy.

    With uniform envelope design, TaskResult.result is ALWAYS a StoredObject.
    This function materializes (retrieves) data from the storage layer.

    - InlineObject: data is already present, just unwrap
    - ExternalObject: fetch from S3/MinIO and unwrap

    Caches retrievals within the activity invocation keyed by (bucket, key, sha256).

    Args:
        ctx: Execution context containing ACTIONS, TRIGGER, etc.

    Returns:
        MaterializedExecutionContext with all StoredObjects replaced by raw values.

    Note:
        When ExecutionContext passes through Temporal activities, nested Pydantic
        models (TaskResult, StoredObject) are deserialized as plain dicts. This
        function validates them back to proper types before materialization.
    """
    result: MaterializedExecutionContext = {}

    # Track action refs to map results back after parallel materialization
    action_refs: list[str] = []
    trigger_task_idx: int | None = None

    logger.debug("Materializing context", ctx=ctx, typ=type(ctx))

    # Parallelize all StoredObject materializations using GatheringTaskGroup
    coros = []
    # Materialize ACTIONS - each value is a TaskResult with StoredObject result
    if actions := ctx.get("ACTIONS"):
        for ref, task_result in actions.items():
            action_refs.append(ref)
            validated = TaskResult.model_validate(task_result)
            coros.append(_materialize_task_result(validated))

    # Materialize TRIGGER - always a StoredObject with uniform envelope
    if trigger := ctx.get("TRIGGER"):
        trigger_task_idx = len(action_refs)  # Index after all action tasks
        validated = StoredObjectValidator.validate_python(trigger)
        coros.append(get_object_storage().retrieve(validated))

    # Collect results and map back to their refs
    try:
        materialized_results = await asyncio.gather(*coros)
    except Exception as e:
        logger.warning("Error materializing context", error=e)
        raise ApplicationError(
            "Failed to materialize context",
            non_retryable=True,
        ) from e

    # Reconstruct ACTIONS dict with materialized results
    if action_refs:
        result["ACTIONS"] = {
            ref: materialized_results[i] for i, ref in enumerate(action_refs)
        }

    # Extract TRIGGER result if it was materialized
    if trigger_task_idx is not None:
        result["TRIGGER"] = materialized_results[trigger_task_idx]

    # Copy through non-StoredObject fields unchanged
    if env := ctx.get("ENV"):
        result["ENV"] = env
    if secrets := ctx.get("SECRETS"):
        result["SECRETS"] = secrets
    if vars := ctx.get("VARS"):
        result["VARS"] = vars
    if var := ctx.get("var"):
        result["var"] = var

    return result


class ValidateActionActivityInput(BaseModel):
    role: Role
    task: ActionStatement


class DSLActivities:
    """Container for all UDFs registered in the registry."""

    def __new__(cls):
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def load(cls) -> list[Callable[[RunActionInput], Any]]:
        """Load and return all UDFs in the class."""
        return [
            fn
            for method_name in dir(cls)
            if hasattr(
                fn := getattr(cls, method_name),
                "__temporal_activity_definition",
            )
        ]

    @staticmethod
    @activity.defn
    def noop_gather_action_activity(input: RunActionInput, role: Role) -> Any:
        """No-op gather action activity."""
        return input.exec_context.get("ACTIONS", {}).get(input.task.ref)

    @staticmethod
    @activity.defn
    def parse_wait_until_activity(
        wait_until: str,
    ) -> str | None:
        """Parse the wait until datetime. We wrap this in an activity to avoid
        non-determinism errors when using the `dateparser` library
        """
        dt = dateparser.parse(
            wait_until, settings={"TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True}
        )
        return dt.isoformat() if dt else None

    @staticmethod
    @activity.defn
    async def store_workflow_payload_activity(
        key: str,
        data: Any,
    ) -> StoredObject:
        """Store workflow trigger payload and return StoredObject.

        Always returns StoredObject (InlineObject or ExternalObject depending on config/size).
        Temporal serializes Pydantic models automatically.
        """
        storage = get_object_storage()
        return await storage.store(key, data)

    @staticmethod
    @activity.defn
    def evaluate_single_expression_activity(
        expression: str,
        operand: ExecutionContext,
    ) -> Any:
        """Evaluate a single templated expression.

        Materializes any StoredObjects in operand before evaluation. This ensures
        that expressions evaluate against raw values even when results are externalized.

        Additional validation is performed so that *invalid* or *empty* expressions
        no longer fail silently – instead we raise a ``TracecatExpressionError``
        which will cause the activity to fail fast and surface an explicit error
        to the calling workflow.
        """
        # Materialize any StoredObjects in operand
        materialized = run_sync(materialize_context(operand))

        expr_str = expression.strip()

        # Fail fast on empty / whitespace‐only expressions so that users receive a
        # clear error instead of silently evaluating to ``False``.
        if not expr_str:
            raise TracecatExpressionError("Expression cannot be empty")

        # Evaluate the expression. Any parsing / evaluation errors raised inside
        # ``TemplateExpression`` are propagated unchanged so that Temporal marks
        # the activity as failed.
        # Internally, this will raise a ``TracecatExpressionError`` if the expression
        # is malformed/invalid.
        expr = TemplateExpression(expr_str, operand=materialized)
        return expr.result()

    @staticmethod
    @activity.defn
    def evaluate_templated_object_activity(
        input: EvaluateTemplatedObjectActivityInput,
    ) -> StoredObject:
        """Evaluate templated objects using the expression engine.

        Materializes any StoredObjects in operand before evaluation. This ensures
        that expressions evaluate against raw values even when results are externalized.
        """
        # Materialize any StoredObjects in operand
        materialized = run_sync(materialize_context(input.operand))
        result = eval_templated_object(input.obj, operand=materialized)
        stored = run_sync(get_object_storage().store(input.key, result))
        return stored

    @staticmethod
    @activity.defn
    def handle_scatter_input_activity(
        input: ScatterActionInput,
    ) -> StoredObject:
        """Evaluate scatter collection and store for scatter iteration.

        This activity is associated with the scatter action's ref via ScatterActionInput,
        allowing it to appear in workflow execution event history as a compact event.

        Materializes any StoredObjects in operand before evaluation. This ensures
        that expressions evaluate against raw values even when results are externalized.

        Returns CollectionObject if externalized, InlineObject otherwise.
        """
        return _evaluate_scatter_input(input)

    @staticmethod
    @activity.defn
    def handle_looped_subflow_input_activity(
        input: EvaluateLoopedSubflowInputActivityInput,
    ) -> int:
        """Evaluate for_each expression to get iteration count for looped subflows."""
        # Materialize any StoredObjects in operand
        materialized = run_sync(materialize_context(input.operand))

        # Get iterables from for_each expression
        iterators = get_iterables_from_expression(
            expr=input.for_each, operand=materialized
        )

        # Return total count
        return len(list(zip(*iterators, strict=False)))

    @staticmethod
    @activity.defn
    def resolve_subflow_batch_activity(
        input: ResolveSubflowBatchActivityInput,
    ) -> ResolvedSubflowBatch:
        """Resolve subflow args for a batch of loop iterations.

        Evaluates task.args (including DSL primitives like environment, timeout)
        for each iteration in the batch, with var.item patched per iteration.

        Returns:
            ResolvedSubflowBatch with:
            - configs: Single config if all identical, list if varying per iteration
            - trigger_inputs: CollectionObject of evaluated trigger_inputs
        """
        return _resolve_subflow_batch(input)

    @staticmethod
    @activity.defn
    async def synchronize_collection_object_activity(
        input: SynchronizeCollectionObjectActivityInput,
    ) -> StoredObject:
        """Materialize a list of StoredObjects and store as a single result.

        This activity synchronizes multiple child workflow results (each a StoredObject)
        by materializing each result and combining them into a list, then storing
        that list as a single StoredObject.

        Returns CollectionObject if externalized, InlineObject otherwise.
        """
        # Guard CollectionObject: only use chunked storage when externalization
        # is enabled. Fall back to inline list for non-externalized deployments.
        storage = get_object_storage()
        if config.TRACECAT__RESULT_EXTERNALIZATION_ENABLED:
            refs: list[dict[str, Any]] = []
            for i, obj in enumerate(input.collection):
                value = await storage.retrieve(obj)
                stored = await storage.store(collection_item_key(input.key, i), value)
                refs.append(stored.model_dump())
            return await store_collection(
                input.key,
                refs,
                element_kind="stored_object",
            )
        else:
            values: list[Any] = []
            for obj in input.collection:
                value = await storage.retrieve(obj)
                values.append(value)
            return InlineObject(data=values, typename="list")

    @staticmethod
    @activity.defn
    async def finalize_gather_activity(
        input: FinalizeGatherActivityInput,
    ) -> FinalizeGatherActivityResult:
        """Finalize gather by materializing items and storing as CollectionObject.

        Takes a list of StoredObjects (one per execution stream), materializes
        each to its raw value, applies drop_nulls + error_strategy, and stores
        the resulting list as a CollectionObject.

        Returns the CollectionObject handle and any partitioned errors.
        """
        storage = get_object_storage()
        values: list[Any] = []
        for obj in input.collection:
            value = await storage.retrieve(obj)
            values.append(value)

        if input.drop_nulls:
            values = [v for v in values if v is not None]

        results: list[Any] = []
        errors: list[ActionErrorInfo] = []
        match input.error_strategy:
            case StreamErrorHandlingStrategy.PARTITION:
                results, errors = _partition_errors(values)
            case StreamErrorHandlingStrategy.DROP:
                results = [v for v in values if not _is_error_info(v)]
            case StreamErrorHandlingStrategy.INCLUDE:
                results = list(values)
            case StreamErrorHandlingStrategy.RAISE:
                # Caller is responsible for raising if errors are present.
                results, errors = _partition_errors(values)
            case _:
                raise ApplicationError(
                    f"Invalid error handling strategy: {input.error_strategy}",
                    non_retryable=True,
                )

        # Guard CollectionObject: only use chunked storage when externalization
        # is enabled. Fall back to inline list for non-externalized deployments.
        if config.TRACECAT__RESULT_EXTERNALIZATION_ENABLED:
            stored = await _store_collection_as_refs(input.key, results)
        else:
            stored = InlineObject(data=results, typename="list")
        return FinalizeGatherActivityResult(result=stored, errors=errors)

    @staticmethod
    @activity.defn
    def build_agent_args_activity(
        input: BuildAgentArgsActivityInput,
    ) -> AgentActionArgs:
        """Build an AgentActionArgs from a dictionary of arguments.

        Materializes any StoredObjects in operand before evaluation. This ensures
        that expressions evaluate against raw values even when results are externalized.
        """
        materialized = run_sync(materialize_context(input.operand))
        evaled_args = eval_templated_object(input.args, operand=materialized)
        return AgentActionArgs(**evaled_args)

    @staticmethod
    @activity.defn
    def build_preset_agent_args_activity(
        input: BuildPresetAgentArgsActivityInput,
    ) -> PresetAgentActionArgs:
        """Build a PresetAgentActionArgs from a dictionary of arguments.

        Materializes any StoredObjects in operand before evaluation. This ensures
        that expressions evaluate against raw values even when results are externalized.
        """
        materialized = run_sync(materialize_context(input.operand))
        evaled_args = eval_templated_object(input.args, operand=materialized)
        return PresetAgentActionArgs(**evaled_args)

    @staticmethod
    @activity.defn
    def resolve_return_expression_activity(
        input: EvaluateTemplatedObjectActivityInput,
    ) -> StoredObject:
        """Evaluate templated objects using the expression engine.

        Materializes any StoredObjects in operand before evaluation. This ensures
        that expressions evaluate against raw values even when results are externalized.
        """
        # Materialize any StoredObjects in operand
        materialized = run_sync(materialize_context(input.operand))
        result = eval_templated_object(input.obj, operand=materialized)
        stored = run_sync(get_object_storage().store(input.key, result))
        return stored

    @staticmethod
    @activity.defn
    def normalize_trigger_inputs_activity(
        inputs: NormalizeTriggerInputsActivityInputs,
    ) -> StoredObject:
        """Return trigger inputs with defaults applied according to DSL expects."""
        try:
            value = {}
            storage = get_object_storage()
            if inputs.trigger_inputs is not None:
                value = run_sync(storage.retrieve(inputs.trigger_inputs))
            normalized = normalize_trigger_inputs(inputs.input_schema, value)
            stored = run_sync(storage.store(inputs.key, normalized))
            return stored
        except ValidationError as e:
            logger.info("Validation error when normalizing trigger inputs", error=e)
            raise ApplicationError(
                "Failed to validate trigger inputs",
                ValidationDetail.list_from_pydantic(e),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
        except Exception as e:
            logger.warning(
                "Unexpected error cause when normalizing trigger inputs",
                error=e,
            )
            raise ApplicationError(
                "Unexpected error when normalizing trigger inputs",
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e

    @staticmethod
    @activity.defn
    async def prepare_subflow_activity(
        input: PrepareSubflowActivityInput,
    ) -> PreparedSubflowResult:
        """Single activity to prepare all data for subflow execution.

        Consolidates:
        1. Workflow alias resolution (if workflow_alias provided)
        2. Workflow definition fetch (DSL + registry_lock)
        3. For loops: evaluate all trigger_inputs → CollectionObject
        4. For loops: evaluate all runtime_configs → T|list[T] optimization

        Returns PreparedSubflowResult containing all shared data needed to spawn child workflows.
        """
        return await _prepare_subflow(input)


def _evaluate_scatter_input(input: ScatterActionInput) -> StoredObject:
    """Evaluate scatter collection expression and store as CollectionObject.

    Returns CollectionObject if externalized, InlineObject otherwise.
    """
    # Materialize any StoredObjects in operand
    materialized = run_sync(materialize_context(input.operand))
    result = eval_templated_object(input.collection, operand=materialized)

    # Treat None as empty collection (will be handled by empty check below)
    if result is None:
        result = []
    elif not is_iterable(result):
        raise ApplicationError(
            f"Scatter collection is not iterable: {type(result)}: {result}",
            non_retryable=True,
        )

    items = list(result)
    # Guard CollectionObject: only use chunked storage when externalization
    # is enabled. Fall back to inline list for non-externalized deployments.
    if config.TRACECAT__RESULT_EXTERNALIZATION_ENABLED:
        return run_sync(_store_collection_as_refs(input.key, items))
    else:
        return InlineObject(data=items, typename="list")


def _patch_object(
    obj: dict[str, Any], *, path: str, value: Any, sep: str = "."
) -> None:
    """Patch a nested dict at the given path."""
    *stem, leaf = path.split(sep=sep)
    current: dict[str, Any] = obj
    for key in stem:
        current = current.setdefault(key, {})
    current[leaf] = value


def _partition_errors(items: list[Any]) -> tuple[list[Any], list[ActionErrorInfo]]:
    results: list[Any] = []
    errors: list[ActionErrorInfo] = []
    for item in items:
        if info := _as_error_info(item):
            errors.append(info)
        else:
            results.append(item)
    return results, errors


def _is_error_info(detail: Any) -> bool:
    if isinstance(detail, ActionErrorInfo):
        return True
    if not isinstance(detail, Mapping):
        return False
    try:
        ActionErrorInfoAdapter.validate_python(detail)
        return True
    except Exception:
        return False


def _as_error_info(detail: Any) -> ActionErrorInfo | None:
    try:
        return ActionErrorInfoAdapter.validate_python(detail)
    except Exception:
        return None


def _resolve_subflow_batch(
    input: ResolveSubflowBatchActivityInput,
) -> ResolvedSubflowBatch:
    """Resolve subflow args for a batch of iterations.

    1. Materialize context and evaluate for_each expression
    2. Slice to batch [start:start+size]
    3. For each item, patch context with var.item and evaluate args
    4. Detect if configs are uniform → return single or list
    5. Store trigger_inputs as CollectionObject

    Environment override precedence (highest to lowest):
    1. args.environment (subflow-specific)
    2. (Child DSL default is applied later in workflow)
    """
    task = input.task

    if not task.for_each:
        raise ApplicationError(
            "ResolveSubflowBatchActivityInput requires task.for_each",
            non_retryable=True,
        )

    # Materialize any StoredObjects in operand
    materialized = run_sync(materialize_context(input.operand))

    # Get iterables from for_each expression
    iterators = get_iterables_from_expression(expr=task.for_each, operand=materialized)

    # Collect all items and slice to batch
    all_items = list(zip(*iterators, strict=False))
    batch_items = all_items[input.batch_start : input.batch_start + input.batch_size]

    if not batch_items:
        raise ApplicationError(
            f"Empty batch: start={input.batch_start}, size={input.batch_size}, total={len(all_items)}",
            non_retryable=True,
        )

    # Evaluate args for each iteration in the batch
    configs: list[ResolvedSubflowConfig] = []
    trigger_inputs_list: list[Any] = []

    for items in batch_items:
        # Patch context with var.item (and other iterator variables)
        patched_context = cast(dict[str, Any], materialized.copy())
        for iterator_path, iterator_value in items:
            _patch_object(
                obj=patched_context,
                path=ExprContext.LOCAL_VARS + iterator_path,
                value=iterator_value,
            )

        # Evaluate the full task args with patched context
        evaluated_args = eval_templated_object(dict(task.args), operand=patched_context)

        # Extract DSL config fields
        configs.append(
            ResolvedSubflowConfig(
                environment=evaluated_args.get("environment"),
                timeout=evaluated_args.get("timeout"),
            )
        )

        # Extract trigger_inputs
        trigger_inputs_list.append(evaluated_args.get("trigger_inputs"))

    # Optimize: if all configs are identical, return single config
    first_config = configs[0]
    all_same = all(c == first_config for c in configs)
    final_configs: ResolvedSubflowConfig | list[ResolvedSubflowConfig] = (
        first_config if all_same else configs
    )

    # Store each trigger_inputs as individual StoredObject
    # This allows direct passing to each child workflow
    storage = get_object_storage()
    trigger_inputs_stored: list[StoredObject] = []
    for i, trigger_input in enumerate(trigger_inputs_list):
        key = collection_item_key(input.key, i)
        stored = run_sync(storage.store(key, trigger_input))
        trigger_inputs_stored.append(stored)

    return ResolvedSubflowBatch(
        configs=final_configs,
        trigger_inputs=trigger_inputs_stored,
    )


def _evaluate_loop_iterations(
    task: ActionStatement,
    materialized: MaterializedExecutionContext,
    dsl_config: DSLConfig,
) -> tuple[list[Any], DSLConfig | list[DSLConfig]]:
    """Evaluate trigger_inputs and configs for each loop iteration.

    CPU-bound function to be run in thread pool via asyncio.to_thread().

    Returns:
        Tuple of (trigger_inputs_list, runtime_configs).
        runtime_configs is single DSLConfig if all identical, else list.
    """
    if not task.for_each:
        raise ApplicationError(
            "task.for_each is required for loop iterations",
            non_retryable=True,
        )
    iterators = get_iterables_from_expression(expr=task.for_each, operand=materialized)
    all_items = list(zip(*iterators, strict=False))

    if len(all_items) > MAX_LOOP_ITERATIONS:
        raise ApplicationError(
            f"Loop exceeds max iterations: {len(all_items)} > {MAX_LOOP_ITERATIONS}",
            non_retryable=True,
        )

    trigger_inputs_list: list[Any] = []
    configs_list: list[DSLConfig] = []
    first_config: DSLConfig | None = None
    all_configs_same = True

    for items in all_items:
        # Patch context with var.item (and other iterator variables)
        patched_context = cast(dict[str, Any], materialized.copy())
        for iterator_path, iterator_value in items:
            _patch_object(
                obj=patched_context,
                path=ExprContext.LOCAL_VARS + iterator_path,
                value=iterator_value,
            )

        # Evaluate the full task args with patched context
        iter_evaluated_args = eval_templated_object(
            dict(task.args), operand=patched_context
        )
        iter_val_args = ExecuteSubflowArgs.model_validate(iter_evaluated_args)

        # Environment precedence: args.environment > dsl.config
        resolved_environment = iter_val_args.environment or dsl_config.environment
        resolved_timeout = iter_val_args.timeout or dsl_config.timeout

        config = DSLConfig(
            environment=resolved_environment,
            timeout=resolved_timeout,
        )

        # Track if configs diverge
        if first_config is None:
            first_config = config
        elif all_configs_same and config != first_config:
            all_configs_same = False

        trigger_inputs_list.append(iter_val_args.trigger_inputs)
        configs_list.append(config)

    # T | list[T] optimization: return single config if all identical
    runtime_configs: DSLConfig | list[DSLConfig] = (
        first_config if all_configs_same and first_config else configs_list
    )

    return trigger_inputs_list, runtime_configs


async def _prepare_subflow(input: PrepareSubflowActivityInput) -> PreparedSubflowResult:
    """Implementation of prepare_subflow_activity."""
    # Late imports to avoid circular dependency
    from tracecat.workflow.management.definitions import WorkflowDefinitionsService
    from tracecat.workflow.management.management import WorkflowsManagementService

    task = input.task

    # Materialize any StoredObjects in operand
    materialized = await materialize_context(input.operand)

    # Evaluate task args to get workflow_id or workflow_alias
    evaluated_args = eval_templated_object(task.args, operand=materialized)
    val_args = ExecuteSubflowArgs.model_validate(evaluated_args)

    # Resolve workflow ID
    wf_id: WorkflowUUID
    if workflow_alias := val_args.workflow_alias:
        # Resolve alias to workflow ID
        async with WorkflowsManagementService.with_session(input.role) as service:
            resolved_id = await service.resolve_workflow_alias(
                alias=workflow_alias,
                use_committed=input.use_committed,
            )
            if resolved_id is None:
                raise ApplicationError(
                    f"Workflow alias '{workflow_alias}' not found",
                    non_retryable=True,
                )
            wf_id = WorkflowUUID.new(resolved_id)
    elif workflow_id := val_args.workflow_id:
        wf_id = WorkflowUUID.new(workflow_id)
    else:
        raise ApplicationError(
            "Either workflow_id or workflow_alias must be provided",
            non_retryable=True,
        )

    # Fetch workflow definition
    async with WorkflowDefinitionsService.with_session(role=input.role) as service:
        defn = await service.get_definition_by_workflow_id(
            wf_id, version=evaluated_args.get("version")
        )
        if not defn:
            raise ApplicationError(
                f"Workflow definition not found for {wf_id.short()}",
                non_retryable=True,
            )
    dsl = DSLInput(**defn.content)
    registry_lock = (
        RegistryLock.model_validate(defn.registry_lock) if defn.registry_lock else None
    )

    # For single subflows (no for_each), evaluate args and return
    if not task.for_each:
        evaluated_args = eval_templated_object(dict(task.args), operand=materialized)
        val_args = ExecuteSubflowArgs.model_validate(evaluated_args)

        runtime_config = DSLConfig(
            environment=val_args.environment or dsl.config.environment,
            timeout=val_args.timeout or dsl.config.timeout,
        )

        return PreparedSubflowResult(
            wf_id=wf_id,
            dsl=dsl,
            registry_lock=registry_lock,
            trigger_inputs=None,
            runtime_configs=runtime_config,
        )

    # For looped subflows: evaluate all trigger_inputs and runtime_configs
    # Run CPU-bound expression evaluation in thread pool to avoid blocking event loop
    trigger_inputs_list, runtime_configs = await asyncio.to_thread(
        _evaluate_loop_iterations,
        task=task,
        materialized=materialized,
        dsl_config=dsl.config,
    )

    # Guard CollectionObject: only use chunked storage when externalization
    # is enabled. Fall back to inline list for non-externalized deployments.
    trigger_inputs_stored: StoredObject
    if config.TRACECAT__RESULT_EXTERNALIZATION_ENABLED:
        trigger_inputs_stored = await _store_collection_as_refs(
            f"{input.key}/trigger_inputs", trigger_inputs_list
        )
    else:
        trigger_inputs_stored = InlineObject(data=trigger_inputs_list, typename="list")

    return PreparedSubflowResult(
        wf_id=wf_id,
        dsl=dsl,
        registry_lock=registry_lock,
        trigger_inputs=trigger_inputs_stored,
        runtime_configs=runtime_configs,
    )
