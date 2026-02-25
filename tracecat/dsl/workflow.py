from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Generator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import (
    RetryPolicy,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    FailureError,
)

with workflow.unsafe.imports_passed_through():
    import dateparser  # noqa: F401  # pyright: ignore[reportUnusedImport]
    import jsonpath_ng.ext.parser  # noqa: F401  # pyright: ignore[reportUnusedImport]
    import jsonpath_ng.lexer  # noqa  # pyright: ignore[reportUnusedImport]
    import jsonpath_ng.parser  # noqa  # pyright: ignore[reportUnusedImport]
    import tracecat_registry  # noqa  # pyright: ignore[reportUnusedImport]
    from pydantic import ValidationError
    from tracecat_ee.agent.types import AgentWorkflowID
    from tracecat_ee.agent.workflows.durable import (
        AgentWorkflowArgs,
        DurableAgentWorkflow,
    )

    from tracecat import config, identifiers
    from tracecat.agent.aliases import build_agent_alias
    from tracecat.agent.schemas import RunAgentArgs
    from tracecat.agent.session.types import AgentSessionEntity
    from tracecat.agent.types import AgentConfig
    from tracecat.concurrency import cooperative
    from tracecat.contexts import (
        ctx_interaction,
        ctx_logical_time,
        ctx_role,
        ctx_run,
        ctx_stream_id,
    )
    from tracecat.dsl.action import (
        BuildAgentArgsActivityInput,
        BuildPresetAgentArgsActivityInput,
        DSLActivities,
        EvaluateLoopedSubflowInputActivityInput,
        EvaluateTemplatedObjectActivityInput,
        NormalizeTriggerInputsActivityInputs,
        PrepareSubflowActivityInput,
        ResolveSubflowBatchActivityInput,
        SynchronizeCollectionObjectActivityInput,
    )
    from tracecat.dsl.common import (
        RETRY_POLICIES,
        AgentActionMemo,
        ChildWorkflowMemo,
        DSLInput,
        DSLRunArgs,
        ExecuteSubflowArgs,
        PreparedSubflowResult,
        ResolvedSubflowBatch,
        ResolvedSubflowInput,
        SubflowContext,
        dsl_execution_error_from_exception,
        get_trigger_type,
    )
    from tracecat.dsl.enums import (
        FailStrategy,
        LoopStrategy,
        PlatformAction,
        WaitStrategy,
    )
    from tracecat.dsl.scheduler import DSLScheduler
    from tracecat.dsl.schemas import (
        ROOT_STREAM,
        ActionStatement,
        DSLConfig,
        DSLEnvironment,
        ExecutionContext,
        RunActionInput,
        RunContext,
        StreamID,
        TaskResult,
    )
    from tracecat.dsl.types import ActionErrorInfo, ActionErrorInfoAdapter
    from tracecat.dsl.validation import (
        ResolveTimeAnchorActivityInputs,
        format_input_schema_validation_error,
        resolve_time_anchor_activity,
    )
    from tracecat.dsl.workflow_logging import get_workflow_logger
    from tracecat.ee.interactions.decorators import maybe_interactive
    from tracecat.ee.interactions.schemas import InteractionInput, InteractionResult
    from tracecat.ee.interactions.service import InteractionManager
    from tracecat.exceptions import (
        TracecatException,
        TracecatExpressionError,
        TracecatNotFoundError,
    )
    from tracecat.expressions.eval import is_template_only
    from tracecat.identifiers import WorkspaceID
    from tracecat.identifiers.workflow import (
        WorkflowExecutionID,
        WorkflowID,
        exec_id_to_parts,
    )
    from tracecat.registry.lock.types import RegistryLock
    from tracecat.storage.object import (
        CollectionObject,
        ExternalObject,
        InlineObject,
        StoredObject,
        StoredObjectValidator,
        action_collection_prefix,
        action_key,
        return_key,
        trigger_key,
    )
    from tracecat.validation.schemas import ValidationDetailListTA
    from tracecat.workflow.executions.constants import (
        WF_EXECUTION_MEMO_REGISTRY_LOCK_KEY,
    )
    from tracecat.workflow.executions.enums import (
        ExecutionType,
        TemporalSearchAttr,
        TriggerType,
    )
    from tracecat.workflow.executions.types import ErrorHandlerWorkflowInput
    from tracecat.workflow.management.definitions import (
        get_workflow_definition_activity,
        resolve_registry_lock_activity,
    )
    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.management.schemas import (
        GetErrorHandlerWorkflowIDActivityInputs,
        GetWorkflowDefinitionActivityInputs,
        ResolveRegistryLockActivityInputs,
        ResolveWorkflowAliasActivityInputs,
        WorkflowDefinitionActivityResult,
    )
    from tracecat.workflow.schedules.schemas import GetScheduleActivityInputs
    from tracecat.workflow.schedules.service import WorkflowSchedulesService


def _inherit_search_attributes_with_alias(
    base_attrs: TypedSearchAttributes | None,
    alias: str,
) -> TypedSearchAttributes:
    pairs: list[SearchAttributePair[Any]] = [
        TemporalSearchAttr.ALIAS.create_pair(alias)
    ]
    if base_attrs:
        pairs.extend(
            p
            for p in base_attrs.search_attributes
            if p.key != TemporalSearchAttr.ALIAS.key
        )
    return TypedSearchAttributes(search_attributes=pairs)


def _build_agent_child_search_attributes(
    info: workflow.Info,
    action_ref: str,
) -> TypedSearchAttributes:
    try:
        parent_wf_id, _ = exec_id_to_parts(info.workflow_id)
    except ValueError as e:
        raise RuntimeError(
            f"Malformed workflow ID when building agent child search attributes: {info.workflow_id}"
        ) from e
    alias = build_agent_alias(parent_wf_id, action_ref)
    return _inherit_search_attributes_with_alias(info.typed_search_attributes, alias)


@workflow.defn
class DSLWorkflow:
    """Manage only the state and execution of the DSL workflow.

    Note: dsl, dispatch_type, registry_lock, runtime_config, wf_start_time,
    time_anchor, context, run_context, dep_list, and scheduler are initialized
    in run() before _run_workflow() is called. They are not set in __init__
    because Temporal workflow init must be synchronous and cannot make activity calls.
    """

    # Instance variables initialized in run() before _run_workflow()
    # pyright: ignore[reportUninitializedInstanceVariable]
    dsl: DSLInput
    dispatch_type: str
    registry_lock: RegistryLock
    runtime_config: DSLConfig
    wf_start_time: datetime
    time_anchor: datetime
    context: ExecutionContext
    run_context: RunContext
    dep_list: dict[str, list[str]]
    scheduler: DSLScheduler

    @workflow.init
    def __init__(self, args: DSLRunArgs) -> None:
        self.role = args.role
        self.start_to_close_timeout = args.timeout
        """The activity execution timeout."""
        self.execution_type = args.execution_type
        """Execution type (draft or published). Draft executions use draft aliases for child workflows."""
        wf_info = workflow.info()
        # Tracecat wf exec id == Temporal wf exec id
        self.wf_exec_id = wf_info.workflow_id
        # Tracecat wf run id == Temporal wf run id
        self.wf_run_id = wf_info.run_id
        self.logger = get_workflow_logger(
            wf_id=args.wf_id,
            wf_exec_id=self.wf_exec_id,
            wf_run_id=self.wf_run_id,
            role=self.role,
            service="dsl-workflow-runner",
        )
        # Set runtime args
        ctx_role.set(self.role)

        self.logger.debug(
            "DSL workflow started", args=args, execution_type=self.execution_type
        )
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

    @workflow.update
    async def interaction_handler(self, input: InteractionInput) -> InteractionResult:
        """Handle interactions from the workflow and return a result."""
        return self.interactions.handle_interaction(input)

    @interaction_handler.validator
    def validate_interaction_handler(self, input: InteractionInput) -> None:
        """Validate the interaction handler."""
        return self.interactions.validate_interaction(input)

    def get_context(self, stream_id: StreamID | None = None) -> ExecutionContext:
        """Get the current execution context."""
        sid = stream_id or ctx_stream_id.get()
        return self.scheduler.streams[sid]

    @property
    def workspace_id(self) -> WorkspaceID:
        """Get the workspace ID."""
        if self.role.workspace_id is None:
            raise ValueError("Workspace ID is required")
        return self.role.workspace_id

    async def _heal_role_organization_id_if_missing(self) -> None:
        """Recover missing organization_id for legacy scheduled workflow roles."""
        if self.role.organization_id is not None:
            return

        if self.role.workspace_id is None:
            self.logger.warning(
                "Role is missing organization_id and workspace_id; skipping auto-heal"
            )
            return

        self.logger.warning(
            "Role missing organization_id; attempting workspace-based auto-heal",
            workspace_id=self.role.workspace_id,
        )
        organization_id = await workflow.execute_activity(
            WorkflowSchedulesService.get_workspace_organization_id_activity,
            arg=self.role.workspace_id,
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=RETRY_POLICIES["activity:fail_slow"],
        )
        if organization_id is None:
            self.logger.warning(
                "Auto-heal could not resolve organization_id from workspace",
                workspace_id=self.role.workspace_id,
            )
            return

        self.role = self.role.model_copy(update={"organization_id": organization_id})
        self.logger = self.logger.bind(role=self.role)
        ctx_role.set(self.role)
        self.logger.info(
            "Auto-healed role organization_id",
            organization_id=organization_id,
            workspace_id=self.role.workspace_id,
        )

    @workflow.run
    async def run(self, args: DSLRunArgs) -> StoredObject:
        await self._heal_role_organization_id_if_missing()

        # Set DSL and registry_lock
        registry_lock = None
        if args.dsl:
            # Use the provided DSL
            self.logger.debug("Using provided workflow definition")
            self.dsl = args.dsl
            # Use registry_lock from args if provided (e.g., from parent workflow)
            registry_lock = args.registry_lock
            self.dispatch_type = "push"
        else:
            # Otherwise, fetch the latest workflow definition
            self.logger.debug("Fetching latest workflow definition")
            try:
                result = await self._get_workflow_definition(args.wf_id)
                self.dsl = result.dsl
                registry_lock = result.registry_lock
            except TracecatException as e:
                self.logger.error("Failed to fetch workflow definition")
                raise ApplicationError(
                    "Failed to fetch workflow definition",
                    non_retryable=True,
                    type=e.__class__.__name__,
                ) from e
            self.dispatch_type = "pull"

        # Resolve registry lock if not provided or empty
        # This ensures all trigger paths (schedules, child workflows, API) have a valid lock
        if not registry_lock:
            self.logger.debug("Resolving registry lock via activity")
            action_names = {task.action for task in self.dsl.actions}
            try:
                self.registry_lock = await workflow.execute_activity(
                    resolve_registry_lock_activity,
                    arg=ResolveRegistryLockActivityInputs(
                        role=self.role,
                        action_names=action_names,
                    ),
                    start_to_close_timeout=self.start_to_close_timeout,
                    retry_policy=RETRY_POLICIES["activity:fail_slow"],
                )
            except ActivityError as e:
                match cause := e.cause:
                    case ApplicationError():
                        # Preserve structured application errors from the activity,
                        # including entitlement failures and non-retryable flags.
                        raise cause from e
                    case _:
                        raise ApplicationError(
                            f"Failed to resolve registry lock: {e}",
                            non_retryable=True,
                            type=e.__class__.__name__,
                        ) from cause
        else:
            self.registry_lock = registry_lock

        # Log registry lock for debugging
        self.logger.debug(
            "Workflow registry lock",
            registry_lock=self.registry_lock,
            dispatch_type=self.dispatch_type,
        )
        workflow.upsert_memo(
            {
                WF_EXECUTION_MEMO_REGISTRY_LOCK_KEY: self.registry_lock.model_dump(),
            }
        )

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
                    self.logger.error(
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

            trigger_type = get_trigger_type(workflow.info())
            try:
                err_run_args = await self._prepare_error_handler_workflow(
                    message=e.message,
                    handler_wf_id=handler_wf_id,
                    orig_wf_id=args.wf_id,
                    orig_wf_exec_id=self.wf_exec_id,
                    orig_dsl=self.dsl,
                    trigger_type=TriggerType(trigger_type),
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

    async def _run_workflow(self, args: DSLRunArgs) -> StoredObject:
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

            self.logger.debug(
                "Runtime config was set",
                args_config=args.runtime_config,
                dsl_config=self.dsl.config,
            )
            set_fields = args.runtime_config.model_dump(exclude_unset=True)
            self.runtime_config = self.dsl.config.model_copy(update=set_fields)
        else:
            # Otherwise default to the DSL config
            self.logger.debug(
                "Runtime config was not set, using DSL config",
                dsl_config=self.dsl.config,
            )
            self.runtime_config = self.dsl.config
        self.logger.debug("Runtime config after", runtime_config=self.runtime_config)

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
            trigger_inputs = (
                StoredObjectValidator.validate_python(args.trigger_inputs)
                if args.trigger_inputs is not None
                else None
            )
        # Validate and apply defaults from input schema to trigger inputs
        if input_schema := self.dsl.entrypoint.expects:
            try:
                trigger_inputs = await workflow.execute_activity(
                    DSLActivities.normalize_trigger_inputs_activity,
                    arg=NormalizeTriggerInputsActivityInputs(
                        input_schema=input_schema,
                        trigger_inputs=trigger_inputs,
                        key=trigger_key(str(self.workspace_id), self.wf_exec_id),
                    ),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )
            except ActivityError as e:
                match cause := e.cause:
                    case ApplicationError(type=t, details=details) if (
                        t == ValidationError.__name__
                    ):
                        self.logger.warning(
                            "Validation error when normalizing trigger inputs",
                            error=e,
                            details=details,
                        )
                        [val_detail] = details
                        validated = ValidationDetailListTA.validate_python(val_detail)
                        raise ApplicationError(
                            format_input_schema_validation_error(validated),
                            details,
                            non_retryable=True,
                            type=ValidationError.__name__,
                        ) from cause
                    case _:
                        self.logger.warning(
                            "Unexpected error cause when normalizing trigger inputs",
                            error=e,
                        )
                        raise ApplicationError(
                            "Failed to normalize trigger inputs",
                            non_retryable=True,
                            type=e.__class__.__name__,
                        ) from cause

        # Store workflow start time for computing elapsed time
        self.wf_start_time = wf_info.start_time

        # Resolve time anchor - recorded in history for replay/reset determinism
        if args.time_anchor is not None:
            # Use explicitly provided time anchor (e.g., from parent workflow or API override)
            self.time_anchor = args.time_anchor
        else:
            # Compute time anchor via local activity (recorded in history)
            self.time_anchor = await workflow.execute_local_activity(
                resolve_time_anchor_activity,
                arg=ResolveTimeAnchorActivityInputs(
                    trigger_type=get_trigger_type(wf_info),
                    start_time=wf_info.start_time,
                    scheduled_start_time=self._get_scheduled_start_time(wf_info),
                ),
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )

        # Prepare user facing context
        # trigger_inputs is already a StoredObject from args or normalize_trigger_inputs_activity
        # TRIGGER is always present - None signals no trigger inputs were provided
        self.context = ExecutionContext(
            ACTIONS={},
            TRIGGER=trigger_inputs,
            ENV=DSLEnvironment(
                workflow={
                    "start_time": wf_info.start_time,
                    "time_anchor": self.time_anchor,
                    "dispatch_type": self.dispatch_type,
                    "execution_id": self.wf_exec_id,
                    "run_id": self.wf_run_id,
                    "trigger_type": get_trigger_type(wf_info),
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
            wf_run_id=uuid.UUID(wf_info.run_id, version=4),
            environment=self.runtime_config.environment,
            logical_time=self.time_anchor,
        )
        ctx_run.set(self.run_context)

        self.dep_list = {task.ref: task.depends_on for task in self.dsl.actions}

        self.logger.info(
            "Running DSL task workflow",
            runtime_config=self.runtime_config,
            activity_timeout=self.start_to_close_timeout,
            execution_timeout=wf_info.execution_timeout,
        )

        self.scheduler = DSLScheduler(
            executor=self.execute_task,
            dsl=self.dsl,
            context=self.context,
            role=self.role,
            run_context=self.run_context,
            logger=self.logger.bind(unit="dsl-scheduler"),
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
                f"{'=' * 10} ({i + 1}/{n_exc}) {details.expr_context}.{ref} {'=' * 10}\n\n{info.exception!s}"
                for i, (ref, info) in enumerate(task_exceptions.items())
                if (details := info.details)
            )
            # NOTE: This error is shown in the final activity in the workflow history
            raise ApplicationError(
                f"Workflow failed with {n_exc} error(s)\n\n{formatted_exc}",
                # We should add the details of the exceptions to the error message because this will get captured
                # in the error handler workflow
                {ref: info.details for ref, info in task_exceptions.items()},
                non_retryable=True,
                type=ApplicationError.__name__,
            )

        try:
            self.logger.info("DSL workflow completed")
            return await self._handle_return()
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
            self.logger.debug("Creating wait until timer", wait_until=task.wait_until)

            # Parse the delay until date
            wait_until = await workflow.execute_activity(
                DSLActivities.parse_wait_until_activity,
                task.wait_until,
                start_to_close_timeout=timedelta(seconds=10),
            )
            self.logger.debug("Parsed wait until date", wait_until=wait_until)
            if wait_until is None:
                # Unreachable as this should have been validated at the API level
                raise ApplicationError(
                    "Invalid wait until date",
                    non_retryable=True,
                )

            current_time = datetime.now(UTC)
            self.logger.debug("Current time", current_time=current_time)
            wait_until_dt = datetime.fromisoformat(wait_until)
            if wait_until_dt > current_time:
                duration = wait_until_dt - current_time
                self.logger.debug(
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
            self.logger.debug("Starting action with delay", delay=task.start_delay)
            # In Temporal 1.9.0+, we can use workflow.sleep() as well
            await asyncio.sleep(task.start_delay)

    async def execute_task(self, task: ActionStatement) -> TaskResult:
        """Execute a task and manage the results."""
        if task.action == PlatformAction.TRANSFORM_GATHER:
            return await self._noop_gather_action(task)
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
            ctx["ACTIONS"][task.ref] = result
            self._set_logical_time_context()
            retry_until_result = await workflow.execute_activity(
                DSLActivities.evaluate_single_expression_activity,
                args=(retry_until.strip(), ctx),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
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

    @staticmethod
    def _unwrap_temporal_failure_cause(
        error: BaseException,
    ) -> tuple[BaseException, str]:
        """Return the deepest nested Temporal cause and best-effort message."""
        current = error
        seen: set[int] = set()
        # Termination argument:
        # - Each non-breaking iteration adds one new exception object id to `seen`.
        # - If a cause object repeats, we break on the `seen` check (cycle guard).
        # - If `cause` is missing or not an exception, we break.
        # Therefore this traversal cannot loop indefinitely.
        while True:
            current_id = id(current)
            if current_id in seen:
                break
            seen.add(current_id)

            nested = getattr(current, "cause", None)
            if not isinstance(nested, BaseException):
                break
            current = nested

        message = str(current)
        if message:
            return current, message
        outer_message = str(error)
        if outer_message:
            return current, outer_message
        return current, current.__class__.__name__

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
        stream_id = ctx_stream_id.get()
        self.logger.debug(
            "Begin task execution", task_ref=task.ref, stream_id=stream_id
        )
        task_result = TaskResult.from_result(None)

        try:
            # Handle timing control flow logic
            await self._handle_timers(task)

            # Do action stuff
            match task.action:
                case PlatformAction.CHILD_WORKFLOW_EXECUTE:
                    # NOTE: We don't support (nor recommend, unless a use case is justified) passing SECRETS to child workflows
                    # Single activity prepares everything: alias resolution, definition fetch, loop iteration data
                    self.logger.trace("Preparing child workflow")
                    use_committed = self.execution_type != ExecutionType.DRAFT
                    prepared = await workflow.execute_activity(
                        DSLActivities.prepare_subflow_activity,
                        arg=PrepareSubflowActivityInput(
                            role=self.role,
                            task=task,
                            operand=self.get_context(),
                            key=action_collection_prefix(
                                str(self.workspace_id),
                                self.wf_exec_id,
                                stream_id,
                                task.ref,
                            ),
                            use_committed=use_committed,
                        ),
                        start_to_close_timeout=timedelta(seconds=120),
                        retry_policy=RETRY_POLICIES["activity:fail_fast"],
                    )
                    self.logger.trace("Child workflow prepared", prepared=prepared)
                    # Execute child workflow (handles both single and looped)
                    stored_result = await self._execute_child_workflow_prepared(
                        task=task, prepared=prepared
                    )
                    # _execute_child_workflow returns StoredObject directly
                    # Infer result_typename from the stored data
                    match stored_result:
                        case InlineObject(data=data) as inline:
                            result_typename = inline.typename or type(data).__name__
                        case ExternalObject() as external:
                            result_typename = external.typename or "external"
                        case CollectionObject() as collection:
                            result_typename = collection.typename or "list"
                    task_result = TaskResult(
                        result=stored_result,
                        result_typename=result_typename,
                    )
                    self.logger.trace(
                        "Child workflow completed successfully",
                        stored_result=stored_result,
                    )
                    # action_result handled - skip with_result below
                    action_result = None
                case PlatformAction.AI_AGENT:
                    self.logger.debug("Executing agent", task=task)
                    agent_operand = self._build_action_context(task, stream_id)
                    self._set_logical_time_context()
                    action_args = await workflow.execute_activity(
                        DSLActivities.build_agent_args_activity,
                        arg=BuildAgentArgsActivityInput(
                            args=dict(task.args), operand=agent_operand
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=RETRY_POLICIES["activity:fail_fast"],
                    )
                    wf_info = workflow.info()
                    child_search_attributes = _build_agent_child_search_attributes(
                        wf_info, task.ref
                    )
                    session_id = workflow.uuid4()
                    arg = AgentWorkflowArgs(
                        role=self.role,
                        agent_args=RunAgentArgs(
                            user_prompt=action_args.user_prompt,
                            session_id=session_id,
                            config=AgentConfig(
                                model_name=action_args.model_name,
                                model_provider=action_args.model_provider,
                                instructions=action_args.instructions,
                                output_type=action_args.output_type,
                                model_settings=action_args.model_settings,
                                retries=action_args.retries,
                                base_url=action_args.base_url,
                                actions=action_args.actions,
                                tool_approvals=action_args.tool_approvals,
                            ),
                            max_requests=action_args.max_requests,
                            max_tool_calls=action_args.max_tool_calls,
                            use_workspace_credentials=action_args.use_workspace_credentials,
                        ),
                        title=self.dsl.title,
                        entity_type=AgentSessionEntity.WORKFLOW,
                        entity_id=self.run_context.wf_id,
                    )
                    action_result = await workflow.execute_child_workflow(
                        DurableAgentWorkflow.run,
                        arg=arg,
                        id=AgentWorkflowID(session_id),
                        retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                        # Route to agent worker queue for session activities
                        task_queue=config.TRACECAT__AGENT_QUEUE,
                        execution_timeout=wf_info.execution_timeout,
                        task_timeout=wf_info.task_timeout,
                        search_attributes=child_search_attributes,
                        memo=AgentActionMemo(
                            action_ref=task.ref,
                            action_title=task.title,
                            stream_id=stream_id or ROOT_STREAM,
                        ).model_dump(),
                    )
                case PlatformAction.AI_ACTION:
                    self.logger.debug("Executing AI action", task=task)
                    agent_operand = self._build_action_context(task, stream_id)
                    self._set_logical_time_context()
                    action_args = await workflow.execute_activity(
                        DSLActivities.build_agent_args_activity,
                        arg=BuildAgentArgsActivityInput(
                            args=dict(task.args), operand=agent_operand
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=RETRY_POLICIES["activity:fail_fast"],
                    )
                    wf_info = workflow.info()
                    child_search_attributes = _build_agent_child_search_attributes(
                        wf_info, task.ref
                    )
                    session_id = workflow.uuid4()
                    arg = AgentWorkflowArgs(
                        role=self.role,
                        agent_args=RunAgentArgs(
                            user_prompt=action_args.user_prompt,
                            session_id=session_id,
                            config=AgentConfig(
                                model_name=action_args.model_name,
                                model_provider=action_args.model_provider,
                                instructions=action_args.instructions,
                                output_type=action_args.output_type,
                                model_settings=action_args.model_settings,
                                retries=action_args.retries,
                                base_url=action_args.base_url,
                                # AI action has no tools
                                actions=None,
                                tool_approvals=None,
                            ),
                            max_requests=action_args.max_requests,
                            # No tool calls for AI action
                            max_tool_calls=0,
                            use_workspace_credentials=action_args.use_workspace_credentials,
                        ),
                        title=self.dsl.title,
                        entity_type=AgentSessionEntity.WORKFLOW,
                        entity_id=self.run_context.wf_id,
                    )
                    action_result = await workflow.execute_child_workflow(
                        DurableAgentWorkflow.run,
                        arg=arg,
                        id=AgentWorkflowID(session_id),
                        retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                        # Route to agent worker queue for session activities
                        task_queue=config.TRACECAT__AGENT_QUEUE,
                        execution_timeout=wf_info.execution_timeout,
                        task_timeout=wf_info.task_timeout,
                        search_attributes=child_search_attributes,
                        memo=AgentActionMemo(
                            action_ref=task.ref,
                            action_title=task.title,
                            stream_id=stream_id or ROOT_STREAM,
                        ).model_dump(),
                    )
                case PlatformAction.AI_PRESET_AGENT:
                    self.logger.debug("Executing preset agent", task=task)
                    agent_operand = self._build_action_context(task, stream_id)
                    self._set_logical_time_context()
                    preset_action_args = await workflow.execute_activity(
                        DSLActivities.build_preset_agent_args_activity,
                        arg=BuildPresetAgentArgsActivityInput(
                            args=dict(task.args), operand=agent_operand
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=RETRY_POLICIES["activity:fail_fast"],
                    )

                    # Create override config with placeholder model/provider
                    # These will be ignored by DurableAgentWorkflow when preset_slug is present
                    # but are required by AgentConfig schema.
                    override_config = None
                    if preset_action_args.actions or preset_action_args.instructions:
                        override_config = AgentConfig(
                            model_name="preset-override",
                            model_provider="preset-override",
                            actions=preset_action_args.actions,
                            instructions=preset_action_args.instructions,
                        )

                    wf_info = workflow.info()
                    child_search_attributes = _build_agent_child_search_attributes(
                        wf_info, task.ref
                    )
                    session_id = workflow.uuid4()
                    arg = AgentWorkflowArgs(
                        role=self.role,
                        agent_args=RunAgentArgs(
                            user_prompt=preset_action_args.user_prompt,
                            session_id=session_id,
                            preset_slug=preset_action_args.preset,
                            config=override_config,
                            max_requests=preset_action_args.max_requests,
                            max_tool_calls=preset_action_args.max_tool_calls,
                            use_workspace_credentials=preset_action_args.use_workspace_credentials,
                        ),
                        title=self.dsl.title,
                        entity_type=AgentSessionEntity.WORKFLOW,
                        entity_id=self.run_context.wf_id,
                    )
                    action_result = await workflow.execute_child_workflow(
                        DurableAgentWorkflow.run,
                        arg=arg,
                        id=AgentWorkflowID(session_id),
                        retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                        # Route to agent worker queue for session activities
                        task_queue=config.TRACECAT__AGENT_QUEUE,
                        execution_timeout=wf_info.execution_timeout,
                        task_timeout=wf_info.task_timeout,
                        search_attributes=child_search_attributes,
                        memo=AgentActionMemo(
                            action_ref=task.ref,
                            action_title=task.title,
                            stream_id=stream_id or ROOT_STREAM,
                        ).model_dump(),
                    )
                case _:
                    # Below this point, we're executing the task
                    self.logger.trace(
                        "Running action",
                        task_ref=task.ref,
                        runtime_config=self.runtime_config,
                    )
                    stored_result = await self._run_action(task)
                    # _run_action returns StoredObject directly
                    # Infer result_typename from the stored data
                    match stored_result:
                        case InlineObject(data=data) as inline:
                            result_typename = inline.typename or type(data).__name__
                        case ExternalObject() as external:
                            result_typename = external.typename or "external"
                        case CollectionObject() as collection:
                            result_typename = collection.typename or "list"
                    task_result = TaskResult(
                        result=stored_result,
                        result_typename=result_typename,
                    )
                    self.logger.trace(
                        "Action completed successfully", stored_result=stored_result
                    )
                    # action_result handled - skip with_result below
                    action_result = None
            if action_result is not None:
                self.logger.trace(
                    "Action completed successfully", action_result=action_result
                )
                task_result = task_result.with_result(action_result)
        # NOTE: By the time we receive an exception, we've exhausted all retry attempts
        # Note that execute_task is called by the scheduler, so we don't have to return ApplicationError
        except (ActivityError, ChildWorkflowError, FailureError) as e:
            # These are deterministic and expected errors that
            err_type = e.__class__.__name__
            msg = self.ERROR_TYPE_TO_MESSAGE[err_type]
            cause = e.cause
            root_error, root_message = self._unwrap_temporal_failure_cause(
                cause if isinstance(cause, BaseException) else e
            )
            self.logger.warning(
                msg,
                role=self.role,
                e=e,
                cause=cause,
                root_cause=root_error,
                root_message=root_message,
                type=err_type,
            )
            match cause:
                case ApplicationError(details=details) if details:
                    err_info = details[0]
                    err_type = cause.type or err_type
                    task_result = task_result.with_error(err_info, err_type)
                    # Reraise the cause, as it's wrapped by the ApplicationError
                    raise cause from e
                case ApplicationError() as app_err:
                    err_type = app_err.type or err_type
                    err_message = app_err.message or root_message
                    task_result = task_result.with_error(err_message, err_type)
                    raise app_err from e
                case _:
                    resolved_type = root_error.__class__.__name__
                    self.logger.warning(
                        "Unexpected error cause",
                        cause=cause,
                        root_cause=root_error,
                        root_message=root_message,
                    )
                    task_result = task_result.with_error(root_message, resolved_type)
                    raise ApplicationError(
                        root_message, non_retryable=True, type=resolved_type
                    ) from e

        except TracecatExpressionError as e:
            err_type = e.__class__.__name__
            detail = e.detail or "Error occurred when handling an expression"
            raise ApplicationError(detail, non_retryable=True, type=err_type) from e

        except ValidationError as e:
            self.logger.warning("Runtime validation error", error=e.errors())
            task_result = task_result.with_error(e.errors(), ValidationError.__name__)
            raise e
        except Exception as e:
            err_type = e.__class__.__name__
            msg = f"Task execution failed with unexpected error: {e}"
            self.logger.error(
                "Activity execution failed with unexpected error",
                error=msg,
                type=err_type,
            )
            task_result = task_result.with_error(msg, err_type)
            raise ApplicationError(msg, non_retryable=True, type=err_type) from e
        finally:
            self.logger.trace("Setting action result", task_result=task_result)
            context = self.get_context(stream_id)
            context["ACTIONS"][task.ref] = task_result
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
        sf_context: SubflowContext,
    ) -> StoredObject:
        """Execute a child workflow (single or looped).

        For single execution: evaluates args and builds DSLRunArgs directly.
        For loops: delegates to _execute_child_workflow_loop for batched resolution.
        """
        self.logger.debug("Execute child workflow", subflow_context=sf_context)
        if task.for_each:
            return await self._execute_child_workflow_loop(
                task=task, sf_context=sf_context
            )
        else:
            # Single execution: evaluate args and build DSLRunArgs
            stream_id = ctx_stream_id.get()
            key = action_key(
                str(self.workspace_id), self.wf_exec_id, stream_id, task.ref
            )
            evaluated_args = await workflow.execute_activity(
                DSLActivities.evaluate_templated_object_activity,
                arg=EvaluateTemplatedObjectActivityInput(
                    obj=dict(task.args),
                    operand=self.get_context(),
                    key=key,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )

            # Evaluate task.environment if present (takes precedence over args.environment)
            task_environment = None
            if task.environment:
                task_environment = await workflow.execute_activity(
                    DSLActivities.evaluate_single_expression_activity,
                    args=[task.environment, self.get_context()],
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )

            # Get args.environment from the stored evaluated args
            # Note: evaluated_args is a StoredObject, we need to get the raw value
            # For now, we evaluate environment/timeout separately since they're DSL config
            args_environment = task.args.get("environment")
            args_timeout = task.args.get("timeout")

            # Evaluate environment/timeout from args if they contain expressions
            if args_environment and isinstance(args_environment, str):
                args_environment = await workflow.execute_activity(
                    DSLActivities.evaluate_single_expression_activity,
                    args=[args_environment, self.get_context()],
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )

            if args_timeout and isinstance(args_timeout, str):
                args_timeout = await workflow.execute_activity(
                    DSLActivities.evaluate_single_expression_activity,
                    args=[str(args_timeout), self.get_context()],
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )

            # Environment precedence: task.environment > args.environment > dsl.config
            resolved_environment = (
                task_environment
                or args_environment
                or sf_context.dsl.config.environment
            )
            resolved_timeout = args_timeout or sf_context.dsl.config.timeout

            self.logger.trace(
                "Executing child workflow",
                subflow_context=sf_context,
                task_environment=task_environment,
                args_environment=args_environment,
                resolved_environment=resolved_environment,
            )

            runtime_config = DSLConfig(
                environment=resolved_environment,
                timeout=resolved_timeout,
            )

            sf_run_args = DSLRunArgs(
                role=self.role,
                dsl=sf_context.dsl,
                wf_id=sf_context.wf_id,
                trigger_inputs=evaluated_args,
                parent_run_context=sf_context.run_context,
                runtime_config=runtime_config,
                execution_type=sf_context.execution_type,
                time_anchor=sf_context.time_anchor,
                registry_lock=sf_context.registry_lock,
            )

            return await self._run_child_workflow(task, sf_run_args)

    async def _execute_child_workflow_prepared(
        self,
        task: ActionStatement,
        prepared: PreparedSubflowResult,
    ) -> StoredObject:
        """Execute a child workflow using PreparedSubflowResult.

        For single execution: evaluates trigger_inputs and spawns one child.
        For loops: iterates over prepared.trigger_inputs CollectionObject,
        passing collection + index to each child.
        """
        self.logger.debug("Execute child workflow (prepared)", prepared=prepared)

        # Compute time_anchor for child workflows
        child_time_anchor = self._compute_logical_time()

        if prepared.trigger_inputs is None:
            # Single execution: evaluate trigger_inputs separately
            stream_id = ctx_stream_id.get()
            key = action_key(
                str(self.workspace_id), self.wf_exec_id, stream_id, task.ref
            )
            trigger_inputs = await workflow.execute_activity(
                DSLActivities.evaluate_templated_object_activity,
                arg=EvaluateTemplatedObjectActivityInput(
                    obj=task.args.get("trigger_inputs"),
                    operand=self.get_context(),
                    key=key,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )

            runtime_config = prepared.get_config(0)
            sf_run_args = DSLRunArgs(
                role=self.role,
                dsl=prepared.dsl,
                wf_id=prepared.wf_id,
                trigger_inputs=trigger_inputs,
                parent_run_context=self.run_context,
                runtime_config=runtime_config,
                execution_type=self.execution_type,
                time_anchor=child_time_anchor,
                registry_lock=prepared.registry_lock,
            )

            return await self._run_child_workflow(task, sf_run_args)
        else:
            # Looped execution: iterate over prepared.trigger_inputs
            return await self._execute_child_workflow_loop_prepared(
                task=task, prepared=prepared, child_time_anchor=child_time_anchor
            )

    async def _execute_child_workflow_loop_prepared(
        self,
        task: ActionStatement,
        prepared: PreparedSubflowResult,
        child_time_anchor: datetime,
    ) -> StoredObject:
        """Execute child workflow loop using PreparedSubflowResult.

        Iterates over prepared.trigger_inputs (CollectionObject), passing
        the collection reference + index to each child workflow.
        """
        loop_strategy = LoopStrategy(task.args.get("loop_strategy", LoopStrategy.BATCH))
        fail_strategy = FailStrategy(
            task.args.get("fail_strategy", FailStrategy.ISOLATED)
        )
        total_count = prepared.count

        self.logger.trace(
            "Executing child workflow loop (prepared)",
            total_count=total_count,
            loop_strategy=loop_strategy,
            fail_strategy=fail_strategy,
        )

        # Determine batch size based on strategy
        batch_size = {
            LoopStrategy.SEQUENTIAL: 1,
            LoopStrategy.BATCH: int(task.args.get("batch_size", 32)),
            LoopStrategy.PARALLEL: total_count,
        }[loop_strategy]

        # Process in batches for concurrency control
        all_results: list[StoredObject] = []
        batch_start = 0

        while batch_start < total_count:
            current_batch_size = min(batch_size, total_count - batch_start)
            batch_results = await self._execute_child_workflow_batch_prepared(
                task=task,
                prepared=prepared,
                batch_start=batch_start,
                batch_size=current_batch_size,
                fail_strategy=fail_strategy,
                child_time_anchor=child_time_anchor,
            )
            all_results.extend(batch_results)
            batch_start += current_batch_size

        # Synchronize by converting Sequence[StoredObject] -> CollectionObject
        stream_id = ctx_stream_id.get()
        collection = await workflow.execute_activity(
            DSLActivities.synchronize_collection_object_activity,
            SynchronizeCollectionObjectActivityInput(
                collection=all_results,
                key=action_collection_prefix(
                    str(self.workspace_id), self.wf_exec_id, stream_id, task.ref
                ),
            ),
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )
        return collection

    async def _execute_child_workflow_batch_prepared(
        self,
        task: ActionStatement,
        prepared: PreparedSubflowResult,
        batch_start: int,
        batch_size: int,
        fail_strategy: FailStrategy,
        child_time_anchor: datetime,
    ) -> list[StoredObject]:
        """Execute a batch of child workflows with prepared data.

        Passes CollectionObject + index to each child, allowing it to
        retrieve its specific trigger_inputs.
        """

        def iter_run_args() -> Iterator[tuple[int, DSLRunArgs]]:
            for i in range(batch_size):
                loop_index = batch_start + i
                config = prepared.get_config(loop_index)

                # Get trigger_inputs for this specific iteration
                # Works with both CollectionObject and InlineObject
                trigger_inputs = prepared.get_trigger_input_at(loop_index)

                yield (
                    loop_index,
                    DSLRunArgs(
                        role=self.role,
                        dsl=prepared.dsl,
                        wf_id=prepared.wf_id,
                        trigger_inputs=trigger_inputs,
                        parent_run_context=self.run_context,
                        runtime_config=config,
                        execution_type=self.execution_type,
                        time_anchor=child_time_anchor,
                        registry_lock=prepared.registry_lock,
                    ),
                )

        coros: list[Awaitable[StoredObject]] = []
        async for loop_index, run_args in cooperative(
            iter_run_args(),
            delay=0.1,
        ):
            self.logger.trace(
                "Run child workflow batch (prepared)",
                loop_index=loop_index,
                fail_strategy=fail_strategy,
            )
            coro = self._run_child_workflow(task, run_args, loop_index=loop_index)
            coros.append(coro)

        gather_result = await asyncio.gather(*coros, return_exceptions=True)

        if fail_strategy == FailStrategy.ALL:
            if any(isinstance(val, BaseException) for val in gather_result):
                raise RuntimeError("One or more child workflows failed")

        result: list[StoredObject] = []
        for val in gather_result:
            match val:
                case BaseException():
                    result.append(
                        InlineObject(data=dsl_execution_error_from_exception(val))
                    )
                case _:
                    result.append(StoredObjectValidator.validate_python(val))
        return result

    async def _execute_child_workflow_loop(
        self,
        task: ActionStatement,
        sf_context: SubflowContext,
    ) -> StoredObject:
        """Execute child workflow in a loop with per-batch resolution.

        Uses resolve_subflow_batch_activity to evaluate args (including
        environment/timeout overrides) per iteration with var context.
        """
        if not task.for_each:
            raise ApplicationError(
                "for_each expression is required for looped subflows",
                non_retryable=True,
            )
        loop_strategy = LoopStrategy(task.args.get("loop_strategy", LoopStrategy.BATCH))
        fail_strategy = FailStrategy(
            task.args.get("fail_strategy", FailStrategy.ISOLATED)
        )
        self.logger.trace(
            "Executing child workflow in loop",
            sf_context=sf_context,
            loop_strategy=loop_strategy,
            fail_strategy=fail_strategy,
        )

        # First, get total count by evaluating for_each expression
        # We need this to know how many iterations to process
        total_count = await workflow.execute_activity(
            DSLActivities.handle_looped_subflow_input_activity,
            arg=EvaluateLoopedSubflowInputActivityInput(
                for_each=task.for_each,
                operand=self.get_context(),
            ),
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )

        # Determine batch size based on strategy
        batch_size = {
            LoopStrategy.SEQUENTIAL: 1,
            LoopStrategy.BATCH: sf_context.batch_size,
            LoopStrategy.PARALLEL: total_count,  # All at once
        }[loop_strategy]

        self.logger.trace(
            "Loop execution plan",
            total_count=total_count,
            batch_size=batch_size,
            loop_strategy=loop_strategy,
        )

        # Process in batches
        all_results: list[StoredObject] = []
        batch_start = 0
        stream_id = ctx_stream_id.get()
        key_prefix = action_collection_prefix(
            str(self.workspace_id), self.wf_exec_id, stream_id, task.ref
        )

        while batch_start < total_count:
            current_batch_size = min(batch_size, total_count - batch_start)

            # Resolve args for this batch using the new activity
            resolved_batch = await workflow.execute_activity(
                DSLActivities.resolve_subflow_batch_activity,
                arg=ResolveSubflowBatchActivityInput(
                    task=task,
                    operand=self.get_context(),
                    batch_start=batch_start,
                    batch_size=current_batch_size,
                    key=f"{key_prefix}/batch_{batch_start}.json",
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )

            # Execute this batch
            batch_results = await self._execute_child_workflow_batch(
                resolved_batch=resolved_batch,
                task=task,
                sf_context=sf_context,
                batch_start=batch_start,
                fail_strategy=fail_strategy,
            )
            all_results.extend(batch_results)

            batch_start += current_batch_size

        # Synchronize by converting Sequence[StoredObject] -> CollectionObject
        collection = await workflow.execute_activity(
            DSLActivities.synchronize_collection_object_activity,
            SynchronizeCollectionObjectActivityInput(
                collection=all_results,
                key=key_prefix,
            ),
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )
        return collection

    async def _execute_child_workflow_batch(
        self,
        resolved_batch: ResolvedSubflowBatch,
        task: ActionStatement,
        sf_context: SubflowContext,
        batch_start: int,
        *,
        fail_strategy: FailStrategy = FailStrategy.ISOLATED,
    ) -> list[StoredObject]:
        """Execute a batch of child workflows with resolved args.

        Args:
            resolved_batch: Pre-resolved configs and trigger_inputs for this batch
            task: The ActionStatement being executed
            sf_context: Shared subflow context (workflow definition, registry lock, etc.)
            batch_start: Global index offset for this batch (for loop_index calculation)
            fail_strategy: How to handle failures in the batch
        """
        batch_count = resolved_batch.count

        def iter_run_args() -> Generator[tuple[int, DSLRunArgs]]:
            for i in range(batch_count):
                config = resolved_batch.get_config(i)

                # Build runtime_config with per-iteration overrides
                # Precedence: resolved config > dsl.config default
                runtime_config = DSLConfig(
                    environment=config.environment or sf_context.dsl.config.environment,
                    timeout=config.timeout or sf_context.dsl.config.timeout,
                )

                # Each iteration gets its own trigger_inputs StoredObject
                yield (
                    batch_start + i,  # Global loop index
                    DSLRunArgs(
                        role=self.role,
                        dsl=sf_context.dsl,
                        wf_id=sf_context.wf_id,
                        trigger_inputs=resolved_batch.trigger_inputs[i],
                        parent_run_context=sf_context.run_context,
                        runtime_config=runtime_config,
                        execution_type=sf_context.execution_type,
                        time_anchor=sf_context.time_anchor,
                        registry_lock=sf_context.registry_lock,
                    ),
                )

        coros: list[Awaitable[StoredObject]] = []
        async for loop_index, run_args in cooperative(
            iter_run_args(),
            delay=0.1,
        ):
            self.logger.trace(
                "Run child workflow batch",
                loop_index=loop_index,
                fail_strategy=fail_strategy,
                environment=run_args.runtime_config.environment,
            )
            coro = self._run_child_workflow(task, run_args, loop_index=loop_index)
            coros.append(coro)

        gather_result = await asyncio.gather(*coros, return_exceptions=True)

        if fail_strategy == FailStrategy.ALL:
            if any(isinstance(val, BaseException) for val in gather_result):
                raise RuntimeError("One or more child workflows failed")

        result: list[StoredObject] = []
        for val in gather_result:
            match val:
                case BaseException():
                    result.append(
                        InlineObject(data=dsl_execution_error_from_exception(val))
                    )
                case _:
                    result.append(StoredObjectValidator.validate_python(val))
        return result

    async def _handle_return(self) -> StoredObject:
        self.logger.debug("Handling return", context=self.context)
        if self.dsl.returns is None:
            match config.TRACECAT__WORKFLOW_RETURN_STRATEGY:
                case "context":
                    # NOTE: This is used only during testing so we always return it inline
                    self.logger.trace("Returning DSL context")
                    self.context.pop("ENV", None)
                    return InlineObject(data=self.context)
                case "minimal":
                    return InlineObject(data=self.run_context)
        # Return some custom value that should be evaluated
        self.logger.trace("Returning value from expression")
        self._set_logical_time_context()
        key = return_key(str(self.workspace_id), self.wf_exec_id)
        return await workflow.execute_activity(
            DSLActivities.resolve_return_expression_activity,
            arg=EvaluateTemplatedObjectActivityInput(
                obj=self.dsl.returns, operand=self.context, key=key
            ),
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )

    async def _resolve_workflow_alias(self, wf_alias: str) -> identifiers.WorkflowID:
        # Evaluate the workflow alias as a templated expression
        # For draft executions, use draft aliases; for published executions, use committed aliases
        activity_inputs = ResolveWorkflowAliasActivityInputs(
            workflow_alias=wf_alias,
            role=self.role,
            use_committed=self.execution_type == ExecutionType.PUBLISHED,
        )
        wf_id = await workflow.execute_activity(
            WorkflowsManagementService.resolve_workflow_alias_activity,
            args=(self.run_context, self.get_context(), activity_inputs),
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )
        if not wf_id:
            raise ValueError(f"Workflow alias {wf_alias!r} not found")
        return wf_id

    async def _get_workflow_definition(
        self, workflow_id: identifiers.WorkflowID, version: int | None = None
    ) -> WorkflowDefinitionActivityResult:
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
            retry_policy=RETRY_POLICIES["activity:fail_slow"],
        )

    async def _get_schedule_trigger_inputs(
        self, schedule_id: identifiers.ScheduleUUID, worflow_id: identifiers.WorkflowID
    ) -> StoredObject | None:
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
        result = await workflow.execute_activity(
            WorkflowSchedulesService.get_schedule_trigger_inputs_activity,
            arg=activity_inputs,
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )
        return StoredObjectValidator.validate_python(result) if result else None

    def _get_scheduled_start_time(self, wf_info: workflow.Info) -> datetime | None:
        """Extract TemporalScheduledStartTime from search attributes if available.

        This is the intended schedule time for scheduled workflows, which may differ
        from the actual start time if the worker was delayed.
        """
        from temporalio.common import SearchAttributeKey

        try:
            key = SearchAttributeKey.for_datetime("TemporalScheduledStartTime")
            return wf_info.typed_search_attributes.get(key)
        except Exception:
            return None

    async def _prepare_child_workflow(self, args: ExecuteSubflowArgs) -> SubflowContext:
        """Grab a workflow definition and create shared child workflow context.

        Returns SubflowContext with shared data (workflow definition, registry lock, etc.).
        Per-iteration config (environment, timeout) is resolved separately via
        ResolvedSubflowBatch for loops, or directly from args for single execution.
        """
        if args.workflow_id:
            child_wf_id = args.workflow_id
        elif args.workflow_alias:
            child_wf_id = await self._resolve_workflow_alias(args.workflow_alias)
        else:
            raise ValueError("Either workflow_id or workflow_alias must be provided")

        result = await self._get_workflow_definition(child_wf_id, version=args.version)
        dsl = result.dsl

        self.logger.debug(
            "Got workflow definition",
            dsl=dsl,
            args=args,
            dsl_config=dsl.config,
            self_config=self.runtime_config,
        )

        # Propagate time_anchor: use child's override if set, otherwise use parent's
        # current logical time so child continues from parent's elapsed position
        child_time_anchor = (
            args.time_anchor
            if args.time_anchor is not None
            else self._compute_logical_time()
        )

        return SubflowContext(
            wf_id=child_wf_id,
            dsl=dsl,
            registry_lock=result.registry_lock,
            run_context=self.run_context,
            execution_type=self.execution_type,
            time_anchor=child_time_anchor,
            batch_size=args.batch_size,
        )

    async def _noop_gather_action(self, task: ActionStatement) -> Any:
        # Parent stream
        stream_id = ctx_stream_id.get()
        self.logger.debug(
            "Noop gather action", action_ref=task.ref, stream_id=stream_id
        )
        new_context = self._build_action_context(task, stream_id)

        arg = RunActionInput(
            task=task,
            run_context=self.run_context,
            exec_context=new_context,
            interaction_context=ctx_interaction.get(),
            stream_id=stream_id,
            registry_lock=self.registry_lock,
        )

        return await workflow.execute_activity(
            DSLActivities.noop_gather_action_activity,
            args=(arg, self.role),
            start_to_close_timeout=timedelta(
                seconds=task.start_delay + task.retry_policy.timeout
            ),
            retry_policy=RetryPolicy(
                maximum_attempts=task.retry_policy.max_attempts,
            ),
        )

    def _build_action_context(
        self, task: ActionStatement, stream_id: StreamID
    ) -> ExecutionContext:
        """Construct the execution context for an action with resolved dependencies."""
        return self.scheduler.build_stream_aware_context(task, stream_id)

    def _compute_logical_time(self) -> datetime:
        """Compute the current logical time = time_anchor + elapsed workflow time.

        This provides deterministic time during workflow replay since workflow.now()
        is recorded in history and replayed identically.
        """
        elapsed = workflow.now() - self.wf_start_time
        return self.time_anchor + elapsed

    def _set_logical_time_context(self) -> None:
        """Set ctx_logical_time for deterministic FN.now() in template evaluations."""
        ctx_logical_time.set(self._compute_logical_time())

    async def _run_action(self, task: ActionStatement) -> StoredObject:
        # XXX(perf): We shouldn't pass the full execution context to the activity
        # We should only keep the contexts that are needed for the action
        stream_id = ctx_stream_id.get()
        new_context = self._build_action_context(task, stream_id)

        # Inject current logical_time into the workflow context for FN.now() etc.
        if env_context := new_context.get("ENV"):
            if workflow_ctx := env_context.get("workflow"):
                workflow_ctx["logical_time"] = self._compute_logical_time()

        # Check if action has environment override
        run_context = self.run_context
        if task.environment is not None:
            environment = task.environment.strip()
            # If it's an expr
            if is_template_only(environment):
                # Evaluate the environment expression
                self._set_logical_time_context()
                environment = await workflow.execute_activity(
                    DSLActivities.evaluate_single_expression_activity,
                    args=(task.environment, new_context),
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )
            # Create a new run context with the overridden environment
            run_context = self.run_context.model_copy(
                update={"environment": environment}
            )

        # Tells us where to get the redis stream
        session_id = (
            workflow.uuid4() if PlatformAction.is_streamable(task.action) else None
        )

        arg = RunActionInput(
            task=task,
            run_context=run_context,
            exec_context=new_context,
            interaction_context=ctx_interaction.get(),
            stream_id=stream_id,
            session_id=session_id,
            registry_lock=self.registry_lock,
        )

        # Dispatch to ExecutorWorker on shared-action-queue
        # Using string activity name since it's registered on a different worker
        stored = await workflow.execute_activity(
            "execute_action_activity",
            args=(arg, self.role),
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            start_to_close_timeout=timedelta(
                seconds=task.start_delay + task.retry_policy.timeout
            ),
            retry_policy=RetryPolicy(
                maximum_attempts=task.retry_policy.max_attempts,
            ),
        )
        return StoredObjectValidator.validate_python(stored)

    async def _run_child_workflow(
        self, task: ActionStatement, run_args: DSLRunArgs, loop_index: int | None = None
    ) -> StoredObject:
        """Each run subflow call needs to know the object ref location of the trigger inputs.

        It should either receive as trigger inputs:
        - CollectionObject + loop_index
        - Single InlineObject / ExternalObject

        """
        wf_exec_id = identifiers.workflow.generate_exec_id(run_args.wf_id)
        wf_info = workflow.info()
        # XXX(safety): This has been validated in prepare_child_workflow
        args = ResolvedSubflowInput.model_construct(**task.args)
        # Use Temporal memo to store the action ref in the child workflow run
        stream_id = ctx_stream_id.get()
        memo = ChildWorkflowMemo(
            action_ref=task.ref,
            loop_index=loop_index,
            wait_strategy=args.wait_strategy,
            stream_id=stream_id,
        ).model_dump()
        self.logger.debug(
            "Running child workflow",
            wait_strategy=args.wait_strategy,
            memo=memo,
        )

        match args.wait_strategy:
            case WaitStrategy.DETACH:
                child_wf_handle = await workflow.start_child_workflow(
                    DSLWorkflow.run,
                    run_args,
                    id=wf_exec_id,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
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
                # Wrap workflow ID in InlineObject for uniform envelope
                return InlineObject(data=child_wf_handle.id)
            case _:
                # WAIT and all other strategies
                # execute_child_workflow returns StoredObject (from DSLWorkflow.run)
                result = await workflow.execute_child_workflow(
                    DSLWorkflow.run,
                    run_args,
                    id=wf_exec_id,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    # Propagate the parent workflow attributes to the child workflow
                    task_queue=wf_info.task_queue,
                    execution_timeout=wf_info.execution_timeout,
                    task_timeout=wf_info.task_timeout,
                    memo=memo,
                    search_attributes=wf_info.typed_search_attributes,
                )
                return StoredObjectValidator.validate_python(result)

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
            start_to_close_timeout=self.start_to_close_timeout,
            retry_policy=RETRY_POLICIES["activity:fail_slow"],
        )

    async def _prepare_error_handler_workflow(
        self,
        *,
        message: str,
        handler_wf_id: WorkflowID,
        orig_wf_id: WorkflowID,
        orig_wf_exec_id: WorkflowExecutionID,
        orig_dsl: DSLInput,
        trigger_type: TriggerType,
        errors: list[ActionErrorInfo] | None = None,
    ) -> DSLRunArgs:
        """Grab a workflow definition and create error handler workflow run args"""

        result = await self._get_workflow_definition(handler_wf_id)
        dsl = result.dsl

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
                self.logger.warning(
                    "Workspace ID is required to create error handler URL"
                )
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
                    self.logger.error("Error parsing workflow execution ID", error=e)

        return DSLRunArgs(
            role=self.role,
            dsl=dsl,
            wf_id=handler_wf_id,
            parent_run_context=ctx_run.get(),
            trigger_inputs=InlineObject(
                data=ErrorHandlerWorkflowInput(
                    message=message,
                    handler_wf_id=handler_wf_id,
                    orig_wf_id=orig_wf_id,
                    orig_wf_exec_id=orig_wf_exec_id,
                    orig_wf_exec_url=url,
                    orig_wf_title=orig_dsl.title,
                    errors=errors,
                    trigger_type=trigger_type,
                )
            ),
            runtime_config=runtime_config,
            execution_type=self.execution_type,
            # Use error handler's own registry_lock from its definition, not parent's
            registry_lock=result.registry_lock,
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
        memo = ChildWorkflowMemo(action_ref=identifiers.action.ref(args.dsl.title))
        await workflow.execute_child_workflow(
            DSLWorkflow.run,
            args,
            id=wf_exec_id,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            # Propagate the parent workflow attributes to the child workflow
            task_queue=wf_info.task_queue,
            execution_timeout=wf_info.execution_timeout,
            task_timeout=wf_info.task_timeout,
            memo=memo.model_dump(),
            search_attributes=wf_info.typed_search_attributes,
        )
