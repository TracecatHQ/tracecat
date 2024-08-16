from __future__ import annotations

import asyncio
import itertools
from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Iterator
from datetime import timedelta
from enum import StrEnum, auto
from typing import Any, TypedDict

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    FailureError,
)

with workflow.unsafe.imports_passed_through():
    import httpx
    import jsonpath_ng.lexer  # noqa
    import jsonpath_ng.parser  # noqa
    import lark  # noqa
    from pydantic import BaseModel, ValidationError

    from tracecat import identifiers
    from tracecat.auth.sandbox import AuthSandbox
    from tracecat.concurrency import GatheringTaskGroup
    from tracecat.contexts import RunContext, ctx_logger, ctx_role, ctx_run
    from tracecat.dsl.common import DSLInput, DSLRunArgs
    from tracecat.dsl.io import resolve_success_output
    from tracecat.dsl.models import ActionStatement, ActionTest, DSLNodeResult
    from tracecat.expressions.core import TemplateExpression
    from tracecat.expressions.eval import (
        eval_templated_object,
        extract_templated_secrets,
        get_iterables_from_expression,
    )
    from tracecat.expressions.shared import ExprContext
    from tracecat.logging import logger
    from tracecat.registry import registry
    from tracecat.types.auth import Role
    from tracecat.types.exceptions import (
        TracecatCredentialsError,
        TracecatDSLError,
        TracecatException,
        TracecatExpressionError,
        TracecatValidationError,
    )
    from tracecat.workflow.management.definitions import (
        get_workflow_definition_activity,
    )
    from tracecat.workflow.management.models import GetWorkflowDefinitionActivityInputs


class DSLContext(TypedDict, total=False):
    INPUTS: dict[str, Any]
    """DSL Static Inputs context"""

    ACTIONS: dict[str, Any]
    """DSL Actions context"""

    TRIGGER: dict[str, Any]
    """DSL Trigger dynamic inputs context"""

    ENV: dict[str, Any]
    """DSL Environment context. Has metadata about the workflow."""

    @staticmethod
    def create_default(
        INPUTS: dict[str, Any] | None = None,
        ACTIONS: dict[str, Any] | None = None,
        TRIGGER: dict[str, Any] | None = None,
        ENV: dict[str, Any] | None = None,
    ) -> DSLContext:
        return DSLContext(
            INPUTS=INPUTS or {},
            ACTIONS=ACTIONS or {},
            TRIGGER=TRIGGER or {},
            ENV=ENV or {},
        )


class DSLExecutionError(TypedDict, total=False):
    """A proxy for an exception.

    This is the object that gets returned in place of an exception returned when
    using `asyncio.gather(..., return_exceptions=True)`, as Exception types aren't serializable."""

    is_error: bool
    """A flag to indicate that this object is an error."""

    type: str
    """The type of the exception. e.g. `ValueError`"""

    message: str
    """The message of the exception."""

    @staticmethod
    def from_exception(e: BaseException) -> DSLExecutionError:
        return DSLExecutionError(
            is_error=True,
            type=e.__class__.__name__,
            message=str(e),
        )


non_retryable_error_types = [
    # General
    Exception.__name__,
    TypeError.__name__,
    ValueError.__name__,
    RuntimeError.__name__,
    # Pydantic
    ValidationError.__name__,
    # Tracecat
    TracecatException.__name__,
    TracecatExpressionError.__name__,
    TracecatValidationError.__name__,
    TracecatDSLError.__name__,
    TracecatCredentialsError.__name__,
    # Temporal
    ApplicationError.__name__,
    ChildWorkflowError.__name__,
    FailureError.__name__,
]


retry_policies = {
    "activity:fail_fast": RetryPolicy(
        # XXX: Do not set max attempts to 0, it will default to unlimited
        maximum_attempts=1,
        non_retryable_error_types=non_retryable_error_types,
    ),
    "workflow:fail_fast": RetryPolicy(
        # XXX: Do not set max attempts to 0, it will default to unlimited
        maximum_attempts=1,
        non_retryable_error_types=non_retryable_error_types,
    ),
}


class TaskMarker(StrEnum):
    SKIP = auto()
    TERMINATED = auto()


class SkipStrategy(StrEnum):
    ISOLATE = auto()
    PROPAGATE = auto()


class LoopStrategy(StrEnum):
    PARALLEL = "parallel"
    BATCH = "batch"
    SEQUENTIAL = "sequential"


class FailStrategy(StrEnum):
    ISOLATED = "isolated"
    ALL = "all"


class DSLScheduler:
    """Manage only scheduling of tasks in a topological-like order."""

    _queue_wait_timeout = 1
    skip_strategy: SkipStrategy
    """Decide how to handle tasks that are marked for skipping."""

    def __init__(
        self,
        *,
        activity_coro: Callable[[ActionStatement], Coroutine[Any, Any, None]],
        dsl: DSLInput,
        skip_strategy: SkipStrategy = SkipStrategy.PROPAGATE,
    ):
        self.dsl = dsl
        self.tasks: dict[str, ActionStatement] = {}
        self.adj: dict[str, set[str]] = defaultdict(set)
        self.indegrees: dict[str, int] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        # self.running_tasks: dict[str, asyncio.Task[None]] = {}
        self.completed_tasks: set[str] = set()
        # Tasks can be marked for termination.
        # This is useful for tasks that are
        self.marked_tasks: dict[str, TaskMarker] = {}
        self.skip_strategy = skip_strategy
        self.task_exceptions = {}

        self.executor = activity_coro
        self.logger = ctx_logger.get(logger).bind(unit="dsl-scheduler")

        for task in dsl.actions:
            self.tasks[task.ref] = task
            self.indegrees[task.ref] = len(task.depends_on)
            for dep in task.depends_on:
                self.adj[dep].add(task.ref)

    async def _dynamic_task(self, task_ref: str) -> None:
        """Dynamic task execution.

        Tasks
        -----
        1. Run the task
        2. Manage the indegrees of the tasks
        """
        task = self.tasks[task_ref]
        try:
            await self.executor(task)

            # For now, tasks that were marked to skip also join this set
            self.completed_tasks.add(task_ref)
            self.logger.info("Task completed", task_ref=task_ref)

            # Update child indegrees
            # ----------------------
            # Treat a skipped task as completed, update as usual.
            # Any child task whose indegree reaches 0 must check if all its parent
            # dependencies we skipped. if ALL parents were skipped, then the child
            # task is also marked for skipping. If ANY parent was not skipped, then
            # the child task is added to the queue.

            # The intuition here is that if you have a task that becomes unreachable,
            # then some of its children will also become unreachable. A node becomes unreachable
            # if there is no one successful oath that leads to it.

            # This allows us to have diamond-shaped graphs where some branches can be skipped
            # but at the join point, if any parent was not skipped, then the child can still be executed.
            async with asyncio.TaskGroup() as tg:
                for next_task_ref in self.adj[task_ref]:
                    self.indegrees[next_task_ref] -= 1
                    if self.indegrees[next_task_ref] == 0:
                        if (
                            self.skip_strategy == SkipStrategy.PROPAGATE
                            and self.task_is_reachable(next_task_ref)
                        ):
                            self.mark_task(next_task_ref, TaskMarker.SKIP)
                        tg.create_task(self.queue.put(next_task_ref))
        except ActivityError as e:
            self.logger.error(
                "Activity error in DSLScheduler",
                task_ref=task_ref,
                msg=e.message,
                retry_state=e.retry_state,
            )
            self.task_exceptions[task_ref] = e
        except Exception as e:
            self.logger.error(
                "Unexpected error in DSLScheduler", task_ref=task_ref, error=e
            )
            self.task_exceptions[task_ref] = e

    async def dynamic_start(self) -> None:
        """Run the scheduler in dynamic mode."""
        self.queue.put_nowait(self.dsl.entrypoint.ref)
        while not self.task_exceptions and (
            not self.queue.empty() or len(self.completed_tasks) < len(self.tasks)
        ):
            self.logger.trace(
                "Waiting for tasks.",
                qsize=self.queue.qsize(),
                n_completed=len(self.completed_tasks),
                n_tasks=len(self.tasks),
            )
            try:
                task_ref = await asyncio.wait_for(
                    self.queue.get(), timeout=self._queue_wait_timeout
                )
            except TimeoutError:
                continue

            asyncio.create_task(self._dynamic_task(task_ref))
        if self.task_exceptions:
            self.logger.error(
                "DSLScheduler got task exceptions, stopping...",
                n_exceptions=len(self.task_exceptions),
                exceptions=self.task_exceptions,
                n_completed=len(self.completed_tasks),
                n_tasks=len(self.tasks),
            )
            raise ApplicationError(
                "Task exceptions occurred", *self.task_exceptions.values()
            )
        self.logger.info("All tasks completed")

    def mark_task(self, task_ref: str, marker: TaskMarker) -> None:
        self.marked_tasks[task_ref] = marker

    def task_is_reachable(self, task_ref: str) -> bool:
        """Check whether a task is reachable by checking if all its dependencies were skipped."""
        return all(
            self.marked_tasks.get(parent) == TaskMarker.SKIP
            for parent in self.tasks[task_ref].depends_on
        )

    async def _static_task(self, task_ref: str) -> None:
        raise NotImplementedError

    async def static_start(self) -> None:
        raise NotImplementedError

    def start(self) -> Awaitable[None]:
        if self.dsl.config.scheduler == "dynamic":
            return self.dynamic_start()
        else:
            return self.static_start()


@workflow.defn
class DSLWorkflow:
    """Manage only the state and execution of the DSL workflow."""

    @workflow.run
    async def run(self, args: DSLRunArgs) -> Any:
        # Setup
        self.role = args.role
        ctx_role.set(self.role)
        wf_info = workflow.info()
        self.start_to_close_timeout = args.run_config.get(
            "timeout", timedelta(minutes=5)
        )

        self.run_ctx = RunContext(
            wf_id=args.wf_id,
            wf_exec_id=wf_info.workflow_id,
            wf_run_id=wf_info.run_id,
        )
        ctx_run.set(self.run_ctx)

        self.logger = logger.bind(
            run_ctx=self.run_ctx, role=self.role, unit="dsl-workflow-runner"
        )
        ctx_logger.set(self.logger)

        self.dsl = args.dsl
        self.context = DSLContext(
            ACTIONS={},
            INPUTS=self.dsl.inputs,
            TRIGGER=args.trigger_inputs or {},
            ENV={"workflow": {"start_time": wf_info.start_time}},
        )

        self.dep_list = {task.ref: task.depends_on for task in self.dsl.actions}
        self.action_test_map = {test.ref: test for test in self.dsl.tests}
        self.logger.info("Running DSL task workflow")

        self.scheduler = DSLScheduler(activity_coro=self.execute_task, dsl=self.dsl)
        try:
            await self.scheduler.start()
        except ApplicationError as e:
            raise ApplicationError(
                e.message, non_retryable=True, type=e.__class__.__name__
            ) from e
        except Exception as e:
            msg = f"DSL workflow execution failed with unexpected error: {e}"
            raise ApplicationError(
                msg,
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e

        try:
            self.logger.info("DSL workflow completed")
            return self._handle_return()
        except TracecatExpressionError as e:
            raise ApplicationError(
                f"Couldn't parse return value expression: {e}",
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
        except Exception as e:
            raise ApplicationError(
                f"Unexpected error handling return value: {e}",
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e

    async def execute_task(self, task: ActionStatement) -> None:
        """Purely execute a task and manage the results.

        Preflight checks
        ---------------
        1. Evaluate `run_if` condition
        2. Resolve all templated arguments
        3. If there's an ActionTest, skip execution and return the patched result.
            - Note that we still schedule the task for execution, but we don't actually run it.
        """
        with self.logger.contextualize(task_ref=task.ref):
            if self._should_skip_execution(task):
                self._mark_task(task.ref, TaskMarker.SKIP)
                return

            try:
                logger.info("Begin task execution", task_ref=task.ref)
                # Check for a child workflow
                if self._should_execute_child_workflow(task):
                    # NOTE: We don't support (nor recommend, unless a use case is justified) passing SECRETS to child workflows
                    # 1. Prepare the child workflow
                    logger.trace("Preparing child workflow")
                    child_run_args_data = await self._prepare_child_workflow(task)
                    child_run_args = DSLRunArgs.model_validate(child_run_args_data)

                    if task.for_each:
                        loop_strategy = LoopStrategy(
                            task.args.get("loop_strategy", "parallel")
                        )
                        logger.trace(
                            "Executing child workflow in loop",
                            dsl_run_args=child_run_args,
                            loop_strategy=loop_strategy,
                        )

                        iterator = iter_for_each(task=task, context=self.context)
                        if loop_strategy == LoopStrategy.PARALLEL:
                            action_result = await self._execute_workflow_batch(
                                batch=iterator,
                                run_args=child_run_args,
                                fail_strategy=task.args.get(
                                    "fail_strategy", "isolated"
                                ),
                            )

                        elif loop_strategy == LoopStrategy.BATCH:
                            action_result = []
                            batch_size = task.args.get("batch_size", 16)
                            for batch in itertools.batched(iterator, batch_size):
                                results = await self._execute_workflow_batch(
                                    batch=batch,
                                    run_args=child_run_args,
                                    fail_strategy=task.args.get(
                                        "fail_strategy", "isolated"
                                    ),
                                )
                                action_result.extend(results)
                        else:
                            # Sequential
                            action_result = []
                            for patched_args in iterator:
                                child_run_args.trigger_inputs = patched_args.get(
                                    "trigger_inputs", {}
                                )
                                result = await self._run_child_workflow(child_run_args)
                                action_result.append(result)
                    else:
                        logger.trace(
                            "Executing child workflow",
                            dsl_run_args=child_run_args,
                        )

                        args = eval_templated_object(task.args, operand=self.context)
                        child_run_args.trigger_inputs = args.get("trigger_inputs", {})
                        action_result = await self._run_child_workflow(child_run_args)
                else:
                    # NOTE: We should check for loop iteration here.
                    # Activities should always execute without needing to manage control flow

                    # Below this point, we're executing the task
                    # Check for an action test
                    act_test = (
                        self.action_test_map.get(task.ref)
                        if self.dsl.config.enable_runtime_tests
                        else None
                    )
                    logger.trace("Running action", act_test=act_test)
                    action_result = await self._run_action(task, action_test=act_test)

                self.context[ExprContext.ACTIONS][task.ref] = DSLNodeResult(
                    result=action_result,
                    result_typename=type(action_result).__name__,
                )
            except ActivityError as e:
                logger.error("Activity execution failed", error=e.message)
                raise ApplicationError(
                    e.message, non_retryable=True, type=e.__class__.__name__
                ) from e
            except ChildWorkflowError as e:
                logger.error("Child workflow execution failed", error=e.message)
                raise ApplicationError(
                    e.message, non_retryable=True, type=e.__class__.__name__
                ) from e
            except FailureError as e:
                logger.error("Workflow execution failed", error=e.message)
                raise ApplicationError(
                    e.message, non_retryable=True, type=e.__class__.__name__
                ) from e
            except Exception as e:
                msg = f"Task execution failed with unexpected error: {e}"
                logger.error(
                    "Activity execution failed with unexpected error", error=msg
                )
                raise ApplicationError(
                    msg, non_retryable=True, type=e.__class__.__name__
                ) from e

    async def _execute_workflow_batch(
        self,
        batch: Iterable[Any],
        run_args: DSLRunArgs,
        *,
        fail_strategy: FailStrategy = FailStrategy.ISOLATED,
    ) -> list[Any]:
        if fail_strategy == FailStrategy.ALL:
            async with GatheringTaskGroup() as tg:
                for patched_args in batch:
                    run_args.trigger_inputs = patched_args.get("trigger_inputs", {})
                    tg.create_task(self._run_child_workflow(run_args))
            return tg.results()
        else:
            # Isolated
            coros = []
            for patched_args in batch:
                run_args.trigger_inputs = patched_args.get("trigger_inputs", {})
                # Shallow copy here to avoid sharing the same model object for each execution
                coro = self._run_child_workflow(run_args.model_copy())
                coros.append(coro)
            gather_result = await asyncio.gather(*coros, return_exceptions=True)
            result: list[DSLExecutionError | Any] = [
                DSLExecutionError.from_exception(val)
                if isinstance(val, BaseException)
                else val
                for val in gather_result
            ]
            return result

    def _handle_return(self) -> Any:
        if self.dsl.returns is None:
            # Return the context
            # XXX: Don't return ENV context for now
            self.logger.trace("Returning DSL context")
            self.context.pop(ExprContext.ENV, None)
            return self.context
        # Return some custom value that should be evaluated
        self.logger.trace("Returning value from expression")
        return eval_templated_object(self.dsl.returns, operand=self.context)

    def _prepare_child_workflow(self, task: ActionStatement) -> Awaitable[DSLRunArgs]:
        udf = registry.get(task.action)
        validated_args = udf.validate_args(**task.args)
        self.logger.trace(
            "Validated child workflow args", validated_args=validated_args
        )
        return workflow.execute_activity(
            get_workflow_definition_activity,
            arg=GetWorkflowDefinitionActivityInputs(
                role=self.role,
                task=task,
                run_context=self.run_ctx,
                **validated_args,
            ),
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )

    def _run_action(
        self, task: ActionStatement, action_test: ActionTest | None = None
    ) -> Awaitable[Any]:
        return workflow.execute_activity(
            _udf_key_to_activity_name(task.action),
            arg=UDFActionInput(
                task=task,
                role=self.role,
                run_context=self.run_ctx,
                exec_context=self.context,
                action_test=action_test,
            ),
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )

    def _run_child_workflow(self, run_args: DSLRunArgs) -> Awaitable[DSLContext]:
        wf_exec_id = identifiers.workflow.exec_id(run_args.wf_id)
        return workflow.execute_child_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=workflow.info().task_queue,
            retry_policy=retry_policies["workflow:fail_fast"],
            execution_timeout=self.start_to_close_timeout,
        )

    def _should_skip_execution(self, task: ActionStatement) -> bool:
        if self.scheduler.marked_tasks.get(task.ref) == TaskMarker.SKIP:
            self.logger.info("Task marked for skipping, skipped")
            return True
        # Evaluate the `run_if` condition
        if task.run_if is not None:
            expr = TemplateExpression(task.run_if, operand=self.context)
            self.logger.debug("`run_if` condition", task_run_if=task.run_if)
            if not bool(expr.result()):
                self.logger.info("Task `run_if` condition was not met, skipped")
                return True
        return False

    def _mark_task(self, task_ref: str, marker: TaskMarker) -> None:
        self.scheduler.mark_task(task_ref, marker)

    def _should_execute_child_workflow(self, task: ActionStatement) -> bool:
        return task.action == "core.workflow.execute"


class UDFActionInput(BaseModel):
    task: ActionStatement
    role: Role
    exec_context: dict[ExprContext, dict[str, Any]]
    run_context: RunContext
    action_test: ActionTest | None = None


def _udf_key_to_activity_name(key: str) -> str:
    return key.replace(".", "__")


class DSLActivities:
    """Container for all UDFs registered in the registry."""

    def __new__(cls):  # type: ignore
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def init(cls):
        """Create activity methods from the UDF registry and attach them to DSLActivities."""
        global registry
        for key in registry.keys:
            # path.to.method_name -> path__to__method_name
            method_name = _udf_key_to_activity_name(key)

            async def async_wrapper(input: UDFActionInput):
                return await cls.run_udf(input)

            fn = activity.defn(name=method_name)(async_wrapper)
            setattr(cls, method_name, staticmethod(fn))

        return cls

    @classmethod
    def get_activities(cls) -> list[Callable[[UDFActionInput], Any]]:
        """Get all loaded UDFs in the class."""
        return [
            getattr(cls, method_name)
            for method_name in dir(cls)
            if hasattr(getattr(cls, method_name), "__temporal_activity_definition")
        ]

    @classmethod
    def load(cls) -> list[Callable[[UDFActionInput], Any]]:
        """Load and return all UDFs in the class."""
        cls.init()
        return cls.get_activities()

    async def run_udf(input: UDFActionInput) -> Any:
        ctx_run.set(input.run_context)
        ctx_role.set(input.role)
        task = input.task

        act_logger = logger.bind(
            task_ref=task.ref, wf_id=input.run_context.wf_id, role=input.role
        )

        try:
            # Multi-phase expression resolution
            # ---------------------------------
            # 1. Resolve all expressions in all shared (non action-local) contexts
            # 2. Enter loop iteration (if any)
            # 3. Resolve all action-local expressions

            # Set
            # If there's a for loop, we need to process this action in parallel

            # Evaluate `SECRETS` context (XXX: You likely should use the secrets manager instead)
            # --------------------------
            # Securely inject secrets into the task arguments
            # 1. Find all secrets in the task arguments
            # 2. Load the secrets
            # 3. Inject the secrets into the task arguments using an enriched context
            # NOTE: Regardless of loop iteration, we should only make this call/substitution once!!
            secret_refs = extract_templated_secrets(task.args)
            async with AuthSandbox(secrets=secret_refs, target="context") as sandbox:
                context_with_secrets = {
                    **input.exec_context,
                    ExprContext.SECRETS: sandbox.secrets.copy(),
                }

            # When we're here, we've populated the task arguments with shared context values
            type = task.action
            ctx_logger.set(act_logger)

            udf = registry[type]
            act_logger.info(
                "Run udf",
                task_ref=task.ref,
                type=type,
                is_async=udf.is_async,
                args=task.args,
            )

            # We manually control the cache here for now.
            act_test = input.action_test
            if act_test and act_test.enable:
                # XXX: This will fail if we run it against a loop
                act_logger.warning(
                    f"Action test enabled, mocking the output of {task.ref!r}."
                    " You should not use this in production workflows."
                )
                if act_test.validate_args:
                    args = eval_templated_object(
                        task.args, operand=context_with_secrets
                    )
                    udf.validate_args(**args)
                result = await resolve_success_output(act_test)

            elif task.for_each:
                iterator = iter_for_each(task=task, context=context_with_secrets)
                try:
                    async with GatheringTaskGroup() as tg:
                        for patched_args in iterator:
                            tg.create_task(udf.run_async(patched_args))

                    result = tg.results()
                except* Exception as eg:
                    errors = [str(x) for x in eg.exceptions]
                    logger.error("Error resolving expressions", errors=errors)
                    raise TracecatException(
                        f"Error resolving expressions: {errors}",
                        detail={"errors": errors},
                    ) from eg

            else:
                args = eval_templated_object(task.args, operand=context_with_secrets)
                result = await udf.run_async(args)

            act_logger.debug("Result", result=result)
            return result

        except TracecatException as e:
            err_type = e.__class__.__name__
            msg = str(e)
            act_logger.error(f"{err_type} occurred: {msg}", error=e)
            raise ApplicationError(
                msg, e.detail, non_retryable=True, type=err_type
            ) from e
        except ApplicationError as e:
            act_logger.error("ApplicationError occurred", error=e)
            raise
        except httpx.HTTPStatusError as e:
            act_logger.error("HTTP status error occurred", error=e)
            raise ApplicationError(
                f"HTTP status error {e.response.status_code}",
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e
        except Exception as e:
            act_logger.error("Unexpected error occurred", error=e)
            raise ApplicationError(
                f"Unexpected error {e.__class__.__name__}",
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e


def iter_for_each(
    task: ActionStatement,
    context: DSLContext,
    *,
    assign_context: ExprContext = ExprContext.LOCAL_VARS,
    patch: bool = True,
) -> Iterator[Any]:
    """Yield patched contexts for each loop iteration."""
    # Evaluate the loop expression
    iterators = get_iterables_from_expression(expr=task.for_each, operand=context)

    # Assert that all length of the iterables are the same
    # This is a requirement for parallel processing
    # if len({len(expr.collection) for expr in iterators}) != 1:
    #     raise ValueError("All iterables must be of the same length")

    # Create a generator that zips the iterables together
    for i, items in enumerate(zip(*iterators, strict=False)):
        logger.trace("Loop iteration", iteration=i)
        # Patch the context with the loop item and evaluate the action-local expressions
        # We're copying this so that we don't pollute the original context
        # Currently, the only source of action-local expressions is the loop iteration
        # In the future, we may have other sources of action-local expressions
        patched_context = (
            context.copy()
            if patch
            # XXX: ENV is the only context that should be shared
            else DSLContext.create_default()
        )
        logger.trace("Context before patch", patched_context=patched_context)
        for iterator_path, iterator_value in items:
            patch_object(
                patched_context,
                path=assign_context + iterator_path,
                value=iterator_value,
            )
        logger.trace("Patched context", patched_context=patched_context)
        patched_args = eval_templated_object(task.args, operand=patched_context)
        logger.trace("Patched args", patched_args=patched_args)
        yield patched_args


def patch_object(obj: dict[str, Any], *, path: str, value: Any, sep: str = ".") -> None:
    *stem, leaf = path.split(sep=sep)
    for key in stem:
        obj = obj.setdefault(key, {})
    obj[leaf] = value
