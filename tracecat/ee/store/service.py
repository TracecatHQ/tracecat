from __future__ import annotations

from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat import config
from tracecat.contexts import ctx_logger
from tracecat.dsl.models import ExecutionContext, RunActionInput, TaskResult
from tracecat.ee.store.constants import OBJECT_REF_RESULT_TYPE
from tracecat.ee.store.models import (
    ObjectRef,
    ResolveConditionActivityInput,
    ResolveObjectActivityInput,
    ResolveObjectRefsActivityInput,
    StoreWorkflowResultActivityInput,
    as_object_ref,
)
from tracecat.ee.store.object_store import ObjectStore
from tracecat.expressions.common import ExprContext, IterableExpr, IterableExprAdapter
from tracecat.expressions.core import extract_action_and_secret_expressions
from tracecat.expressions.eval import eval_templated_object
from tracecat.logger import logger


async def resolve(obj: Any) -> Any:
    """
    Recursively traverses a Python object and resolves all ObjectRef instances.

    Args:
        obj: The object to traverse and resolve (dict, list, or any other Python object)
        context: The execution context for resolving references

    Returns:
        A new object with all ObjectRefs resolved to their actual values
    """

    # TODO(perf): Rewrite as iterative
    # Handle different types of objects
    match obj:
        # Typename structure match
        case {"result": result, "result_typename": result_typename} if (
            result_typename == OBJECT_REF_RESULT_TYPE
        ):
            logger.debug("EXPLICIT OBJECT REF PATH")
            # Can safely evaluate this as an object ref
            if obj_ref := as_object_ref(result):
                # Resolve the ObjectRef to its actual value
                result = await ObjectStore.get().get_object(obj_ref)
                logger.warning("Resolved ObjectRef", obj_ref=obj_ref, result=result)
                # Wrap it b
                return TaskResult(
                    result=result,
                    result_typename=type(result).__name__,
                )
            raise ValueError("Typename was object-ref but couldn't fetch the object.")
        case dict():
            # Create a new dictionary with resolved values
            logger.debug("DICTPATH", obj=obj)
            # If this is an object ref we're done
            if obj_ref := as_object_ref(obj):
                # Directly return the resolved value
                return await ObjectStore.get().get_object(obj_ref)
            else:
                return {k: await resolve(v) for k, v in obj.items()}
        case list():
            # Create a new list with resolved values
            return [await resolve(val) for val in obj]

    # For primitives and other non-container types, return as is
    return obj


# async def resolve_iterative(obj: Any) -> Any:
#     # Controls the traversal.
#     stack = [("$", obj)]
#     # We need to mutate `obj` as we go though it
#     while stack:
#         curr_path, curr_obj = stack.pop()
#         match curr_obj:
#             case {"key": key, "digest": digest, "metdata": metadata, "size": size}:
#                 # Create a new dictionary with resolved values
#                 obj_ref = ObjectRef(
#                     key=key,
#                     digest=digest,
#                     metadata=metadata,
#                     size=size,
#                 )
#                 resolved_obj = await ObjectStore.get().get_object(obj_ref)
#                 # Replace at path
#             case dict():
#                 # Queue all the dict values
#                 stack.extend(obj.values())


async def resolve_execution_context(input: RunActionInput) -> ExecutionContext:
    """Prepare the action context for running an action. If we're using the store pull from minio."""

    log = ctx_logger.get()
    context = input.exec_context.copy()
    if not config.TRACECAT__USE_OBJECT_STORE:
        log.debug("Object store is disabled, skipping action result fetching")
        return context

    # Actions
    # (1) Extract expressions: Grab the action refs that this action depends on
    log.debug("Store enabled, pulling action results into execution context")
    return await resolve_object_refs(input.task.args, context)


@activity.defn
async def resolve_condition_activity(input: ResolveConditionActivityInput) -> bool:
    """Resolve a condition expression. Throws an ApplicationError if the result
    cannot be converted to a boolean.
    """
    logger.debug("Resolve condition", condition=input.condition_expr)
    result = await resolve_templated_object(input.condition_expr, input.context)
    try:
        return bool(result)
    except Exception:
        raise ApplicationError(
            "Condition result could not be converted to a boolean",
            non_retryable=True,
        ) from None


@activity.defn
async def resolve_for_each_activity(
    input: ResolveObjectActivityInput,
) -> list[IterableExpr]:
    result = await resolve_templated_object(obj=input.obj, context=input.context)
    match result:
        case IterableExpr():
            return [result]
        case list():
            return [IterableExprAdapter.validate_python(v) for v in result]
        case dict():
            return [IterableExpr(**result)]
        case _:
            raise ApplicationError(f"Unexpected type for iterable expr {type(result)}")


@activity.defn
async def resolve_object_refs_activity(
    input: ResolveObjectRefsActivityInput,
) -> ExecutionContext:
    """Resolve the minimal set of object refs from the execution context."""
    return await resolve_object_refs(input.obj, input.context)


async def resolve_templated_object(obj: Any, context: ExecutionContext) -> Any:
    if config.TRACECAT__USE_OBJECT_STORE:
        logger.debug("Resolving object refs", obj=obj, context=context)
        context = await resolve_object_refs(obj, context)
    # Don't block the main workflow thread
    logger.debug("POINT A")
    return eval_templated_object(obj, operand=context)


@activity.defn
async def store_workflow_result_activity(
    input: StoreWorkflowResultActivityInput,
) -> ObjectRef:
    """Store the result of a workflow."""
    logger.info("Resolving templated object")
    context = await resolve_object_refs(obj=input.args, context=input.context)
    result = eval_templated_object(input.args, operand=context)
    obj_ref = await ObjectStore.get().put_object(obj=result)
    return obj_ref


async def resolve_object_refs(obj: Any, context: ExecutionContext) -> ExecutionContext:
    """Resolve the minimal set of object refs in the execution context."""
    exprs = extract_action_and_secret_expressions(obj=obj)
    extracted_action_refs = exprs[ExprContext.ACTIONS]
    if not extracted_action_refs:
        logger.info("No action refs in result")
        return context

    # (2) Pull action results from the store
    # We only pull the action results that are actually used in the template
    # We need to populate the action context with the action results
    # Inside the ExecutionContext, each action ref is mapped to an object ref
    # Grab each object ref and resolve it
    action_refs = list(extracted_action_refs)
    # Read keys from the action context.
    # This should be a dict[str, TaskResult]
    # NOTE: We must only replace TaskResult.result with the result
    action_context: dict[str, TaskResult] = context.get(ExprContext.ACTIONS, {})
    logger.warning("Action context", action_context=action_context)

    # ref2key: dict[str, ObjectRef] = {}
    logger.error("PARSING")
    for act_ref in action_refs:
        # For each action ref, we check if it's a blob
        act_res = action_context.get(act_ref)
        if act_res is None:
            # Shouldn't happen
            logger.warning("Action ref not found in action context", ref=act_ref)
            continue
        # NOTE: It's too naive to just check result_typename.
        # We should duck-type check ALL dict-type fields in the object
        # if act_res.get("result_typename") == OBJECT_REF_RESULT_TYPE:
        # This is a blob, parse it as object ref
        result = await resolve(act_res)
        action_context[act_ref]["result"] = result
        action_context[act_ref]["result_typename"] = type(result).__name__

        # result = act_res.get("result")
        # if obj_ref := as_object_ref(result):
        #     ref2key[act_ref] = obj_ref
        # else:
        #     # Shouldn't happen
        #     logger.warning(
        #         "Couldn't parse action ref result as ObjectRef",
        #         ref=act_ref,
        #         result=result,
        #     )
    logger.error("CONTEXT", action_context=action_context)
    # NOTE(perf): We could filter for unique keys here
    # result_objs = await store.get_byte_objects_by_key(
    #     keys=[ref.key for ref in ref2key.values()]
    # )
    # logger.warning("Got result objs", n=len(result_objs))

    # # We only update the actions that we fetched
    # for (act_ref, obj_ref), fetched_bytes in zip(
    #     ref2key.items(), result_objs, strict=True
    # ):
    #     if fetched_bytes is None:
    #         logger.warning("Object not found in store", key=obj_ref.key)
    #         continue

    #     # TODO: Handle checksum mismatch
    #     hashing.validate_digest(data=fetched_bytes, digest=obj_ref.digest)
    #     result = orjson.loads(fetched_bytes)
    #     action_context[act_ref]["result"] = result
    #     action_context[act_ref]["result_typename"] = type(result).__name__

    context.update(ACTIONS=action_context)
    logger.warning("Updated execution context", context=context)
    return context
