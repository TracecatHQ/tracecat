from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError

from tracecat.auth.types import Role
from tracecat.dsl.action import ScatterActionInput

with workflow.unsafe.imports_passed_through():
    from pydantic_core import to_json
    from temporalio.exceptions import ApplicationError

    from tracecat.concurrency import cooperative
    from tracecat.contexts import ctx_stream_id
    from tracecat.dsl.action import (
        DSLActivities,
        EvaluateTemplatedObjectActivityInput,
        FinalizeGatherActivityInput,
    )
    from tracecat.dsl.common import (
        RETRY_POLICIES,
        AdjDst,
        DSLInput,
        edge_components_from_dep,
    )
    from tracecat.dsl.constants import MAX_DO_WHILE_ITERATIONS
    from tracecat.dsl.enums import (
        EdgeMarker,
        EdgeType,
        JoinStrategy,
        PlatformAction,
        Sentinel,
        SkipStrategy,
        StreamErrorHandlingStrategy,
    )
    from tracecat.dsl.schemas import (
        ROOT_STREAM,
        ActionStatement,
        ExecutionContext,
        GatherArgs,
        LoopEndArgs,
        RunContext,
        ScatterArgs,
        StreamID,
        TaskResult,
    )
    from tracecat.dsl.types import (
        ActionErrorInfo,
        ActionErrorInfoAdapter,
        Task,
        TaskExceptionInfo,
    )
    from tracecat.dsl.workflow_logging import WorkflowRuntimeLogger, workflow_logger
    from tracecat.exceptions import TaskUnreachable
    from tracecat.expressions.common import ExprContext
    from tracecat.expressions.core import extract_expressions
    from tracecat.storage.object import (
        CollectionObject,
        InlineObject,
        StoredObject,
        StoredObjectValidator,
        action_collection_prefix,
        action_key,
    )


def _get_collection_size(stored: StoredObject) -> int:
    """Get the size of a stored collection.

    Works with both CollectionObject (externalized) and InlineObject (inline list).
    """
    match stored:
        case CollectionObject() as col:
            return col.count
        case InlineObject(data=data) if isinstance(data, list):
            return len(data)
        case _:
            raise TypeError(
                f"Expected CollectionObject or InlineObject with list data, "
                f"got {type(stored).__name__}"
            )


@dataclass(frozen=True, slots=True)
class DSLEdge:
    src: str
    """The source task reference"""
    dst: str
    """The destination task reference"""
    type: EdgeType
    """The edge type"""
    stream_id: StreamID
    """The stream ID of the edge"""

    def __repr__(self) -> str:
        return f"{self.src}-[{self.type.value}]->{self.dst} ({self.stream_id})"


@dataclass(frozen=True, slots=True)
class LoopRegion:
    """Resolved metadata for one do-while loop region."""

    start_ref: str
    end_ref: str
    scope_ref: str
    members: frozenset[str]


class DSLScheduler:
    """Manage only scheduling and control flow of tasks in a topological-like order."""

    def __init__(
        self,
        *,
        executor: Callable[[ActionStatement], Awaitable[Any]],
        dsl: DSLInput,
        max_pending_tasks: int,
        skip_strategy: SkipStrategy = SkipStrategy.PROPAGATE,
        context: ExecutionContext,
        role: Role,
        run_context: RunContext,
        logger: WorkflowRuntimeLogger | None = None,
    ):
        # Static
        self.dsl = dsl
        self.executor = executor
        if max_pending_tasks < 1:
            raise ValueError("max_pending_tasks must be greater than 0")
        self.max_pending_tasks = max_pending_tasks
        self.skip_strategy = skip_strategy
        self.role = role
        self.run_context = run_context
        # Workflow-safe logger by default; callers can inject a pre-bound instance.
        self.logger = logger or workflow_logger
        self.tasks: dict[str, ActionStatement] = {}
        """Task definitions"""

        # Dynamic: Handle instances
        # Mut: Queue is used to schedule tasks
        self.queue: asyncio.Queue[Task] = asyncio.Queue()

        # Mut: Use to
        self.indegrees: dict[Task, int] = {}
        # Mut
        self.completed_tasks: set[Task] = set()
        # Mut: This tracks the state of edges between tasks
        # This is no longer correct because we now have multiple edges between tasks
        self.edges: dict[DSLEdge, EdgeMarker] = defaultdict(lambda: EdgeMarker.PENDING)
        # Mut
        self.task_exceptions: dict[str, TaskExceptionInfo] = {}
        self.stream_exceptions: dict[StreamID, TaskExceptionInfo] = {}

        # Build adjacency list with sets for efficient construction
        adj_temp: dict[str, set[AdjDst]] = defaultdict(set)
        for task in dsl.actions:
            self.tasks[task.ref] = task
            # This remains the same regardless of error paths, as each error path counts as an indegree
            self.indegrees[Task(task.ref, ROOT_STREAM)] = len(task.depends_on)
            for dep_ref in task.depends_on:
                src_ref, edge_type = self._get_edge_components(dep_ref)
                adj_temp[src_ref].add((task.ref, edge_type))

        # Convert to sorted tuples for deterministic iteration

        self.adj = {
            ref: tuple(sorted(adj_temp.get(ref, set()), key=self._adj_sort_key))
            for ref in self.tasks
        }
        """Adjacency list of task dependencies (sorted for determinism)"""

        control_adj = dsl._to_adjacency()
        (
            self.action_scopes,
            self.scope_hierarchy,
            _scope_openers,
        ) = dsl._assign_action_scopes(control_adj)
        self.loop_regions = self._build_loop_regions()
        self.loop_regions_by_end = {
            region.end_ref: region for region in self.loop_regions.values()
        }
        self.loop_indices: dict[tuple[str, StreamID], int] = {}
        self.loop_start_skips: set[tuple[str, StreamID]] = set()

        # Scope management
        self._root_context = context
        """Points to the worklfow roots stream context"""
        self.streams: dict[StreamID, ExecutionContext] = {
            ROOT_STREAM: self._root_context
        }

        self.stream_hierarchy: dict[StreamID, StreamID | None] = {ROOT_STREAM: None}
        """Points to the parent stream ID for each stream ID"""
        self.task_streams: defaultdict[Task, list[StreamID]] = defaultdict(list)
        self.open_streams: dict[Task, int] = {}
        """Used to track the number of scopes that have been closed for an scatter"""

        self.logger.debug(
            "Scheduler config",
            adj=self.adj,
            indegrees=self.indegrees,
            task_count=len(self.tasks),
            max_pending_tasks=self.max_pending_tasks,
        )

    @staticmethod
    def _adj_sort_key(adj: AdjDst) -> tuple[str, str]:
        dst_ref, edge_type = adj
        return dst_ref, edge_type.value

    def _scope_is_descendant(self, scope: str, ancestor_scope: str) -> bool:
        curr_scope = scope
        while curr_scope is not None:
            if curr_scope == ancestor_scope:
                return True
            curr_scope = self.scope_hierarchy.get(curr_scope)
        return False

    def _build_loop_regions(self) -> dict[str, LoopRegion]:
        regions: dict[str, LoopRegion] = {}
        for stmt in self.dsl.actions:
            if stmt.action != PlatformAction.LOOP_END:
                continue
            if not stmt.depends_on:
                raise RuntimeError(
                    f"Loop end action {stmt.ref!r} must depend on loop body actions"
                )

            dep_scopes = {
                self.action_scopes[edge_components_from_dep(dep_ref)[0]]
                for dep_ref in stmt.depends_on
            }
            if len(dep_scopes) != 1:
                raise RuntimeError(
                    f"Loop end action {stmt.ref!r} must close exactly one loop scope"
                )
            loop_scope = next(iter(dep_scopes))
            loop_start_stmt = self.tasks.get(loop_scope)
            if (
                loop_start_stmt is None
                or loop_start_stmt.action != PlatformAction.LOOP_START
            ):
                raise RuntimeError(
                    f"Loop end action {stmt.ref!r} does not match a loop start action"
                )
            if loop_scope in regions:
                raise RuntimeError(
                    f"Loop start action {loop_scope!r} has multiple loop end actions"
                )

            members = frozenset(
                ref
                for ref, scope in self.action_scopes.items()
                if self._scope_is_descendant(scope, loop_scope)
            )
            regions[loop_scope] = LoopRegion(
                start_ref=loop_scope,
                end_ref=stmt.ref,
                scope_ref=loop_scope,
                members=members | {stmt.ref},
            )
        return regions

    @staticmethod
    def _stream_within(stream_id: StreamID, base_stream_id: StreamID) -> bool:
        return stream_id == base_stream_id or str(stream_id).startswith(
            f"{base_stream_id}/"
        )

    def _cleanup_loop_descendant_streams(
        self, region: LoopRegion, stream_id: StreamID
    ) -> None:
        streams_to_remove: set[StreamID] = set()
        for task_key, scoped_streams in self.task_streams.items():
            if task_key.ref not in region.members or not self._stream_within(
                task_key.stream_id, stream_id
            ):
                continue
            streams_to_remove.update(scoped_streams)

        if not streams_to_remove:
            return

        # Include recursively nested streams created under loop-internal scatters.
        changed = True
        while changed:
            changed = False
            for candidate, parent in self.stream_hierarchy.items():
                if parent in streams_to_remove and candidate not in streams_to_remove:
                    streams_to_remove.add(candidate)
                    changed = True

        for stream in streams_to_remove:
            self.streams.pop(stream, None)
            self.stream_hierarchy.pop(stream, None)
            self.stream_exceptions.pop(stream, None)

        self.completed_tasks = {
            task
            for task in self.completed_tasks
            if task.stream_id not in streams_to_remove
        }
        self.indegrees = {
            task: indegree
            for task, indegree in self.indegrees.items()
            if task.stream_id not in streams_to_remove
        }
        self.edges = defaultdict(
            lambda: EdgeMarker.PENDING,
            {
                edge: marker
                for edge, marker in self.edges.items()
                if edge.stream_id not in streams_to_remove
            },
        )
        self.task_streams = defaultdict(
            list,
            {
                task: streams
                for task, streams in self.task_streams.items()
                if task.stream_id not in streams_to_remove
            },
        )
        self.open_streams = {
            task: n_open
            for task, n_open in self.open_streams.items()
            if task.stream_id not in streams_to_remove
        }

    def _reset_loop_iteration_state(
        self, region: LoopRegion, stream_id: StreamID
    ) -> None:
        # NOTE(loop semantics):
        # We only reset scheduler bookkeeping (edges/indegrees/completions/streams) to
        # allow another pass through the loop region. We intentionally DO NOT clear the
        # loop-region action context. Results are overwritten when an action runs again;
        # if an action is skipped this iteration, its prior value is retained.
        self._cleanup_loop_descendant_streams(region, stream_id)

        for task_key in list(self.task_streams.keys()):
            if task_key.ref in region.members and task_key.stream_id == stream_id:
                self.task_streams.pop(task_key, None)
        for task_key in list(self.open_streams.keys()):
            if task_key.ref in region.members and task_key.stream_id == stream_id:
                self.open_streams.pop(task_key, None)

        # Reset edge markers within the loop region for this stream.
        for edge in list(self.edges.keys()):
            if (
                edge.stream_id == stream_id
                and edge.src in region.members
                and edge.dst in region.members
            ):
                self.edges[edge] = EdgeMarker.PENDING

        # Reset indegrees for loop tasks so they can be scheduled again.
        for ref in region.members:
            task_key = Task(ref=ref, stream_id=stream_id)
            self.completed_tasks.discard(task_key)
            stmt = self.tasks.get(ref)
            if stmt is None:
                continue

            internal_deps = 0
            for dep_ref in stmt.depends_on:
                source_ref, _ = self._get_edge_components(dep_ref)
                if source_ref in region.members:
                    internal_deps += 1
            self.indegrees[task_key] = internal_deps

        # Reset nested loop indices within this loop scope.
        for loop_key in list(self.loop_indices.keys()):
            loop_start_ref, loop_stream = loop_key
            if loop_start_ref in region.members and self._stream_within(
                loop_stream, stream_id
            ):
                self.loop_indices.pop(loop_key, None)

    @property
    def workspace_id(self) -> str:
        if self.role.workspace_id is None:
            raise ValueError("Workspace ID is required")
        return str(self.role.workspace_id)

    @property
    def wf_exec_id(self) -> str:
        return self.run_context.wf_exec_id

    def __repr__(self) -> str:
        return to_json(self.__dict__, fallback=str, indent=2).decode()

    async def _handle_error_path(self, task: Task, exc: Exception) -> None:
        ref = task.ref

        self.logger.info(
            "Handling error path",
            task=task,
            type=exc.__class__.__name__,
            exc=exc,
        )
        # Prune any non-error paths and queue the rest
        non_err_edges: set[DSLEdge] = {
            DSLEdge(src=ref, dst=dst, type=edge_type, stream_id=task.stream_id)
            for dst, edge_type in self.adj[ref]
            if edge_type != EdgeType.ERROR
        }
        if len(non_err_edges) < len(self.adj[ref]):
            await self._queue_tasks(task, unreachable=non_err_edges)
        else:
            self.logger.info("Task failed with no error paths", task=task)
            # XXX: This can sometimes return null because the exception isn't an ApplicationError
            # But rather a ChildWorkflowError or CancelledError
            if isinstance(exc, ApplicationError) and exc.details:
                self.logger.info(
                    "Task failed with application error",
                    ref=ref,
                    exc=exc,
                    details=exc.details,
                )
                details = exc.details[0]
                if not isinstance(details, dict):
                    self.logger.info(
                        "Application error details are not a dictionary",
                        ref=ref,
                        details=details,
                    )
                    message = "Application error details are not a dictionary."
                    try:
                        message += f"\nGot: {to_json(details, fallback=str).decode()}"
                    except Exception as e:
                        self.logger.debug(
                            "Couldn't jsonify application error details",
                            ref=ref,
                            error=e,
                        )
                        message += "Couldn't parse error details as json."
                    details = ActionErrorInfo(
                        ref=ref,
                        message=message,
                        type=exc.__class__.__name__,
                        stream_id=task.stream_id,
                    )
                elif all(k in details for k in ("ref", "message", "type")):
                    # Regular action error
                    # it's of shape ActionErrorInfo()
                    try:
                        # This is normal action error
                        details = ActionErrorInfo(**details)
                    except Exception as e:
                        self.logger.info(
                            "Failed to parse regular application error details",
                            ref=ref,
                            error=e,
                        )
                        message = (
                            f"Failed to parse regular application error details: {e}."
                        )
                        try:
                            message += f"\n{to_json(details, fallback=str).decode()}"
                        except Exception as e:
                            self.logger.debug(
                                "Couldn't jsonify application error details",
                                ref=ref,
                                error=e,
                            )
                            message += "Couldn't parse error details as json."
                        details = ActionErrorInfo(
                            ref=ref,
                            message=message,
                            type=exc.__class__.__name__,
                            stream_id=task.stream_id,
                        )
                else:
                    # Child workflow error
                    # it's of shape {ref: ActionErrorInfo(), ...}
                    # try get the first element
                    try:
                        val = list(details.values())[0]
                        details = ActionErrorInfo(**val)
                    except Exception as e:
                        self.logger.info(
                            "Failed to parse child wf application error details",
                            ref=ref,
                            error=e,
                        )
                        message = "Child workflow error details are not a dictionary."
                        try:
                            message += (
                                f"\nGot: {to_json(details, fallback=str).decode()}"
                            )
                        except Exception as e:
                            self.logger.debug(
                                "Couldn't jsonify child wf application error details",
                                ref=ref,
                                error=e,
                            )
                            message += "Couldn't parse error details as json."
                        details = ActionErrorInfo(
                            ref=ref,
                            message=message,
                            type=exc.__class__.__name__,
                            stream_id=task.stream_id,
                        )
            else:
                self.logger.info(
                    "Task failed with non-application error",
                    ref=ref,
                    exc=exc,
                )
                try:
                    message = str(exc)
                except Exception as e:
                    self.logger.info(
                        "Failed to stringify non-application error",
                        ref=ref,
                        error=e,
                    )
                    message = f"Failed to stringify non-application error: {e}"
                details = ActionErrorInfo(
                    ref=ref,
                    message=message,
                    type=exc.__class__.__name__,
                    stream_id=task.stream_id,
                )
            if task.stream_id == ROOT_STREAM:
                self.logger.debug(
                    "Setting task exception in root stream", task=task, details=details
                )
                self.task_exceptions[ref] = TaskExceptionInfo(
                    exception=exc, details=details
                )
                # After this point we gracefully handle the error in the root stream which will be handled by Temporal.
            else:
                self.logger.debug(
                    "Setting task exception in execution stream",
                    task=task,
                    details=details,
                )
                self.stream_exceptions[task.stream_id] = TaskExceptionInfo(
                    exception=exc, details=details
                )
                # To "throw" we need to trigger a skip stream
                all_edges = {
                    DSLEdge(src=ref, dst=dst, type=edge_type, stream_id=task.stream_id)
                    for dst, edge_type in self.adj[ref]
                }
                await self._queue_tasks(task, unreachable=all_edges)

    async def _handle_success_path(self, task: Task) -> None:
        ref = task.ref
        self.logger.debug("Handling success path", ref=ref)
        # Prune any non-success paths and queue the rest
        non_ok_edges = {
            DSLEdge(src=ref, dst=dst, type=edge_type, stream_id=task.stream_id)
            for dst, edge_type in self.adj[ref]
            if edge_type != EdgeType.SUCCESS
        }
        await self._queue_tasks(task, unreachable=non_ok_edges)

    async def _handle_skip_path(self, task: Task, stmt: ActionStatement) -> None:
        ref = task.ref
        self.logger.debug("Handling skip path")

        if stmt.action == PlatformAction.LOOP_START:
            return await self._handle_loop_start(task, stmt, is_skipping=True)

        if stmt.action == PlatformAction.LOOP_END:
            return await self._handle_loop_end(task, stmt, is_skipping=True)

        if stmt.action == PlatformAction.TRANSFORM_SCATTER:
            return await self._handle_scatter(task, stmt, is_skipping=True)

        if stmt.action == PlatformAction.TRANSFORM_GATHER:
            # If we encounter an gather and we're in an execution stream,
            # we need to close the stream.
            self.logger.debug("Gather in execution stream, skipping", task=task)
            # We reached the end of the execution stream, so we can return
            # None to indicate that the stream is complete.
            # We do not need to queue any downstream tasks.
            return await self._handle_gather(task, stmt, is_skipping=True)
        # If we skip a task, we need to mark all its outgoing edges as skipped
        all_edges = {
            DSLEdge(src=ref, dst=dst, type=edge_type, stream_id=task.stream_id)
            for dst, edge_type in self.adj[ref]
        }
        await self._queue_tasks(task, unreachable=all_edges)

    async def _queue_tasks(
        self, task: Task, unreachable: set[DSLEdge] | None = None
    ) -> None:
        """Queue the next tasks that are ready to run."""
        # Update child indegrees
        # ----------------------
        # Treat a skipped task as completed, update as usual.
        # Any child task whose indegree reaches 0 must check if all its parent
        # dependencies we skipped. if ALL parents were skipped, then the child
        # task is also marked for skipping. If ANY parent was not skipped, then
        # the child task is added to the queue.

        # The intuition here is that if you have a task that becomes unreachable,
        # then some of its children will also become unreachable. A node becomes unreachable
        # if there is not one successful path that leads to it.

        # This allows us to have diamond-shaped graphs where some branches can be skipped
        # but at the join point, if any parent was not skipped, then the child can still be executed.
        ref = task.ref
        stream_id = task.stream_id
        next_tasks = self.adj.get(ref, ())
        self.logger.debug(
            "Queueing tasks",
            task=task,
            next_tasks=next_tasks,
        )
        for next_ref, edge_type in next_tasks:
            self.logger.debug("Processing next task", ref=ref, next_ref=next_ref)
            edge = DSLEdge(src=ref, dst=next_ref, type=edge_type, stream_id=stream_id)
            if unreachable and edge in unreachable:
                self._mark_edge(edge, EdgeMarker.SKIPPED)
            else:
                self._mark_edge(edge, EdgeMarker.VISITED)
            # Mark the edge as processed
            # Task inherits the current stream
            # Inherit the delay if it exists. We need this to stagger tasks for scatter.
            next_task = Task(ref=next_ref, stream_id=stream_id, delay=task.delay)
            # We dynamically add the indegree of the next task to the indegrees dict
            if next_task not in self.indegrees:
                self.indegrees[next_task] = len(self.tasks[next_ref].depends_on)
            self.indegrees[next_task] -= 1
            if self.indegrees[next_task] == 0:
                # Schedule the next task
                self.logger.debug(
                    "Adding task to queue; mark visited", next_ref=next_ref
                )
                await self.queue.put(next_task)
        self.logger.trace(
            "Queued tasks",
            visited_tasks=list(self.completed_tasks),
            tasks=list(self.tasks.keys()),
            queue_size=self.queue.qsize(),
        )

    async def _execute(self, task: Task, stmt: ActionStatement) -> None:
        """Execute a task."""
        token = ctx_stream_id.set(task.stream_id)
        try:
            await self.executor(stmt)
        finally:
            ctx_stream_id.reset(token)

    async def _schedule_task(self, task: Task) -> None:
        """Schedule a task for execution."""
        ref = task.ref
        stmt = self.tasks[ref]
        self.logger.debug("Scheduling task", task=task)
        # Normalize delay immediately so downstream tasks never inherit it when we skip.
        original_delay = task.delay
        if original_delay > 0:
            task = replace(task, delay=0.0)
        try:
            # 1) Skip propagation (force-skip) takes highest precedence over everything else
            if self._skip_should_propagate(task, stmt):
                self.logger.debug(
                    "Task should be force-skipped, propagating", task=task
                )
                return await self._handle_skip_path(task, stmt)

            # 2) Then we check if the task is reachable
            if not self._is_reachable(task, stmt):
                self.logger.debug("Task cannot proceed, unreachable", task=task)
                raise TaskUnreachable(f"Task {task} is unreachable")

            # 3) Check if the task should self-skip based on its `run_if` condition
            if await self._task_should_skip(task, stmt):
                self.logger.debug("Task should self-skip", task=task)
                return await self._handle_skip_path(task, stmt)

            # 4) If we made it here, the task is reachable and not force-skipped.

            # Respect the task delay if it exists. We need this to stagger tasks for scatter.
            if original_delay > 0:
                self.logger.debug(
                    "Task has delay, sleeping", task=task, delay=original_delay
                )
                await asyncio.sleep(original_delay)

            # -- If this is a control flow action (scatter), we need to
            # handle it differently.
            if stmt.action == PlatformAction.LOOP_START:
                return await self._handle_loop_start(task, stmt)
            if stmt.action == PlatformAction.LOOP_END:
                return await self._handle_loop_end(task, stmt)
            if stmt.action == PlatformAction.TRANSFORM_SCATTER:
                return await self._handle_scatter(task, stmt)
            # 0) Always handle gather first - its a synchronization barrier that needs
            # to run regardless of dependency skip states
            if stmt.action == PlatformAction.TRANSFORM_GATHER:
                return await self._handle_gather(task, stmt)

            # -- Otherwise, time to execute the task!
            # NOTE: If an exception is thrown from this coroutine, it signals that
            # the task failed after all attempts. Adding the exception to the task
            # exceptions set will cause the workflow to fail.
            await self._execute(task, stmt)
            # NOTE: Moved this here to handle single success path
            await self._handle_success_path(task)
        except Exception as e:
            kind = e.__class__.__name__
            non_retryable = getattr(e, "non_retryable", True)
            self.logger.warning(
                f"{kind} in DSLScheduler", ref=ref, error=e, non_retryable=non_retryable
            )
            await self._handle_error_path(task, e)
        finally:
            # 5) Regardless of the outcome, the task is now complete
            self.logger.debug("Task completed", task=task)
            self.completed_tasks.add(task)

    async def start(self) -> dict[str, TaskExceptionInfo] | None:
        """Run the scheduler and return any exceptions that occurred."""
        # Instead of explicitly setting the entrypoint, we set all zero-indegree
        # tasks to the queue.
        for task_instance, indegree in self.indegrees.items():
            if indegree == 0:
                self.queue.put_nowait(task_instance)

        pending_tasks: set[asyncio.Task[None]] = set()

        while not self.task_exceptions and (not self.queue.empty() or pending_tasks):
            self.logger.trace(
                "Waiting for tasks",
                qsize=self.queue.qsize(),
                n_pending=len(pending_tasks),
            )

            # Clean up completed tasks
            done_tasks = {t for t in pending_tasks if t.done()}
            pending_tasks.difference_update(done_tasks)

            while (
                not self.queue.empty() and len(pending_tasks) < self.max_pending_tasks
            ):
                task_instance = await self.queue.get()
                self.logger.debug("Scheduling task", task=task_instance)
                task = asyncio.create_task(self._schedule_task(task_instance))
                pending_tasks.add(task)
            if not self.queue.empty() and len(pending_tasks) >= self.max_pending_tasks:
                self.logger.debug(
                    "Scheduler throttled by max pending task cap",
                    pending_tasks=len(pending_tasks),
                    max_pending_tasks=self.max_pending_tasks,
                    queue_size=self.queue.qsize(),
                )
                await workflow.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)
            elif pending_tasks:
                # Wait for at least one pending task to complete
                await workflow.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)

        if self.task_exceptions:
            self.logger.warning(
                "DSLScheduler got task exceptions, stopping...",
                n_exceptions=len(self.task_exceptions),
                exceptions=self.task_exceptions,
                n_visited=len(self.completed_tasks),
                n_tasks=len(self.tasks),
            )
            # Cancel all pending tasks and wait for them to complete
            for task in pending_tasks:
                if not task.done():
                    task.cancel()

            if pending_tasks:
                try:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
                except Exception as e:
                    self.logger.warning("Error while canceling tasks", error=e)

            return self.task_exceptions
        self.logger.info(
            "All tasks completed",
            n_tasks=len(self.tasks),
        )
        self.logger.debug(
            "All tasks completed (details)",
            completed_tasks=list(self.completed_tasks),
        )
        return None

    def _is_reachable(self, task: Task, stmt: ActionStatement) -> bool:
        """Check whether a task is reachable based on its dependencies' outcomes.

        Args:
            task_ref (str): The reference of the task to check

        Returns:
            bool: True if the task is reachable, False otherwise

        Raises:
            ValueError: If the join strategy is invalid
        """

        self.logger.debug("Check task reachability", task=task, marked_edges=self.edges)
        n_deps = len(stmt.depends_on)
        if n_deps == 0:
            # Root nodes are always reachable
            return True
        elif n_deps == 1:
            self.logger.debug("Task has only 1 dependency", task=task)
            # If there's only 1 dependency, the node is reachable only if the
            # dependency was successful ignoring the join strategy.
            dep_ref = stmt.depends_on[0]
            return self._edge_has_marker(
                dep_ref, task.ref, EdgeMarker.VISITED, task.stream_id
            )
        else:
            # If there's more than 1 dependency, the node is reachable depending
            # on the join strategy
            n_success_paths = sum(
                self._edge_has_marker(
                    dep_ref, task.ref, EdgeMarker.VISITED, task.stream_id
                )
                for dep_ref in stmt.depends_on
            )
            if stmt.join_strategy == JoinStrategy.ANY:
                return n_success_paths > 0
            if stmt.join_strategy == JoinStrategy.ALL:
                return n_success_paths == n_deps
            raise ValueError(f"Invalid join strategy: {stmt.join_strategy}")

    def _edge_has_marker(
        self,
        src_ref_path: str,
        dst_ref: str,
        marker: EdgeMarker,
        stream_id: StreamID,
    ) -> bool:
        edge = self._get_edge_by_refs(src_ref_path, dst_ref, stream_id)
        return self.edges[edge] == marker

    def _get_edge_components(self, ref_path: str) -> AdjDst:
        return edge_components_from_dep(ref_path)

    def _get_edge_by_refs(
        self, src_ref_path: str, dst_ref: str, stream_id: StreamID
    ) -> DSLEdge:
        """Get an edge by its source and destination references.

        Args:
            src_ref_path: The source reference path
            dst_ref: The destination reference

        Returns:
            The edge
        """
        base_src_ref, edge_type = self._get_edge_components(src_ref_path)
        return DSLEdge(
            src=base_src_ref, dst=dst_ref, type=edge_type, stream_id=stream_id
        )

    def _mark_edge(self, edge: DSLEdge, marker: EdgeMarker) -> None:
        self.logger.debug("Marking edge", edge=edge, marker=marker)
        self.edges[edge] = marker

    def _skip_should_propagate(self, task: Task, stmt: ActionStatement) -> bool:
        """
        Check if a task's skip should propagate to its dependents.

        This function determines whether all dependencies of the given task have been marked as SKIPPED.
        If so, the skip should propagate to this task, causing it to be skipped as well.

        Args:
            task: The Task object being evaluated.
            stmt: The ActionStatement associated with the task.

        Returns:
            True if all dependencies are marked as SKIPPED, False otherwise.
        """
        deps = stmt.depends_on
        # If there are no dependencies, skip propagation does not apply.
        if not deps:
            return False
        # Check if every dependency edge is marked as SKIPPED in the current stream.
        return all(
            self._edge_has_marker(dep_ref, task.ref, EdgeMarker.SKIPPED, task.stream_id)
            for dep_ref in deps
        )

    async def _task_should_skip(self, task: Task, stmt: ActionStatement) -> bool:
        """Check if a task should be skipped based on its `run_if` condition."""
        run_if = stmt.run_if
        if run_if is not None:
            context = self.build_stream_aware_context(stmt, task.stream_id)
            self.logger.debug("`run_if` condition", run_if=run_if)
            try:
                expr_result = await self.resolve_expression(run_if, context)
            except Exception as e:
                raise ApplicationError(
                    f"Error evaluating `run_if` condition: {e}",
                    non_retryable=True,
                ) from e

            if not bool(expr_result):
                self.logger.debug("Task `run_if` condition was not met, skipped")
                return True
        return False

    async def _queue_skip_stream(self, task: Task, stream_id: StreamID) -> None:
        """Queue a skip stream for a task."""
        new_stream_id = StreamID.skip(task.ref, base_stream_id=stream_id)
        self.stream_hierarchy[new_stream_id] = stream_id
        self.streams[new_stream_id] = ExecutionContext(ACTIONS={}, TRIGGER=None)
        unreachable = {
            DSLEdge(src=task.ref, dst=dst, type=edge_type, stream_id=new_stream_id)
            for dst, edge_type in self.adj[task.ref]
        }
        skip_task = Task(ref=task.ref, stream_id=new_stream_id)
        self.logger.debug("Queueing skip stream", skip_task=skip_task)
        # Acknowledge the new scope
        return await self._queue_tasks(skip_task, unreachable=unreachable)

    async def _handle_scatter_skip_stream(
        self, task: Task, stream_id: StreamID
    ) -> None:
        new_stream_id = StreamID.skip(task.ref, base_stream_id=stream_id)
        self.logger.debug(
            "Creating skip stream", task=task, new_stream_id=new_stream_id
        )
        self.stream_hierarchy[new_stream_id] = stream_id
        self.streams[new_stream_id] = ExecutionContext(ACTIONS={}, TRIGGER=None)
        all_next = {
            DSLEdge(src=task.ref, dst=dst, type=edge_type, stream_id=new_stream_id)
            for dst, edge_type in self.adj[task.ref]
        }
        skip_task = Task(ref=task.ref, stream_id=new_stream_id)
        self.logger.debug("Queueing skip stream", skip_task=skip_task)
        # Acknowledge the new scope
        return await self._queue_tasks(skip_task, unreachable=all_next)

    async def _handle_loop_start(
        self, task: Task, stmt: ActionStatement, *, is_skipping: bool = False
    ) -> None:
        self.logger.debug("Handling loop start", task=task, is_skipping=is_skipping)
        loop_key = (task.ref, task.stream_id)
        if is_skipping:
            # If skip reaches loop_start, treat start->end as one skipped unit.
            self.loop_start_skips.add(loop_key)
            unreachable = {
                DSLEdge(src=task.ref, dst=dst, type=edge_type, stream_id=task.stream_id)
                for dst, edge_type in self.adj[task.ref]
            }
            await self._queue_tasks(task, unreachable=unreachable)
            return

        self.loop_start_skips.discard(loop_key)
        index = self.loop_indices.get(loop_key, 0)
        action_context = self._get_action_context(task.stream_id)
        action_context[task.ref] = TaskResult.from_result({"iteration": index})
        await self._execute(task, stmt)
        await self._handle_success_path(task)

    async def _handle_loop_end(
        self, task: Task, stmt: ActionStatement, *, is_skipping: bool = False
    ) -> None:
        self.logger.debug("Handling loop end", task=task, is_skipping=is_skipping)
        if task.ref not in self.loop_regions_by_end:
            raise RuntimeError(f"Unknown loop end action: {task.ref!r}")

        region = self.loop_regions_by_end[task.ref]
        raw_max_iterations = stmt.args.get("max_iterations", 100)
        if (
            isinstance(raw_max_iterations, int)
            and raw_max_iterations > MAX_DO_WHILE_ITERATIONS
        ):
            raise ApplicationError(
                (
                    "Loop max_iterations exceeds platform cap: "
                    f"{raw_max_iterations} > {MAX_DO_WHILE_ITERATIONS}."
                ),
                non_retryable=True,
            )
        args = LoopEndArgs(**stmt.args)
        loop_key = (region.start_ref, task.stream_id)
        current_index = self.loop_indices.get(loop_key, 0)
        # Skip semantics:
        # - skip reached loop_start => skip the whole loop unit (start->end)
        # - skip originated inside loop body => stop skipping at loop_end
        skip_propagated_from_loop_start = (
            is_skipping and loop_key in self.loop_start_skips
        )
        if skip_propagated_from_loop_start:
            self.loop_start_skips.discard(loop_key)

        if is_skipping:
            should_continue = False
        else:
            context = self.build_stream_aware_context(stmt, task.stream_id)
            try:
                expr_result = await self.resolve_expression(args.condition, context)
            except Exception as e:
                raise ApplicationError(
                    f"Error evaluating `condition` in `core.loop.end`: {e}",
                    non_retryable=True,
                ) from e
            should_continue = bool(expr_result)

        action_context = self._get_action_context(task.stream_id)
        action_context[task.ref] = TaskResult.from_result({"continue": should_continue})

        if not is_skipping:
            await self._execute(task, stmt)

        if should_continue:
            next_index = current_index + 1
            if next_index >= args.max_iterations:
                raise ApplicationError(
                    (
                        f"Loop '{task.ref}' exceeded max_iterations={args.max_iterations}. "
                        "Update `condition` or increase `max_iterations`."
                    ),
                    non_retryable=True,
                )
            self._reset_loop_iteration_state(region, task.stream_id)
            self.loop_indices[loop_key] = next_index
            await self.queue.put(Task(ref=region.start_ref, stream_id=task.stream_id))
            return

        self.loop_indices.pop(loop_key, None)
        if skip_propagated_from_loop_start:
            # A loop-start skip means the current iteration is being bypassed; mark every
            # outgoing edge from this task unreachable so skip semantics propagate downstream.
            all_edges = {
                DSLEdge(src=task.ref, dst=dst, type=edge_type, stream_id=task.stream_id)
                for dst, edge_type in self.adj[task.ref]
            }
            await self._queue_tasks(task, unreachable=all_edges)
            return
        await self._handle_success_path(task)

    async def _handle_scatter(
        self, task: Task, stmt: ActionStatement, *, is_skipping: bool = False
    ) -> None:
        """
        Handle scatter action with proper stream creation.

        When exploding a collection, len(collection) scopes are created.
        Each stream has a unique stream ID and contains a single item from the collection.

        The tasks in each stream are queued in the order of the collection.

        The tasks in each stream are executed in the order of the collection.

        """
        # Our current location, before creating any new streams
        curr_stream_id = task.stream_id
        self.logger.debug("Handling scatter", task=task, is_skipping=is_skipping)
        if is_skipping:
            self.logger.debug("Skipped scatter", task=task)
            return await self._handle_scatter_skip_stream(task, curr_stream_id)

        args = ScatterArgs(**stmt.args)
        context = self.get_context(curr_stream_id)

        collection_key = action_collection_prefix(
            self.workspace_id, self.wf_exec_id, curr_stream_id, task.ref
        )

        try:
            collection = await workflow.execute_activity(
                DSLActivities.handle_scatter_input_activity,
                arg=ScatterActionInput(
                    task=stmt,
                    stream_id=curr_stream_id,
                    collection=args.collection,
                    operand=context,
                    key=collection_key,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
        except ActivityError as e:
            match cause := e.cause:
                case ApplicationError():
                    raise cause from None
                case _:
                    raise

        # 1) Create a new stream (ALWAYS)
        # ALWAYS initialize tracking structures (even for empty collections)
        # This ensures that _handle_gather can find the scatter task in tracking structures

        streams: list[StreamID] = []
        self.task_streams[task] = streams

        # Get collection size - works with both CollectionObject and InlineObject
        collection_size = _get_collection_size(collection)

        # -- SKIP STREAM
        if collection_size == 0:
            # Mark scatter as observed
            self.open_streams[task] = 0
            self.logger.debug("Empty collection for scatter", task=task)
            return await self._handle_scatter_skip_stream(task, curr_stream_id)

        # -- EXECUTION STREAM
        self.logger.debug(
            "Scattering collection",
            task=task,
            collection_size=collection_size,
            interval=args.interval,
        )

        async for i in cooperative(range(collection_size)):
            new_stream_id = StreamID.new(task.ref, i, base_stream_id=curr_stream_id)
            streams.append(new_stream_id)

            # Initialize stream with indexed reference to stored collection
            self.stream_hierarchy[new_stream_id] = curr_stream_id
            self.streams[new_stream_id] = ExecutionContext(
                ACTIONS={
                    task.ref: TaskResult.from_collection_item(
                        stored=collection,
                        index=i,
                        item_typename="collection_item",
                    )
                },
                TRIGGER=None,
            )

            # Create tasks for all tasks in this stream
            # Calculate the task delay
            delay = i * (args.interval or 0)
            new_scoped_task = Task(ref=task.ref, stream_id=new_stream_id, delay=delay)
            self.logger.debug(
                "Creating stream",
                stream_id=new_stream_id,
                task=new_scoped_task,
            )
            # This will queue the task for execution stream
            await self._queue_tasks(new_scoped_task)

        self.open_streams[task] = len(streams)
        # Get the next tasks to queue
        self.logger.debug(
            "Scatter completed",
            task=task,
            collection_size=collection_size,
            scopes_created=len(streams),
        )

    def get_context(self, stream_id: StreamID) -> ExecutionContext:
        context = self.streams[stream_id]
        self.logger.trace("Getting stream context", stream_id=stream_id)
        return context

    async def _handle_gather_skip_stream(
        self, task: Task, stmt: ActionStatement, stream_id: StreamID
    ) -> None:
        self.logger.debug("Handling gather skip stream", task=task)
        parent_stream_id = self._get_parent_stream_id_safe(task, stream_id)
        parent_action_context = self._get_action_context(parent_stream_id)

        scatter_ref, stream_idx = stream_id.leaf
        gather_ref = task.ref
        parent_scatter = Task(ref=scatter_ref, stream_id=parent_stream_id)

        if err_info := self.stream_exceptions.get(stream_id):
            # We reached gather because of an irrecoverable error
            self.logger.debug("Found matching error in skip stream", task=task)
            if gather_ref not in parent_action_context:
                # NOTE: This block is executed by the first execution stream that finishes.
                # We need to initialize the result with the cardinality of the scatter
                # This is the number of execution streams that will be synchronized by this gather
                size = len(self.task_streams[parent_scatter])
                result = [Sentinel.GATHER_UNSET for _ in range(size)]
                parent_action_context[gather_ref] = TaskResult.from_result(result)

            # Place an error object in the result
            # Do not pass the full object as some exceptions aren't serializable
            # Access the raw list via get_data() and modify in place
            parent_action_context[gather_ref].get_data()[stream_idx] = InlineObject(
                data=ActionErrorInfoAdapter.dump_python(err_info.details)
            )
            self.logger.debug("Set error object as result", task=task)
        else:
            # Regular skip path
            self.logger.debug("No matching error in skip stream", task=task)

        # We still have to handle the last gather
        if self.open_streams[parent_scatter] == 0:
            self.logger.debug("Closing skip stream")
            await self._handle_gather_result(
                task,
                stmt,
                parent_action_context,
                parent_stream_id,
                GatherArgs(**stmt.args),
                gather_ref,
            )
        else:
            self.logger.debug(
                "The execution stream is complete but the gather isn't.",
                task=task,
                remaining_open_streams=self.open_streams[parent_scatter],
            )

    def _get_parent_stream_id_safe(self, task: Task, stream_id: StreamID) -> StreamID:
        parent_stream = self.stream_hierarchy[stream_id]
        if parent_stream is None:
            # Raise a detailed error if a gather is found in a skip stream with no parent stream.
            raise RuntimeError(
                f"Invalid state: Gather encountered in skip stream with no parent stream for task {task!r} "
                f"(stream_id={stream_id!r}).\n"
                "This indicates the gather is executing in the root stream's skip context, "
                "which should not occur. This is likely a bug in the scheduler logic. "
                "Please check the stream hierarchy and ensure that gathers are not scheduled "
                "in the root skip stream."
            )
        return parent_stream

    def _task_observed(self, task: Task) -> bool:
        return task in self.open_streams

    async def _handle_gather(
        self, task: Task, stmt: ActionStatement, *, is_skipping: bool = False
    ) -> None:
        """Handle gather with proper synchronization.

        Think of this as a synchronization barrier for a collection of execution streams.

        Logic:
        - Gather is given a jsonpath. Get the item from the current stream. This will be returned to the parent stream.
        - We need to know the cardinality of the scatterd collection so that we can reconstruct the collection in the parent stream.

        Edge cases:
        - If used in the global stream (no matching scatter), gather does nothing and returns early.
          This allows gathers to be safely scheduled even when their corresponding scatter was skipped.

        Cases:
        - NO execution stream but has scope - means the scatter was skipped because the collection was empty
        --- Just place an empty array in the result
        - NO execution stream but no scope - means this gather occurred without a corresponding scatter
        - Has execution stream but no scope
        --- If we're in the global scope, this is an invalid state
        --- If we're in a scoped context, this can only happen if the scatter didn't run
        - Has execution stream and scope - this is the normal case

        IN OTHER WORDS
        - Gather should only look at scope_id to make decisions.
        - If gather arrives without stream, look at scope_id. In these cases we should just set the value to empty array.
        """
        self.logger.debug("Handling gather", task=task)
        match task:
            # Root stream
            case Task(stream_id=stream_id) if stream_id == ROOT_STREAM:
                if not is_skipping:
                    raise RuntimeError(
                        f"Found gather in root stream but not skipping: {task}. User has probably made a mistake"
                    )
                self.logger.debug("Skipped gather in root stream.", task=task)
                # I'm not sure if we ever reach this point. We probably error out before this.
                # Check whether we have a corresponding error
                # await self._handle_gather_skip_stream(task, stmt, stream_id)
                raise RuntimeError("We are skipping but we should be in a skip stream")

            # Skip stream
            case Task(stream_id=stream_id) if stream_id and stream_id.endswith(":skip"):
                self.logger.debug("Gather in skip stream. Perform cleanup", task=task)

                # This shouldn't fail as all skip streams have parent streams
                parent_stream = self._get_parent_stream_id_safe(task, stream_id)
                scatter_ref, _ = stream_id.leaf
                scatter_task = Task(ref=scatter_ref, stream_id=parent_stream)
                self.logger.debug(
                    "Checking for observed scatter",
                    scatter_task=scatter_task,
                )
                if self._task_observed(scatter_task):
                    self.logger.debug(
                        "Observed scatter, setting result to empty collection",
                        task=task,
                    )
                    parent_action_context = self._get_action_context(parent_stream)
                    finalized = await workflow.execute_activity(
                        DSLActivities.finalize_gather_activity,
                        arg=FinalizeGatherActivityInput(
                            collection=[],
                            key=action_collection_prefix(
                                self.workspace_id,
                                self.wf_exec_id,
                                str(parent_stream),
                                task.ref,
                            ),
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=RETRY_POLICIES["activity:fail_fast"],
                    )
                    parent_action_context[task.ref] = TaskResult(
                        result=finalized.result,
                        result_typename=finalized.result.typename or "list",
                    )
                else:
                    self.logger.debug(
                        "Scatter not observed, skipping",
                        task=task,
                        scatter_task=scatter_task,
                    )
                    # If scatter wasn't observed, this means it was force-skipped
                    # This means we need to skip all downstream tasks
                    unreachable = {
                        DSLEdge(src=task.ref, dst=dst, type=type, stream_id=stream_id)
                        for dst, type in self.adj[task.ref]
                    }
                    return await self._queue_tasks(task, unreachable=unreachable)

                # Now we can exit the scope and queue next tasks
                next_task = Task(ref=task.ref, stream_id=parent_stream)
                self.logger.debug(
                    "Queueing next task",
                    next_task=next_task,
                    parent_stream=parent_stream,
                )
                return await self._queue_tasks(next_task)

            #  Scoped execution stream
            case Task(stream_id=stream_id) if stream_id:
                self.logger.debug("Default gather path", stream_id=stream_id)
                parent_stream_id = self._get_parent_stream_id_safe(task, stream_id)
            case _:
                self.logger.warning("Invalid gather state", task=task)
                raise ApplicationError("Invalid gather state")
        # =============
        # We only reach this point if we are in an execution stream.
        # =============

        # We are inside an execution stream. Use gather to synchronize with the other execution streams.
        self.logger.debug("Handling gather in execution stream", task=task)

        # What are we doing here?
        # We are in an execution stream. Need to close it now.
        scatter_ref, stream_idx = stream_id.leaf
        # This operation should return the parent stream
        parent_scatter = Task(ref=scatter_ref, stream_id=parent_stream_id)

        # Close the stream regardless of whether we skipped our way to this point.
        self.logger.debug(
            "Closing stream",
            task=task,
            parent_scatter=parent_scatter,
        )
        # Close the execution stream regardless of whether we skipped our way to this point.
        if is_skipping:
            self.open_streams[parent_scatter] -= 1
            self.logger.debug("Skipped gather in execution stream", task=task)
            # Return early to avoid setting the result as null.
            await self._handle_gather_skip_stream(task, stmt, stream_id)
            return

        # Here onwards, we are not skipping.
        args = GatherArgs(**stmt.args)
        gather_ref = task.ref
        # This means we must compute a return value for the gather.
        # We should only compute the items to store if we aren't skipping
        current_context = self.get_context(stream_id)
        try:
            item = await workflow.execute_activity(
                DSLActivities.evaluate_templated_object_activity,
                arg=EvaluateTemplatedObjectActivityInput(
                    obj=args.items,
                    operand=current_context,
                    key=action_key(
                        self.workspace_id,
                        self.wf_exec_id,
                        str(stream_id),
                        gather_ref,
                    ),
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
        except Exception as e:
            raise ApplicationError(
                f"Error evaluating `items` expression in `core.transform.gather`: {e}",
                non_retryable=True,
            ) from e

        # XXX(concurrency): It's important we only decrement open_streams after
        # await block. If not, streams at the current level will observe 0
        # for the parent open_streams when resumed, and will not close the
        # stream prematurely.
        self.open_streams[parent_scatter] -= 1

        # Once we have the item, we go down 1 level in the stream hierarchy
        # and set the item as the result of the action in that stream
        # We do this for each item in the collection
        parent_action_context = self._get_action_context(parent_stream_id)

        # We set the item as the result of the action in that stream
        # We should actually be placing this in parent.result[i]

        # Set result array if this is the first stream
        if gather_ref not in parent_action_context:
            # NOTE: This block is executed by the first execution stream that finishes.
            # We need to initialize the result with the cardinality of the scatter
            # This is the number of execution streams that will be synchronized by this gather
            size = len(self.task_streams[parent_scatter])
            result = [Sentinel.GATHER_UNSET for _ in range(size)]
            parent_action_context[gather_ref] = TaskResult.from_result(result)

        # Access the raw list via get_data() and modify in place
        parent_action_context[gather_ref].get_data()[stream_idx] = item

        if self.open_streams[parent_scatter] == 0:
            await self._handle_gather_result(
                task,
                stmt,
                parent_action_context,
                parent_stream_id,
                args,
                gather_ref,
            )
        else:
            self.logger.debug(
                "The execution stream is complete but the gather isn't.",
                task=task,
                remaining_open_streams=self.open_streams[parent_scatter],
            )

    async def _handle_gather_result(
        self,
        task: Task,
        stmt: ActionStatement,
        parent_action_context: dict[str, TaskResult],
        parent_stream_id: StreamID,
        gather_args: GatherArgs,
        gather_ref: str,
    ) -> None:
        self.logger.debug("Handling gather result", task=task)
        # We have closed all execution streams for this scatter. The gather is now complete.
        # Apply filtering if requested

        # Inline filter for gather operation.
        # Keeps items unless drop_nulls is True and item is None.
        # Automatically remove unset values (Sentinel.IMPLODE_UNSET).
        if gather_ref not in parent_action_context:
            parent_action_context[gather_ref] = TaskResult.from_result([])
        task_result = parent_action_context[gather_ref]

        # Gather items are StoredObjects produced in each execution stream.
        # Filter out unset values (Sentinel.GATHER_UNSET) here, then materialize + filter
        # (drop_nulls, error strategy) inside an activity to avoid large payloads in history.
        stored_items = [
            StoredObjectValidator.validate_python(item)
            for item in task_result.get_data()
            if item != Sentinel.GATHER_UNSET
        ]

        finalized = await workflow.execute_activity(
            DSLActivities.finalize_gather_activity,
            arg=FinalizeGatherActivityInput(
                collection=stored_items,
                key=action_collection_prefix(
                    self.workspace_id,
                    self.wf_exec_id,
                    str(parent_stream_id),
                    gather_ref,
                ),
                drop_nulls=gather_args.drop_nulls,
                error_strategy=gather_args.error_strategy,
            ),
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )

        self.logger.debug(
            "Gather finalized",
            strategy=gather_args.error_strategy,
            task=task,
            result_count=_get_collection_size(finalized.result),
            error_count=len(finalized.errors),
        )

        if (
            gather_args.error_strategy == StreamErrorHandlingStrategy.RAISE
            and finalized.errors
        ):
            message = (
                f"Gather '{gather_ref}' encountered {len(finalized.errors)} error(s)"
            )
            gather_error = ActionErrorInfo(
                ref=gather_ref,
                message=message,
                type=ApplicationError.__name__,
                children=finalized.errors,
                stream_id=parent_stream_id,
            )
            app_error = ApplicationError(
                message,
                {gather_ref: ActionErrorInfoAdapter.dump_python(gather_error)},
                non_retryable=True,
            )
            self.logger.warning(
                "Raising gather error", errors=finalized.errors, app_error=app_error
            )

            # Register the gather failure so the scheduler halts and the workflow error
            # handler can run, even though the exception originates from a non-root stream.
            self.task_exceptions[gather_ref] = TaskExceptionInfo(
                exception=app_error,
                details=gather_error,
            )
            raise app_error

        task_result = task_result.model_copy(
            update={
                "result": finalized.result,
                "result_typename": finalized.result.typename or "list",
            }
        )
        if finalized.errors:
            task_result = task_result.with_error(finalized.errors)

        parent_action_context[gather_ref] = task_result
        self.logger.debug(
            "Gather complete. Go back up to parent stream",
            task=task,
            parent_stream_id=parent_stream_id,
        )
        parent_gather = Task(ref=gather_ref, stream_id=parent_stream_id)
        # TODO: Handle unreachable tasks??
        # Emit gather action in Temporal
        await self._execute(parent_gather, stmt)
        await self._queue_tasks(parent_gather)

    def _get_action_context(self, stream_id: StreamID) -> dict[str, TaskResult]:
        context = self.get_context(stream_id)
        return context.get("ACTIONS", {})

    def build_stream_aware_context(
        self, task: ActionStatement, stream_id: StreamID
    ) -> ExecutionContext:
        """Build a context that is aware of the stream hierarchy."""
        expr_ctxs = extract_expressions(task.model_dump())
        resolved_actions: dict[str, TaskResult] = {}
        for action_ref in expr_ctxs[ExprContext.ACTIONS]:
            result = self.get_stream_aware_action_result(action_ref, stream_id)
            # Only include actions that exist in the stream hierarchy.
            # Actions that don't exist (return None) are omitted to prevent
            # ValidationError in RunActionInput which expects TaskResult values.
            if result is not None:
                resolved_actions[action_ref] = result
        new_context = self._root_context.copy()
        new_context.update(ACTIONS=resolved_actions)
        return new_context

    def get_stream_aware_action_result(
        self, action_ref: str, stream_id: StreamID
    ) -> TaskResult | None:
        """
        Resolve an action expression in a stream-aware manner.

        Traverses from the current stream up through the hierarchy until it finds
        the action result or reaches the global stream.

        Args:
            action_ref: The action reference to resolve (e.g., "webhook", "transform_1")
            stream_id: The current stream ID to start resolution from. If None, uses global stream.

        Returns:
            The action result if found, None otherwise.

        Example:
            # In a scoped context
            result = scheduler.resolve_action_expression("webhook", current_stream_id)

            # In global context
            result = scheduler.resolve_action_expression("webhook")

        Performance
        -----------
        WE SHOULD TOTALLY CACHE THIS FUNCTION BTW
        """
        self.logger.trace(
            "Resolving action expression",
            action_ref=action_ref,
            stream_id=stream_id,
        )

        # Start from the current stream and work upwards
        curr_stream = stream_id

        while curr_stream is not None:
            # Check if the action exists in the current stream
            if stream_context := self.streams.get(curr_stream):
                actions_context = stream_context.get("ACTIONS", {})
                if action_ref in actions_context:
                    self.logger.trace(
                        "Found action in stream",
                        action_ref=action_ref,
                        stream_id=curr_stream,
                    )
                    return actions_context[action_ref]

            # Move to parent stream
            curr_stream = self.stream_hierarchy.get(curr_stream)
            self.logger.trace(
                "Moving to parent stream",
                action_ref=action_ref,
                parent_stream=curr_stream,
            )

        # Action not found in any stream
        self.logger.debug(
            "Action not found in any stream", action_ref=action_ref, stream_id=stream_id
        )
        return None

    async def resolve_expression(
        self, expression: str, context: ExecutionContext
    ) -> Any:
        """Evaluate an expression."""
        self.logger.trace(
            "Resolving expression", expression=expression, context=context
        )
        try:
            return await workflow.execute_activity(
                DSLActivities.evaluate_single_expression_activity,
                args=(expression, context),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
        except ActivityError as e:
            # Capture the ApplicationError from the activity so we can fail the wf
            match cause := e.cause:
                case ApplicationError():
                    raise cause from None
                case _:
                    raise
