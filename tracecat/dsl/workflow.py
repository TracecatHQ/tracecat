from __future__ import annotations

import asyncio
import itertools
import json
from collections import defaultdict
from collections.abc import (
    Awaitable,
    Callable,
    Coroutine,
    Generator,
    Iterable,
)
from datetime import timedelta
from typing import Any, TypedDict

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    FailureError,
)

with workflow.unsafe.imports_passed_through():
    import jsonpath_ng.lexer  # noqa
    import jsonpath_ng.parser  # noqa
    import tracecat_registry  # noqa
    from pydantic import ValidationError

    from tracecat import identifiers
    from tracecat.concurrency import GatheringTaskGroup
    from tracecat.contexts import RunContext, ctx_logger, ctx_role, ctx_run
    from tracecat.dsl.action import (
        DSLActivities,
        ValidateActionActivityInput,
    )
    from tracecat.dsl.common import DSLInput, DSLRunArgs, ExecuteChildWorkflowArgs
    from tracecat.dsl.enums import FailStrategy, LoopStrategy, SkipStrategy, TaskMarker
    from tracecat.dsl.models import (
        ActionStatement,
        ArgsT,
        DSLConfig,
        DSLContext,
        DSLEnvironment,
        DSLNodeResult,
        RunActionInput,
    )
    from tracecat.dsl.validation import (
        ValidateTriggerInputsActivityInputs,
        validate_trigger_inputs_activity,
    )
    from tracecat.expressions.core import TemplateExpression
    from tracecat.expressions.eval import eval_templated_object
    from tracecat.expressions.shared import ExprContext
    from tracecat.logger import logger
    from tracecat.registry.executor import evaluate_templated_args, iter_for_each
    from tracecat.types.exceptions import (
        TracecatCredentialsError,
        TracecatDSLError,
        TracecatException,
        TracecatExpressionError,
        TracecatNotFoundError,
        TracecatValidationError,
    )
    from tracecat.types.validation import ValidationResult
    from tracecat.workflow.management.definitions import (
        get_workflow_definition_activity,
    )
    from tracecat.workflow.management.models import GetWorkflowDefinitionActivityInputs
    from tracecat.workflow.schedules.models import GetScheduleActivityInputs
    from tracecat.workflow.schedules.service import WorkflowSchedulesService


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


class DSLScheduler:
    """Manage only scheduling of tasks in a topological-like order."""

    _queue_wait_timeout = 1
    skip_strategy: SkipStrategy
    """Decide how to handle tasks that are marked for skipping."""

    def __init__(
        self,
        *,
        activity_coro: Callable[[ActionStatement[ArgsT]], Coroutine[Any, Any, None]],
        dsl: DSLInput,
        skip_strategy: SkipStrategy = SkipStrategy.PROPAGATE,
    ):
        self.dsl = dsl
        self.tasks: dict[str, ActionStatement[ArgsT]] = {}
        self.adj: dict[str, set[str]] = defaultdict(set)
        self.indegrees: dict[str, int] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        # self.running_tasks: dict[str, asyncio.Task[None]] = {}
        self.completed_tasks: set[str] = set()
        # Tasks can be marked for termination.
        # This is useful for tasks that are
        self.marked_tasks: dict[str, TaskMarker] = {}
        self.skip_strategy = skip_strategy
        self.task_exceptions: dict[str, BaseException] = {}

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
        except ApplicationError as e:
            self.logger.error(
                "Application error in DSLScheduler",
                task_ref=task_ref,
                msg=e.message,
                non_retryable=e.non_retryable,
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
        # Set runtime args
        self.role = args.role
        self.start_to_close_timeout = args.timeout
        ctx_role.set(self.role)
        wf_info = workflow.info()

        self.logger = logger.bind(
            wf_id=args.wf_id,
            wf_exec_id=wf_info.workflow_id,
            wf_run_id=wf_info.run_id,
            role=self.role,
            unit="dsl-workflow-runner",
        )
        ctx_logger.set(self.logger)
        self.logger.debug("DSL workflow started", args=args)

        # Figure out what this worker will be running
        # Setup workflow definition
        if args.dsl:
            # Use the provided DSL
            self.logger.debug("Using provided workflow definition")
            self.dsl = args.dsl
            self.dispatch_type = "push"
        else:
            # Otherwise, fetch the latest workflow definition
            self.logger.debug("Fetching latest workflow definition")
            try:
                self.dsl = await self._get_workflow_definition(args.wf_id)
            except TracecatException as e:
                self.logger.error("Failed to fetch workflow definition")
                raise ApplicationError(
                    "Failed to fetch workflow definition",
                    non_retryable=True,
                    type=e.__class__.__name__,
                ) from e
            self.dispatch_type = "pull"

        # Consolidate runtime config
        if "runtime_config" in args.model_fields_set:
            # XXX(warning): This section must be handled with care.
            # Particularly because of how Pydantic handles unset fields.
            # We allow incoming runtime config in args to override the DSL config.

            # Use the override runtime config if it's set
            # If we receive runtime config in args, we must
            # consolidate the args in this order:
            # 1. runtime_config.environment (override by caller)
            # 2. dsl.config.environment (set in wf defn)

            logger.warning(
                "Runtime config was set",
                args_config=args.runtime_config,
                dsl_config=self.dsl.config,
            )
            set_fields = args.runtime_config.model_dump(exclude_unset=True)
            self.runtime_config = self.dsl.config.model_copy(update=set_fields)
        else:
            # Otherwise default to the DSL config
            logger.warning(
                "Runtime config was not set, using DSL config",
                dsl_config=self.dsl.config,
            )
            self.runtime_config = self.dsl.config
        logger.warning("Runtime config after", runtime_config=self.runtime_config)

        # Consolidate trigger inputs
        if args.schedule_id:
            self.logger.debug("Fetching schedule trigger inputs")
            try:
                trigger_inputs = await self._get_schedule_trigger_inputs(
                    schedule_id=args.schedule_id, worflow_id=args.wf_id
                )
            except TracecatNotFoundError as e:
                raise ApplicationError(
                    "Failed to fetch trigger inputs as the schedule was not found",
                    non_retryable=True,
                    type=e.__class__.__name__,
                ) from e
        else:
            self.logger.debug("Using provided trigger inputs")
            trigger_inputs = args.trigger_inputs or {}

        try:
            validation_result = await self._validate_trigger_inputs(trigger_inputs)
            logger.info("Trigger inputs are valid", validation_result=validation_result)
        except ValidationError as e:
            logger.error("Failed to validate trigger inputs", error=e.errors())
            raise ApplicationError(
                (
                    "Failed to validate trigger inputs"
                    f"\n\n{json.dumps(e.errors(), indent=2)}"
                ),
                non_retryable=True,
                type=e.__class__.__name__,
            ) from e

        # Prepare user facing context
        self.context = DSLContext(
            ACTIONS={},
            INPUTS=self.dsl.inputs,
            TRIGGER=trigger_inputs,
            ENV=DSLEnvironment(
                workflow={
                    "start_time": wf_info.start_time,
                    "dispatch_type": self.dispatch_type,
                },
                environment=self.runtime_config.environment,
                variables={},
            ),
        )

        # All the starting config has been consolidated, can safely set the run context
        # Internal facing context
        self.run_context = RunContext(
            wf_id=args.wf_id,
            wf_exec_id=wf_info.workflow_id,
            wf_run_id=wf_info.run_id,
            environment=self.runtime_config.environment,
        )
        ctx_run.set(self.run_context)

        self.dep_list = {task.ref: task.depends_on for task in self.dsl.actions}

        self.logger.info(
            "Running DSL task workflow",
            runtime_config=self.runtime_config,
            timeout=self.start_to_close_timeout,
        )

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

    async def execute_task(self, task: ActionStatement[ArgsT]) -> None:
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
                    child_run_args = await self._prepare_child_workflow(task)
                    logger.trace(
                        "Child workflow prepared", child_run_args=child_run_args
                    )
                    # This is the original child runtime args, preset by the DSL
                    # In contrast, task.args are the runtime args that the parent workflow provided
                    action_result = await self._execute_child_workflow(
                        task=task, child_run_args=child_run_args
                    )

                else:
                    # Below this point, we're executing the task
                    logger.trace(
                        "Running action",
                        task_ref=task.ref,
                        runtime_config=self.runtime_config,
                    )
                    action_result = await self._run_action(task)

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
            except ValidationError as e:
                logger.error("Runtime validation error", error=e.errors())
                raise e
            except Exception as e:
                msg = f"Task execution failed with unexpected error: {e}"
                logger.error(
                    "Activity execution failed with unexpected error", error=msg
                )
                raise ApplicationError(
                    msg, non_retryable=True, type=e.__class__.__name__
                ) from e

    async def _execute_child_workflow(
        self,
        task: ActionStatement[ExecuteChildWorkflowArgs],
        child_run_args: DSLRunArgs,
    ) -> Any:
        self.logger.debug("Execute child workflow", child_run_args=child_run_args)
        if task.for_each:
            # In for loop, child run args are shared among all iterations
            return await self._execute_child_workflow_loop(
                task=task, child_run_args=child_run_args
            )
        else:
            # At this point,
            # Child run args
            # Task args here refers to the args passed to the child
            args = evaluate_templated_args(task, context=self.context)
            self.logger.trace(
                "Executing child workflow",
                child_run_args=child_run_args,
                task_args=task.args,
                evaluated_args=args,
            )

            # Always set the trigger inputs in the child run args
            child_run_args.trigger_inputs = args.get("trigger_inputs")

            # Override the runtime config in the child run args
            # Override the environment in the child run args
            # XXX: We must use the default environment in the child workflow DSL if none is provided
            self.logger.debug(
                "Options",
                child_environment=child_run_args.runtime_config.environment,
                task_environment=args.get("environment"),
                dsl_environment=child_run_args.dsl.config.environment,
            )
            child_run_args.runtime_config.environment = (
                args.get("environment") or child_run_args.dsl.config.environment
            )
            return await self._run_child_workflow(child_run_args)

    async def _execute_child_workflow_loop(
        self,
        *,
        task: ActionStatement[ExecuteChildWorkflowArgs],
        child_run_args: DSLRunArgs,
    ) -> list[Any]:
        loop_strategy = LoopStrategy(
            task.args.get("loop_strategy", LoopStrategy.PARALLEL)
        )
        fail_strategy = FailStrategy(
            task.args.get("fail_strategy", FailStrategy.ISOLATED)
        )
        self.logger.trace(
            "Executing child workflow in loop",
            dsl_run_args=child_run_args,
            loop_strategy=loop_strategy,
            fail_strategy=fail_strategy,
        )

        iterator = iter_for_each(task=task, context=self.context)
        if loop_strategy == LoopStrategy.PARALLEL:
            action_result = await self._execute_child_workflow_batch(
                batch=iterator,
                base_run_args=child_run_args,
                fail_strategy=fail_strategy,
            )

        elif loop_strategy == LoopStrategy.BATCH:
            action_result = []
            batch_size = task.args.get("batch_size") or 16
            for batch in itertools.batched(iterator, batch_size):
                results = await self._execute_child_workflow_batch(
                    batch=batch,
                    base_run_args=child_run_args,
                    fail_strategy=fail_strategy,
                )
                action_result.extend(results)
        else:
            # Sequential
            action_result = []
            for patched_args in iterator:
                child_run_args.trigger_inputs = patched_args.get("trigger_inputs", {})
                result = await self._run_child_workflow(child_run_args)
                action_result.append(result)
        return action_result

    async def _execute_child_workflow_batch(
        self,
        batch: Iterable[ExecuteChildWorkflowArgs],
        base_run_args: DSLRunArgs,
        *,
        fail_strategy: FailStrategy = FailStrategy.ISOLATED,
    ) -> list[Any]:
        def iter_patched_args() -> Generator[DSLRunArgs]:
            for patched_args in batch:
                cloned_args = base_run_args.model_copy()
                cloned_args.trigger_inputs = patched_args.get("trigger_inputs", {})
                cloned_args.runtime_config = base_run_args.runtime_config.model_copy()
                cloned_args.runtime_config.environment = (
                    patched_args.get("environment")
                    or base_run_args.runtime_config.environment
                )

                yield cloned_args

        if fail_strategy == FailStrategy.ALL:
            async with GatheringTaskGroup() as tg:
                for patched_run_args in iter_patched_args():
                    logger.trace(
                        "Run child workflow batch",
                        fail_strategy=fail_strategy,
                        patched_run_args=patched_run_args,
                    )
                    tg.create_task(self._run_child_workflow(patched_run_args))
            return tg.results()
        else:
            # Isolated
            coros = []
            for patched_run_args in iter_patched_args():
                logger.trace(
                    "Run child workflow batch",
                    fail_strategy=fail_strategy,
                    patched_run_args=patched_run_args,
                )
                coro = self._run_child_workflow(patched_run_args)
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
            self.context.pop(ExprContext.ENV.value, None)
            return self.context
        # Return some custom value that should be evaluated
        self.logger.trace("Returning value from expression")
        return eval_templated_object(self.dsl.returns, operand=self.context)

    async def _get_workflow_definition(
        self, workflow_id: identifiers.WorkflowID, version: int | None = None
    ) -> DSLInput:
        activity_inputs = GetWorkflowDefinitionActivityInputs(
            role=self.role, workflow_id=workflow_id, version=version
        )

        self.logger.debug(
            "Running get workflow definition activity", activity_inputs=activity_inputs
        )
        return await workflow.execute_activity(
            get_workflow_definition_activity,
            arg=activity_inputs,
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )

    async def _validate_trigger_inputs(
        self, trigger_inputs: dict[str, Any]
    ) -> ValidationResult:
        """Validate trigger inputs.

        Note
        ----
        Not sure why we can't just run the function directly here.
        Pydantic throws an invalid JsonSchema error when we do so.
        """

        validation_result = await workflow.execute_activity(
            validate_trigger_inputs_activity,
            arg=ValidateTriggerInputsActivityInputs(
                dsl=self.dsl,
                trigger_inputs=trigger_inputs,
            ),
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )
        return validation_result

    async def _get_schedule_trigger_inputs(
        self, schedule_id: identifiers.ScheduleID, worflow_id: identifiers.WorkflowID
    ) -> dict[str, Any]:
        """Get the trigger inputs for a schedule.

        Raises
        ------
        TracecatNotFoundError
            If the schedule is not found.
        """
        activity_inputs = GetScheduleActivityInputs(
            role=self.role, schedule_id=schedule_id, workflow_id=worflow_id
        )

        self.logger.debug(
            "Running get schedule activity", activity_inputs=activity_inputs
        )
        schedule_read = await workflow.execute_activity(
            WorkflowSchedulesService.get_schedule_activity,
            arg=activity_inputs,
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )
        return schedule_read.inputs

    async def _validate_action(self, task: ActionStatement[ArgsT]) -> None:
        result = await workflow.execute_activity(
            DSLActivities.validate_action_activity,
            arg=ValidateActionActivityInput(role=self.role, task=task),
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )
        if not result.ok:
            raise ApplicationError(
                f"Action validation failed: {result.message}",
                result.detail,
                non_retryable=True,
                type=TracecatValidationError.__name__,
            )

    async def _prepare_child_workflow(
        self, task: ActionStatement[ExecuteChildWorkflowArgs]
    ) -> DSLRunArgs:
        """Grab a workflow definition and create child workflow run args"""

        await self._validate_action(task)
        # environment is None here. This is coming from the action
        self.logger.trace("Validated child workflow args", task=task)

        args = task.args
        child_wf_id = args["workflow_id"]
        # Use the override if given, else fallback to the main registry version
        child_wfd_version = args.get("version")
        self.logger.debug("Child using version", version=child_wfd_version)
        dsl = await self._get_workflow_definition(
            workflow_id=child_wf_id, version=child_wfd_version
        )

        self.logger.debug(
            "Got workflow definition",
            dsl=dsl,
            args=args,
            dsl_config=dsl.config,
            self_config=self.runtime_config,
        )
        runtime_config = DSLConfig(
            # Override the environment in the runtime config,
            # otherwise use the default provided in the workflow definition
            environment=args.get("environment") or dsl.config.environment,
        )
        self.logger.debug("Runtime config", runtime_config=runtime_config)

        return DSLRunArgs(
            role=self.role,
            dsl=dsl,
            wf_id=child_wf_id,
            parent_run_context=ctx_run.get(),
            trigger_inputs=args["trigger_inputs"],
            runtime_config=runtime_config,
        )

    def _run_action(self, task: ActionStatement[ArgsT]) -> Awaitable[Any]:
        arg = RunActionInput(
            task=task,
            role=self.role,
            run_context=self.run_context,
            exec_context=self.context,
        )
        self.logger.debug("RUN UDF ACTIVITY", arg=arg, task=task)

        return workflow.execute_activity(
            DSLActivities.run_action_activity,
            arg=arg,
            start_to_close_timeout=timedelta(
                seconds=task.start_delay + task.retry_policy.timeout
            ),
            retry_policy=RetryPolicy(
                maximum_attempts=task.retry_policy.max_attempts,
            ),
        )

    def _run_child_workflow(self, run_args: DSLRunArgs) -> Awaitable[Any]:
        self.logger.info("Running child workflow", run_args=run_args)
        wf_exec_id = identifiers.workflow.exec_id(run_args.wf_id)
        return workflow.execute_child_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=workflow.info().task_queue,
            retry_policy=retry_policies["workflow:fail_fast"],
            execution_timeout=self.start_to_close_timeout,
        )

    def _should_skip_execution(self, task: ActionStatement[ArgsT]) -> bool:
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

    def _should_execute_child_workflow(self, task: ActionStatement[ArgsT]) -> bool:
        return task.action == "core.workflow.execute"
