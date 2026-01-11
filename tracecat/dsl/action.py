from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import dateparser
from aiocache import Cache
from aiocache.backends.memory import asyncio
from pydantic import BaseModel
from temporalio import activity

from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    MaterializedExecutionContext,
    MaterializedTaskResult,
    RunActionInput,
    TaskResult,
)
from tracecat.exceptions import RegistryError, TracecatExpressionError
from tracecat.expressions.core import TemplateExpression
from tracecat.expressions.eval import eval_templated_object
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionValidateResponse
from tracecat.storage.object import (
    ExternalObject,
    InlineObject,
    StoredObject,
    get_object_storage,
)
from tracecat.validation.service import validate_registry_action_args

_cache = Cache(Cache.MEMORY, ttl=120)


async def _materialize_task_result(task_result: TaskResult) -> MaterializedTaskResult:
    """Materialize a TaskResult's StoredObject result to raw value.

    Handles collection_index for scatter items - when set, the stored result
    is a collection and we extract the item at that index.

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
        operand: Execution context containing ACTIONS, TRIGGER, etc.

    Returns:
        MaterializedExecutionContext with all StoredObjects replaced by raw values.
    """
    result: MaterializedExecutionContext = {}

    # Materialize ACTIONS - each value is a TaskResult with StoredObject result
    if actions := ctx.get("ACTIONS"):
        materialized_actions: dict[str, MaterializedTaskResult] = {}
        for ref, task_result in actions.items():
            materialized_actions[ref] = await _materialize_task_result(task_result)
        result["ACTIONS"] = materialized_actions

    # Materialize TRIGGER - always a StoredObject with uniform envelope
    if trigger := ctx.get("TRIGGER"):
        result["TRIGGER"] = await get_object_storage().retrieve(trigger)
    else:
        result["TRIGGER"] = None

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

    def __new__(cls):  # type: ignore
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
    async def validate_action_activity(
        input: ValidateActionActivityInput,
    ) -> RegistryActionValidateResponse:
        """Validate an action.
        Goals:
        - Validate the action arguments against the UDF spec.
        - Return the validated arguments.
        """
        try:
            async with get_async_session_context_manager() as session:
                result = await validate_registry_action_args(
                    session=session,
                    action_name=input.task.action,
                    args=input.task.args,
                    action_ref=input.task.ref,
                )

                if result.status == "error":
                    logger.warning(
                        "Error validating UDF args",
                        message=result.msg,
                        details=result.detail,
                    )
                return RegistryActionValidateResponse.from_validation_result(result)
        except KeyError as e:
            raise RegistryError(
                f"Action {input.task.action!r} not found in registry",
            ) from e

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
    async def store_scatter_collection_activity(
        key: str,
        items: list[Any],
    ) -> StoredObject:
        """Store scatter collection and return StoredObject with item count.

        Stores the entire collection as a single object. Returns the StoredObject
        and the number of items, allowing the scheduler to create indexed references.

        Args:
            key: Storage key for the collection
            items: List of items to store

        Returns:
            Dict with 'stored' (StoredObject) and 'count' (number of items)
        """
        storage = get_object_storage()
        stored = await storage.store(key, items)
        return stored

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
        obj: Any,
        operand: ExecutionContext,
    ) -> Any:
        """Evaluate templated objects using the expression engine.

        Materializes any StoredObjects in operand before evaluation. This ensures
        that expressions evaluate against raw values even when results are externalized.
        """
        # Materialize any StoredObjects in operand
        materialized = run_sync(materialize_context(operand))
        return eval_templated_object(obj, operand=materialized)


def run_sync[T: Any](coro: Coroutine[Any, Any, T]) -> T:
    loop = asyncio.get_event_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result()
