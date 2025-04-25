from __future__ import annotations

import asyncio
import itertools
import json
import re
import uuid
from collections.abc import Generator, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    FailureError,
)

with workflow.unsafe.imports_passed_through():
    import dateparser  # noqa: F401
    import jsonpath_ng.ext.parser  # noqa: F401
    import jsonpath_ng.lexer  # noqa
    import jsonpath_ng.parser  # noqa
    import tracecat_registry  # noqa
    from pydantic import ValidationError
    from slugify import slugify

    from tracecat import config, identifiers
    from tracecat.concurrency import GatheringTaskGroup
    from tracecat.contexts import ctx_interaction, ctx_logger, ctx_role, ctx_run
    from tracecat.dsl.action import (
        DSLActivities,
        ValidateActionActivityInput,
    )
    from tracecat.dsl.common import (
        ChildWorkflowMemo,
        DSLInput,
        DSLRunArgs,
        ExecuteChildWorkflowArgs,
        dsl_execution_error_from_exception,
    )
    from tracecat.dsl.enums import (
        FailStrategy,
        LoopStrategy,
        PlatformAction,
        WaitStrategy,
    )
    from tracecat.dsl.models import (
        ActionErrorInfo,
        ActionErrorInfoAdapter,
        ActionStatement,
        DSLConfig,
        DSLEnvironment,
        DSLExecutionError,
        ExecutionContext,
        RunActionInput,
        RunContext,
        TaskResult,
        TriggerInputs,
    )
    from tracecat.dsl.scheduler import DSLScheduler
    from tracecat.dsl.validation import (
        ValidateTriggerInputsActivityInputs,
        validate_trigger_inputs_activity,
    )
    from tracecat.ee.interactions.decorators import maybe_interactive
    from tracecat.ee.interactions.models import (
        InteractionInput,
        InteractionResult,
        InteractionState,
    )
    from tracecat.ee.interactions.service import InteractionManager
    from tracecat.executor.service import evaluate_templated_args, iter_for_each
    from tracecat.expressions.common import ExprContext
    from tracecat.expressions.eval import eval_templated_object
    from tracecat.identifiers.workflow import WorkflowExecutionID, WorkflowID
    from tracecat.logger import logger
    from tracecat.types.exceptions import (
        TracecatCredentialsError,
        TracecatDSLError,
        TracecatException,
        TracecatExpressionError,
        TracecatNotFoundError,
        TracecatValidationError,
    )
    from tracecat.validation.models import ValidationResult
    from tracecat.workflow.executions.models import ErrorHandlerWorkflowInput
    from tracecat.workflow.management.definitions import (
        get_workflow_definition_activity,
    )
    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.management.models import (
        GetErrorHandlerWorkflowIDActivityInputs,
        GetWorkflowDefinitionActivityInputs,
        ResolveWorkflowAliasActivityInputs,
    )
    from tracecat.workflow.schedules.models import GetScheduleActivityInputs
    from tracecat.workflow.schedules.service import WorkflowSchedulesService


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
    "activity:fail_slow": RetryPolicy(maximum_attempts=6),
    "workflow:fail_fast": RetryPolicy(
        # XXX: Do not set max attempts to 0, it will default to unlimited
        maximum_attempts=1,
        non_retryable_error_types=non_retryable_error_types,
    ),
}


@workflow.defn
class DSLWorkflow:
    """Manage only the state and execution of the DSL workflow."""

    @workflow.init
    def __init__(self, args: DSLRunArgs) -> None:
        self.role = args.role
        self.start_to_close_timeout = args.timeout
        wf_info = workflow.info()
        # Tracecat wf exec id == Temporal wf exec id
        self.wf_exec_id = wf_info.workflow_id
        # Tracecat wf run id == Temporal wf run id
        self.wf_run_id = wf_info.run_id
        self.logger = logger.bind(
            wf_id=args.wf_id,
            wf_exec_id=self.wf_exec_id,
            wf_run_id=self.wf_run_id,
            role=self.role,
            service="dsl-workflow-runner",
        )
        # Set runtime args
        ctx_role.set(self.role)
        ctx_logger.set(self.logger)

        self.logger.debug("DSL workflow started", args=args)
        try:
            self.logger.info(
                "Workflow info",
                run_timeout=wf_info.run_timeout,
                execution_timeout=wf_info.execution_timeout,
                task_timeout=wf_info.task_timeout,
                retry_policy=wf_info.retry_policy,
                history_events_length=wf_info.get_current_history_length(),
                history_events_size_bytes=wf_info.get_current_history_size(),
            )
        except Exception as e:
            self.logger.error("Failed to show workflow info", error=e)

        self.interactions = InteractionManager(self)

    @workflow.query
    def get_interaction_states(self) -> dict[uuid.UUID, InteractionState]:
        """Get the interaction states."""
        return self.interactions.states

    @workflow.update
    def interaction_handler(self, input: InteractionInput) -> InteractionResult:
        """Handle interactions from the workflow and return a result."""
        return self.interactions.handle_interaction(input)

    @interaction_handler.validator
    def validate_interaction_handler(self, input: InteractionInput) -> None:
        """Validate the interaction handler."""
        return self.interactions.validate_interaction(input)

    @workflow.run
    async def run(self, args: DSLRunArgs) -> Any:
        # Set DSL
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

        # Note that we can't run the error handler above this
        # Run the workflow with error handling
        try:
            return await self._run_workflow(args)
        except ApplicationError as e:
            # Application error
            self.logger.warning(
                "Error running workflow, running error handler",
                type=e.__class__.__name__,
            )
            # 1. Get the error handler workflow ID
            handler_wf_id = await self._get_error_handler_workflow_id(args)
            if handler_wf_id is None:
                self.logger.warning("No error handler workflow ID found, raising error")
                raise e

            if e.details:
                err_info_map = e.details[0]
                self.logger.info("Raising error info", err_info_data=err_info_map)
                if not isinstance(err_info_map, dict):
                    logger.error(
                        "Unexpected error info object",
                        err_info_map=err_info_map,
                        type=type(err_info_map).__name__,
                    )
                    # TODO: There's likely a nicer way to gracefully handle this
                    # instead of a sentinel error value
                    errors = [
                        ActionErrorInfo(
                            ref="N/A",
                            message=f"Unexpected error info object of type {type(err_info_map).__name__}: {err_info_map}",
                            type=type(err_info_map).__name__,
                        )
                    ]
                else:
                    errors = [
                        ActionErrorInfoAdapter.validate_python(data)
                        for data in err_info_map.values()
                    ]
            else:
                errors = None

            try:
                err_run_args = await self._prepare_error_handler_workflow(
                    message=e.message,
                    handler_wf_id=handler_wf_id,
                    orig_wf_id=args.wf_id,
                    orig_wf_exec_id=self.wf_exec_id,
                    orig_dsl=self.dsl,
                    errors=errors,
                )
                await self._run_error_handler_workflow(err_run_args)
            except Exception as err_handler_exc:
                self.logger.error(
                    "Failed to run error handler workflow",
                    error=err_handler_exc,
                )
                raise err_handler_exc from e

            # Finally, raise the original error
            raise e
        except Exception as e:
            # Platform error
            self.logger.error(
                "Unexpected error running workflow",
                type=e.__class__.__name__,
                error=e,
            )
            raise e

    async def _run_workflow(self, args: DSLRunArgs) -> Any:
        """Actual workflow execution logic."""
        wf_info = workflow.info()

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

            logger.debug(
                "Runtime config was set",
                args_config=args.runtime_config,
                dsl_config=self.dsl.config,
            )
            set_fields = args.runtime_config.model_dump(exclude_unset=True)
            self.runtime_config = self.dsl.config.model_copy(update=set_fields)
        else:
            # Otherwise default to the DSL config
            logger.debug(
                "Runtime config was not set, using DSL config",
                dsl_config=self.dsl.config,
            )
            self.runtime_config = self.dsl.config
        logger.debug("Runtime config after", runtime_config=self.runtime_config)

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
        self.context: ExecutionContext = {
            ExprContext.ACTIONS: {},
            ExprContext.INPUTS: self.dsl.inputs,
            ExprContext.TRIGGER: trigger_inputs,
            ExprContext.ENV: DSLEnvironment(
                workflow={
                    "start_time": wf_info.start_time,
                    "dispatch_type": self.dispatch_type,
                    "execution_id": self.wf_exec_id,
                    "run_id": self.wf_run_id,
                },
                environment=self.runtime_config.environment,
                variables={},
            ),
        }

        # All the starting config has been consolidated, can safely set the run context
        # Internal facing context
        self.run_context = RunContext(
            wf_id=args.wf_id,
            wf_exec_id=wf_info.workflow_id,
            wf_run_id=uuid.UUID(wf_info.run_id, version=4),
            environment=self.runtime_config.environment,
        )
        ctx_run.set(self.run_context)

        self.dep_list = {task.ref: task.depends_on for task in self.dsl.actions}

        self.logger.info(
            "Running DSL task workflow",
            runtime_config=self.runtime_config,
            timeout=self.start_to_close_timeout,
        )

        self.scheduler = DSLScheduler(
            executor=self.execute_task,  # type: ignore
            dsl=self.dsl,
            context=self.context,
        )
        try:
            task_exceptions = await self.scheduler.start()
        except Exception as e:
            msg = f"DSL scheduler failed with unexpected error: {e}"
            raise ApplicationError(
                msg, non_retryable=True, type=e.__class__.__name__
            ) from e

        if task_exceptions:
            n_exc = len(task_exceptions)
            formatted_exc = "\n".join(
                f"{'=' * 20} ({i + 1}/{n_exc}) {details.expr_context}.{ref} {'=' * 20}\n\n{info.exception!s}"
                for i, (ref, info) in enumerate(task_exceptions.items())
                if (details := info.details)
            )
            # NOTE: This error is shown in the final activity in the workflow history
            raise ApplicationError(
                f"Workflow failed with {n_exc} task exception(s)\n\n{formatted_exc}",
                # We should add the details of the exceptions to the error message because this will get captured
                # in the error handler workflow
                {ref: info.details for ref, info in task_exceptions.items()},
                non_retryable=True,
                type=ApplicationError.__name__,
            )

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

    async def _handle_timers(self, task: ActionStatement) -> None:
        """Perform any timing control flow logic (start_delay, wait_until).

        Note
        -----
        - asyncio.sleep() produces a Temporal durable timer when called within a workflow.
        """
        ### Timing control flow logic
        # If we have a retry_until, we need to run wait_until inside.
        # If we have a wait_until, we need to create a durable timer
        if task.wait_until:
            self.logger.info("Creating wait until timer", wait_until=task.wait_until)

            # Parse the delay until date
            wait_until = await workflow.execute_activity(
                DSLActivities.parse_wait_until_activity,
                task.wait_until,
                start_to_close_timeout=timedelta(seconds=10),
            )
            self.logger.info("Parsed wait until date", wait_until=wait_until)
            if wait_until is None:
                # Unreachable as this should have been validated at the API level
                raise ApplicationError(
                    "Invalid wait until date",
                    non_retryable=True,
                )

            current_time = datetime.now(UTC)
            logger.info("Current time", current_time=current_time)
            wait_until_dt = datetime.fromisoformat(wait_until)
            if wait_until_dt > current_time:
                duration = wait_until_dt - current_time
                self.logger.info(
                    "Waiting until", wait_until=wait_until, duration=duration
                )
                await asyncio.sleep(duration.total_seconds())
            else:
                self.logger.warning(
                    "Wait until is in the past, skipping timer",
                    wait_until=wait_until,
                    current_time=current_time,
                )
        # Create a durable timer if we have a start_delay
        elif task.start_delay > 0:
            logger.info("Starting action with delay", delay=task.start_delay)
            # In Temporal 1.9.0+, we can use workflow.sleep() as well
            await asyncio.sleep(task.start_delay)

    async def execute_task(self, task: ActionStatement) -> Any:
        """Execute a task and manage the results."""
        if task.retry_policy.retry_until:
            return await self._execute_task_until_condition(task)
        return await self._execute_task(task)

    async def _execute_task_until_condition(self, task: ActionStatement) -> TaskResult:
        """Execute a task until a condition is met."""
        retry_until = task.retry_policy.retry_until
        if retry_until is None:
            raise ValueError("Retry until is not set")
        ctx = self.context.copy()
        result = None
        while True:
            # NOTE: This only works with successful results
            result = await self._execute_task(task)
            ctx[ExprContext.ACTIONS][task.ref] = result
            retry_until_result = eval_templated_object(retry_until.strip(), operand=ctx)
            if not isinstance(retry_until_result, bool):
                try:
                    retry_until_result = bool(retry_until_result)
                except Exception:
                    raise ApplicationError(
                        "Retry until result is not a boolean", non_retryable=True
                    ) from None
            if retry_until_result:
                break
        return result

    @maybe_interactive
    async def _execute_task(self, task: ActionStatement) -> TaskResult:
        """Purely execute a task and manage the results.


        Prelude
        ------
        - Before this point, we've already evaluated conditional branching logic (run_if) and decided
            that this node must be executed.
        - We should now perform any timing control flow logic (start_delay, wait_until).
        - Note that we're not inside an activity here, so any timers created are DURABLE TEMPORAL TIMERS

        Preflight checks
        ---------------
        1. Perform any timing control flow logic
            - Create a durable timer if we have a start_delay
            - Create a durable timer if we have a wait_until
            - If we have both, the wait_until timer will take precedence
        2. Decide whether we're running a child workflow or not
        """

        logger.info("Begin task execution", task_ref=task.ref)
        task_result = TaskResult(result=None, result_typename=type(None).__name__)

        try:
            # Handle timing control flow logic
            await self._handle_timers(task)

            # Do action stuff
            match task.action:
                case PlatformAction.CHILD_WORKFLOW_EXECUTE:
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
                case _:
                    # Below this point, we're executing the task
                    logger.trace(
                        "Running action",
                        task_ref=task.ref,
                        runtime_config=self.runtime_config,
                    )
                    action_result = await self._run_action(task)
            logger.trace("Action completed successfully", action_result=action_result)
            task_result.update(
                result=action_result, result_typename=type(action_result).__name__
            )
        # NOTE: By the time we receive an exception, we've exhausted all retry attempts
        # Note that execute_task is called by the scheduler, so we don't have to return ApplicationError
        except (ActivityError, ChildWorkflowError, FailureError) as e:
            # These are deterministic and expected errors that
            err_type = e.__class__.__name__
            msg = self.ERROR_TYPE_TO_MESSAGE[err_type]
            self.logger.warning(msg, role=self.role, e=e, cause=e.cause, type=err_type)
            match cause := e.cause:
                case ApplicationError(details=details) if details:
                    err_info = details[0]
                    err_type = cause.type or err_type
                    task_result.update(error=err_info, error_typename=err_type)
                    # Reraise the cause, as it's wrapped by the ApplicationError
                    raise cause from e
                case _:
                    self.logger.warning("Unexpected error cause", cause=cause)
                    task_result.update(error=e.message, error_typename=err_type)
                    raise ApplicationError(
                        e.message, non_retryable=True, type=err_type
                    ) from cause

        except TracecatExpressionError as e:
            err_type = e.__class__.__name__
            detail = e.detail or "Error occurred when handling an expression"
            raise ApplicationError(detail, non_retryable=True, type=err_type) from e

        except ValidationError as e:
            logger.warning("Runtime validation error", error=e.errors())
            task_result.update(
                error=e.errors(), error_typename=ValidationError.__name__
            )
            raise e
        except Exception as e:
            err_type = e.__class__.__name__
            msg = f"Task execution failed with unexpected error: {e}"
            logger.error(
                "Activity execution failed with unexpected error",
                error=msg,
                type=err_type,
            )
            task_result.update(error=msg, error_typename=err_type)
            raise ApplicationError(msg, non_retryable=True, type=err_type) from e
        finally:
            logger.trace("Setting action result", task_result=task_result)
            self.context[ExprContext.ACTIONS][task.ref] = task_result  # type: ignore
        return task_result

    ERROR_TYPE_TO_MESSAGE = {
        ActivityError.__name__: "Activity execution failed",
        ChildWorkflowError.__name__: "Child workflow execution failed",
        FailureError.__name__: "Workflow execution failed",
        ValidationError.__name__: "Runtime validation error",
    }

    async def _execute_child_workflow(
        self,
        task: ActionStatement,
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

            if child_run_args.dsl is None:
                raise ValueError("Child run args must have a DSL")
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
            return await self._run_child_workflow(task, child_run_args)

    async def _execute_child_workflow_loop(
        self,
        *,
        task: ActionStatement,
        child_run_args: DSLRunArgs,
    ) -> list[Any]:
        loop_strategy = LoopStrategy(task.args.get("loop_strategy", LoopStrategy.BATCH))
        fail_strategy = FailStrategy(
            task.args.get("fail_strategy", FailStrategy.ISOLATED)
        )
        self.logger.trace(
            "Executing child workflow in loop",
            dsl_run_args=child_run_args,
            loop_strategy=loop_strategy,
            fail_strategy=fail_strategy,
        )

        def iterator() -> Generator[ExecuteChildWorkflowArgs]:
            for args in iter_for_each(task=task, context=self.context):
                yield ExecuteChildWorkflowArgs(**args)

        it = iterator()

        if loop_strategy == LoopStrategy.PARALLEL:
            return await self._execute_child_workflow_batch(
                batch=it,
                task=task,
                base_run_args=child_run_args,
                fail_strategy=fail_strategy,
            )
        else:
            batch_size = {
                LoopStrategy.SEQUENTIAL: 1,
                LoopStrategy.BATCH: int(task.args.get("batch_size", 32)),
            }[loop_strategy]

            action_result = []
            for batch in itertools.batched(it, batch_size):
                batch_result = await self._execute_child_workflow_batch(
                    batch=batch,
                    task=task,
                    base_run_args=child_run_args,
                    fail_strategy=fail_strategy,
                )
                action_result.extend(batch_result)
            return action_result

    async def _execute_child_workflow_batch(
        self,
        batch: Iterable[ExecuteChildWorkflowArgs],
        task: ActionStatement,
        base_run_args: DSLRunArgs,
        *,
        fail_strategy: FailStrategy = FailStrategy.ISOLATED,
    ) -> list[Any]:
        def iter_patched_args() -> Generator[DSLRunArgs]:
            for args in batch:
                cloned_args = base_run_args.model_copy()
                cloned_args.trigger_inputs = args.trigger_inputs
                cloned_args.runtime_config = base_run_args.runtime_config.model_copy()
                cloned_args.runtime_config.environment = (
                    args.environment or base_run_args.runtime_config.environment
                )
                cloned_args.runtime_config.timeout = (
                    args.timeout or base_run_args.runtime_config.timeout
                )

                yield cloned_args

        if fail_strategy == FailStrategy.ALL:
            async with GatheringTaskGroup() as tg:
                for i, patched_run_args in enumerate(iter_patched_args()):
                    logger.trace(
                        "Run child workflow batch",
                        fail_strategy=fail_strategy,
                        patched_run_args=patched_run_args,
                    )
                    tg.create_task(
                        self._run_child_workflow(task, patched_run_args, loop_index=i)
                    )
                    await workflow.sleep(0.1)
            return tg.results()
        else:
            # Isolated
            coros = []
            for i, patched_run_args in enumerate(iter_patched_args()):
                logger.trace(
                    "Run child workflow batch",
                    fail_strategy=fail_strategy,
                    patched_run_args=patched_run_args,
                )
                coro = self._run_child_workflow(task, patched_run_args, loop_index=i)
                coros.append(coro)
                await workflow.sleep(0.1)
            gather_result = await asyncio.gather(*coros, return_exceptions=True)
            result: list[DSLExecutionError | Any] = [
                dsl_execution_error_from_exception(val)
                if isinstance(val, BaseException)
                else val
                for val in gather_result
            ]
            return result

    def _handle_return(self) -> Any:
        self.logger.debug("Handling return", context=self.context)
        if self.dsl.returns is None:
            # Return the context
            # XXX: Don't return ENV context for now
            self.logger.trace("Returning DSL context")
            self.context.pop(ExprContext.ENV, None)
            return self.context
        # Return some custom value that should be evaluated
        self.logger.trace("Returning value from expression")
        return eval_templated_object(self.dsl.returns, operand=self.context)

    async def _resolve_workflow_alias(self, wf_alias: str) -> identifiers.WorkflowID:
        activity_inputs = ResolveWorkflowAliasActivityInputs(
            workflow_alias=wf_alias, role=self.role
        )
        wf_id = await workflow.execute_activity(
            WorkflowsManagementService.resolve_workflow_alias_activity,
            arg=activity_inputs,
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )
        if not wf_id:
            raise ValueError(f"Workflow alias {wf_alias} not found")
        return wf_id

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
            retry_policy=retry_policies["activity:fail_slow"],
        )

    async def _validate_trigger_inputs(
        self, trigger_inputs: TriggerInputs
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
    ) -> dict[str, Any] | None:
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

    async def _validate_action(self, task: ActionStatement) -> None:
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

    async def _prepare_child_workflow(self, task: ActionStatement) -> DSLRunArgs:
        """Grab a workflow definition and create child workflow run args"""

        args = ExecuteChildWorkflowArgs.model_validate(task.args)
        # If wfid already exists don't do anything
        # Before we execute the child workflow, resolve the workflow alias
        # environment is None here. This is coming from the action
        self.logger.trace("Validated child workflow args", task=task)

        if args.workflow_id:
            child_wf_id = args.workflow_id
        elif args.workflow_alias:
            child_wf_id = await self._resolve_workflow_alias(args.workflow_alias)
        else:
            raise ValueError("Either workflow_id or workflow_alias must be provided")

        dsl = await self._get_workflow_definition(child_wf_id, version=args.version)

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
            environment=args.environment or dsl.config.environment,
            timeout=args.timeout or dsl.config.timeout,
        )
        self.logger.debug("Runtime config", runtime_config=runtime_config)

        return DSLRunArgs(
            role=self.role,
            dsl=dsl,
            wf_id=child_wf_id,
            parent_run_context=ctx_run.get(),
            trigger_inputs=args.trigger_inputs,
            runtime_config=runtime_config,
        )

    async def _run_action(self, task: ActionStatement) -> Any:
        arg = RunActionInput(
            task=task,
            run_context=self.run_context,
            exec_context=self.context,
            interaction_context=ctx_interaction.get(),
        )
        self.logger.debug("Running action", action=task.action)

        return await workflow.execute_activity(
            DSLActivities.run_action_activity,
            args=(arg, self.role),
            start_to_close_timeout=timedelta(
                seconds=task.start_delay + task.retry_policy.timeout
            ),
            retry_policy=RetryPolicy(
                maximum_attempts=task.retry_policy.max_attempts,
            ),
        )

    async def _run_child_workflow(
        self, task: ActionStatement, run_args: DSLRunArgs, loop_index: int | None = None
    ) -> Any:
        wf_exec_id = identifiers.workflow.generate_exec_id(run_args.wf_id)
        wf_info = workflow.info()
        # XXX(safety): This has been validated in prepare_child_workflow
        args = ExecuteChildWorkflowArgs.model_construct(**task.args)
        # Use Temporal memo to store the action ref in the child workflow run
        memo = ChildWorkflowMemo(
            action_ref=task.ref, loop_index=loop_index, wait_strategy=args.wait_strategy
        ).model_dump()
        self.logger.info(
            "Running child workflow",
            run_args=run_args,
            wait_strategy=args.wait_strategy,
            memo=memo,
        )

        match args.wait_strategy:
            case WaitStrategy.DETACH:
                child_wf_handle = await workflow.start_child_workflow(
                    DSLWorkflow.run,
                    run_args,
                    id=wf_exec_id,
                    retry_policy=retry_policies["workflow:fail_fast"],
                    # Propagate the parent workflow attributes to the child workflow
                    task_queue=wf_info.task_queue,
                    execution_timeout=wf_info.execution_timeout,
                    task_timeout=wf_info.task_timeout,
                    memo=memo,
                    search_attributes=wf_info.typed_search_attributes,
                    # DETACH specific options
                    # Abandon the child workflow if the parent is cancelled
                    parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                )
                result = child_wf_handle.id
            case _:
                # WAIT and all other strategies
                result = await workflow.execute_child_workflow(
                    DSLWorkflow.run,
                    run_args,
                    id=wf_exec_id,
                    retry_policy=retry_policies["workflow:fail_fast"],
                    # Propagate the parent workflow attributes to the child workflow
                    task_queue=wf_info.task_queue,
                    execution_timeout=wf_info.execution_timeout,
                    task_timeout=wf_info.task_timeout,
                    memo=memo,
                    search_attributes=wf_info.typed_search_attributes,
                )
        return result

    async def _get_error_handler_workflow_id(
        self, args: DSLRunArgs
    ) -> WorkflowID | None:
        """Get the error handler workflow ID.

        This is done by checking if the error is a TracecatValidationError or
        TracecatExpressionError.
        """
        return await workflow.execute_activity(
            WorkflowsManagementService.get_error_handler_workflow_id,
            arg=GetErrorHandlerWorkflowIDActivityInputs(args=args, role=self.role),
            start_to_close_timeout=args.timeout,
            retry_policy=retry_policies["activity:fail_fast"],
        )

    async def _prepare_error_handler_workflow(
        self,
        *,
        message: str,
        handler_wf_id: WorkflowID,
        orig_wf_id: WorkflowID,
        orig_wf_exec_id: WorkflowExecutionID,
        orig_dsl: DSLInput,
        errors: list[ActionErrorInfo] | None = None,
    ) -> DSLRunArgs:
        """Grab a workflow definition and create error handler workflow run args"""

        dsl = await self._get_workflow_definition(handler_wf_id)

        self.logger.debug(
            "Got workflow definition for error handler",
            dsl=dsl,
            dsl_config=dsl.config,
            self_config=self.runtime_config,
        )
        runtime_config = DSLConfig(
            # Override the environment in the runtime config,
            # otherwise use the default provided in the workflow definition
            environment=self.runtime_config.environment,
            timeout=self.runtime_config.timeout,
        )
        self.logger.debug("Runtime config", runtime_config=runtime_config)

        url = None
        if match := re.match(identifiers.workflow.WF_EXEC_ID_PATTERN, orig_wf_exec_id):
            if self.role.workspace_id is None:
                logger.warning("Workspace ID is required to create error handler URL")
            else:
                try:
                    workflow_id = identifiers.workflow.WorkflowUUID.new(
                        match.group("workflow_id")
                    ).short()
                    exec_id = match.group("execution_id")
                    url = (
                        f"{config.TRACECAT__PUBLIC_APP_URL}/workspaces/{self.role.workspace_id}"
                        f"/workflows/{workflow_id}/executions/{exec_id}"
                    )
                except Exception as e:
                    logger.error("Error parsing workflow execution ID", error=e)

        return DSLRunArgs(
            role=self.role,
            dsl=dsl,
            wf_id=handler_wf_id,
            parent_run_context=ctx_run.get(),
            trigger_inputs=ErrorHandlerWorkflowInput(
                message=message,
                handler_wf_id=handler_wf_id,
                orig_wf_id=orig_wf_id,
                orig_wf_exec_id=orig_wf_exec_id,
                orig_wf_exec_url=url,
                orig_wf_title=orig_dsl.title,
                errors=errors,
            ),
            runtime_config=runtime_config,
        )

    async def _run_error_handler_workflow(
        self,
        args: DSLRunArgs,
    ) -> None:
        self.logger.info("Running error handler workflow", args=args)
        wf_exec_id = identifiers.workflow.generate_exec_id(args.wf_id)
        wf_info = workflow.info()
        if args.dsl is None:
            raise ValueError("DSL is required to run error handler workflow")
        # Use Temporal memo to store the action ref in the child workflow run
        memo = ChildWorkflowMemo(action_ref=slugify(args.dsl.title, separator="_"))
        await workflow.execute_child_workflow(
            DSLWorkflow.run,
            args,
            id=wf_exec_id,
            retry_policy=retry_policies["workflow:fail_fast"],
            # Propagate the parent workflow attributes to the child workflow
            task_queue=wf_info.task_queue,
            execution_timeout=wf_info.execution_timeout,
            task_timeout=wf_info.task_timeout,
            memo=memo.model_dump(),
            search_attributes=wf_info.typed_search_attributes,
        )
