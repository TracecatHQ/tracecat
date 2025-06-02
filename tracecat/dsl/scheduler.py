import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import cached_property
from typing import Any, ClassVar, Literal, Self, cast

from pydantic_core import to_json
from temporalio import workflow
from temporalio.exceptions import ApplicationError

from tracecat.concurrency import cooperative
from tracecat.contexts import ctx_stream_id
from tracecat.dsl.common import AdjDst, DSLInput, edge_components_from_dep
from tracecat.dsl.control_flow import GatherArgs, ScatterArgs
from tracecat.dsl.enums import (
    EdgeMarker,
    EdgeType,
    JoinStrategy,
    PlatformAction,
    Sentinel,
    SkipStrategy,
)
from tracecat.dsl.models import (
    ActionErrorInfo,
    ActionStatement,
    ExecutionContext,
    TaskExceptionInfo,
    TaskResult,
)
from tracecat.expressions.common import ExprContext
from tracecat.expressions.core import TemplateExpression
from tracecat.expressions.eval import eval_templated_object
from tracecat.logger import logger
from tracecat.types.exceptions import TaskUnreachable

type SkipToken = Literal["skip"]


class StreamID(str):
    """Hierarchical stream identifier: 'scatter_1:2/scatter_2:0'"""

    __stream_sep: ClassVar[str] = "/"
    __idx_sep: ClassVar[str] = ":"

    @classmethod
    def new(
        cls, scope: str, index: int | SkipToken, *, base_stream_id: Self | None = None
    ) -> Self:
        """Create a stream ID for an inherited scatter item"""
        new_stream = cls(f"{scope}{cls.__idx_sep}{index}")
        if base_stream_id is None:
            return new_stream
        return cls(f"{base_stream_id}{cls.__stream_sep}{new_stream}")

    @classmethod
    def skip(cls, scope: str, *, base_stream_id: Self | None = None) -> Self:
        """Create a stream ID for a skipped scatter"""
        return cls.new(scope, "skip", base_stream_id=base_stream_id)

    @cached_property
    def streams(self) -> list[str]:
        """Get the list of streams in the stream ID"""
        return self.split(self.__stream_sep)

    @cached_property
    def leaf(self) -> tuple[str, int | SkipToken]:
        """Get the leaf stream ID"""
        scope, index, *rest = self.streams[-1].split(self.__idx_sep)
        if rest:
            raise ValueError(f"Invalid stream ID: {self}")
        return scope, int(index) if index != "skip" else "skip"


@dataclass(frozen=True, slots=True)
class Task:
    """Task instance"""

    ref: str
    stream_id: StreamID | None = None  # None for global stream
    scope_id: str | None = None  # None for global scope


@dataclass(frozen=True, slots=True)
class DSLEdge:
    src: str
    """The source task reference"""
    dst: str
    """The destination task reference"""
    type: EdgeType
    """The edge type"""

    stream_id: StreamID | None = None  # None for global stream
    """The stream ID of the edge"""

    def __repr__(self) -> str:
        return (
            f"{self.src}-[{self.type.value}]->{self.dst} ({self.stream_id or 'global'})"
        )


class Counter:
    __slots__ = ("value",)

    def __init__(self, start: int = 0) -> None:
        self.value = start

    def next(self) -> int:
        """Get the next value and increment the counter."""
        current = self.value
        self.value += 1
        return current

    def __repr__(self) -> str:
        return f"Counter(value={self.value})"


class DSLScheduler:
    """Manage only scheduling and control flow of tasks in a topological-like order."""

    skip_strategy: SkipStrategy
    """Decide how to handle tasks that are marked for skipping."""

    def __init__(
        self,
        *,
        executor: Callable[[ActionStatement], Awaitable[Any]],
        dsl: DSLInput,
        skip_strategy: SkipStrategy = SkipStrategy.PROPAGATE,
        context: ExecutionContext,
    ):
        # Static
        self.dsl = dsl
        self.context = context
        self.executor = executor
        self.skip_strategy = skip_strategy
        # self.logger = ctx_logger.get(logger).bind(unit="dsl-scheduler")
        self.logger = logger
        self.tasks: dict[str, ActionStatement] = {}
        """Task definitions"""
        self.adj: dict[str, set[AdjDst]] = defaultdict(set)
        """Adjacency list of task dependencies"""

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

        for task in dsl.actions:
            self.tasks[task.ref] = task
            # This remains the same regardless of error paths, as each error path counts as an indegree
            self.indegrees[Task(task.ref)] = len(task.depends_on)
            for dep_ref in task.depends_on:
                src_ref, edge_type = self._get_edge_components(dep_ref)
                self.adj[src_ref].add((task.ref, edge_type))

        # Scope management
        self.streams: dict[StreamID, ExecutionContext] = {}
        self.stream_hierarchy: dict[StreamID, StreamID | None] = {}
        """Points to the parent stream ID for each stream ID"""
        self.scope_hierarchy: dict[str, str | None] = {}
        """Points to the parent scope ID for each scope ID"""
        self.task_streams: defaultdict[Task, list[StreamID]] = defaultdict(list)
        self.scope_counters: defaultdict[str, Counter] = defaultdict(Counter)
        """Used to create unique stream IDs for each scatter iteration"""
        self.open_streams: dict[Task, int] = {}
        """Used to track the number of scopes that have been closed for an scatter"""

        # # Discover stream boundaries
        # self.scope_boundaries = ScopeAnalyzer(dsl).discover_scope_boundaries()

        # # Pre-compute which tasks need scoping
        # self.scoped_tasks = set()
        # for boundary in self.scope_boundaries.values():
        #     self.scoped_tasks.update(boundary.scoped_tasks)

        self.logger.warning(
            "Scheduler config",
            # scope_boundaries=self.scope_boundaries,
            adj=self.adj,
            indegrees=self.indegrees,
            tasks=self.tasks,
            edges=self.edges,
            scheduler=self,
        )

    def __repr__(self) -> str:
        return to_json(self.__dict__, fallback=str, indent=2).decode()

    async def _handle_error_path(self, task: Task, exc: Exception) -> None:
        ref = task.ref
        import traceback

        # Log the error with traceback
        tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.logger.error(
            "Handling error path",
            ref=ref,
            type=exc.__class__.__name__,
            exc=exc,
        )
        self.logger.error(tb_str)
        # Prune any non-error paths and queue the rest
        non_err_edges: set[DSLEdge] = {
            DSLEdge(src=ref, dst=dst, type=edge_type, stream_id=task.stream_id)
            for dst, edge_type in self.adj[ref]
            if edge_type != EdgeType.ERROR
        }
        """These are outgoing edges that are not error edges."""
        if len(non_err_edges) < len(self.adj[ref]):
            await self._queue_tasks(task, unreachable=non_err_edges)
        else:
            self.logger.info("Task failed with no error paths", ref=ref)
            # XXX: This can sometimes return null because the exception isn't an ApplicationError
            # But rather a ChildWorkflowError or CancelledError
            if isinstance(exc, ApplicationError) and exc.details:
                self.logger.warning(
                    "Task failed with application error",
                    ref=ref,
                    exc=exc,
                    details=exc.details,
                )
                details = exc.details[0]
                if not isinstance(details, dict):
                    self.logger.warning(
                        "Application error details are not a dictionary",
                        ref=ref,
                        details=details,
                    )
                    message = "Application error details are not a dictionary."
                    try:
                        message += f"\nGot: {to_json(details, fallback=str).decode()}"
                    except Exception as e:
                        self.logger.warning(
                            "Couldn't jsonify application error details",
                            ref=ref,
                            error=e,
                        )
                        message += "Couldn't parse error details as json."
                    details = ActionErrorInfo(
                        ref=ref,
                        message=message,
                        type=exc.__class__.__name__,
                    )
                elif all(k in details for k in ("ref", "message", "type")):
                    # Regular action error
                    # it's of shape ActionErrorInfo()
                    try:
                        # This is normal action error
                        details = ActionErrorInfo(**details)
                    except Exception as e:
                        self.logger.warning(
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
                            self.logger.warning(
                                "Couldn't jsonify application error details",
                                ref=ref,
                                error=e,
                            )
                            message += "Couldn't parse error details as json."
                        details = ActionErrorInfo(
                            ref=ref,
                            message=message,
                            type=exc.__class__.__name__,
                        )
                else:
                    # Child workflow error
                    # it's of shape {ref: ActionErrorInfo(), ...}
                    # try get the first element
                    try:
                        val = list(details.values())[0]
                        details = ActionErrorInfo(**val)
                    except Exception as e:
                        self.logger.warning(
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
                            self.logger.warning(
                                "Couldn't jsonify child wf application error details",
                                ref=ref,
                                error=e,
                            )
                            message += "Couldn't parse error details as json."
                        details = ActionErrorInfo(
                            ref=ref,
                            message=message,
                            type=exc.__class__.__name__,
                        )
            else:
                self.logger.warning(
                    "Task failed with non-application error",
                    ref=ref,
                    exc=exc,
                )
                try:
                    message = str(exc)
                except Exception as e:
                    self.logger.warning(
                        "Failed to stringify non-application error",
                        ref=ref,
                        error=e,
                    )
                    message = f"Failed to stringify non-application error: {e}"
                details = ActionErrorInfo(
                    ref=ref,
                    message=message,
                    type=exc.__class__.__name__,
                )
            self.task_exceptions[ref] = TaskExceptionInfo(
                exception=exc, details=details
            )

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

    async def _handle_skip_path(self, task: Task) -> None:
        ref = task.ref
        self.logger.debug("Handling skip path")
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
        scope_id = task.scope_id
        next_tasks = self.adj[ref]
        self.logger.warning(
            "Queueing tasks",
            task=task,
            marked_edges=self.edges,
            visited_tasks=self.completed_tasks,
            next_tasks=next_tasks,
            unreachable=unreachable,
            SCOPE=scope_id,
        )
        async with asyncio.TaskGroup() as tg:
            for next_ref, edge_type in next_tasks:
                self.logger.debug("Processing next task", ref=ref, next_ref=next_ref)
                edge = DSLEdge(
                    src=ref, dst=next_ref, type=edge_type, stream_id=stream_id
                )
                if unreachable and edge in unreachable:
                    self._mark_edge(edge, EdgeMarker.SKIPPED)
                else:
                    self._mark_edge(edge, EdgeMarker.VISITED)
                # Mark the edge as processed
                # Task inherits the current stream
                next_task = Task(ref=next_ref, stream_id=stream_id, scope_id=scope_id)
                # We dynamically add the indegree of the next task to the indegrees dict
                if next_task not in self.indegrees:
                    self.indegrees[next_task] = len(self.tasks[next_ref].depends_on)
                self.indegrees[next_task] -= 1
                self.logger.debug(
                    "Indegree",
                    next_task=next_task,
                    indegree=self.indegrees[next_task],
                )
                if self.indegrees[next_task] == 0:
                    # Schedule the next task
                    self.logger.debug(
                        "Adding task to queue; mark visited", next_ref=next_ref
                    )
                    tg.create_task(self.queue.put(next_task))
        self.logger.trace(
            "Queued tasks",
            visited_tasks=list(self.completed_tasks),
            tasks=list(self.tasks.keys()),
            queue_size=self.queue.qsize(),
        )

    async def _schedule_task(self, task: Task) -> None:
        """Schedule a task for execution."""
        ref = task.ref
        stmt = self.tasks[ref]
        self.logger.warning("Scheduling task", task=task)
        try:
            # 1) Skip propagation (force-skip) takes highest precedence over everything else
            if self._skip_should_propagate(task, stmt):
                self.logger.info("Task should be force-skipped, propagating", task=task)
                if stmt.action == PlatformAction.TRANSFORM_IMPLODE:
                    # If we encounter an gather and we're in an execution stream,
                    # we need to close the stream.
                    self.logger.warning(
                        "Gather in execution stream, skipping", task=task
                    )
                    # We reached the end of the execution stream, so we can return
                    # None to indicate that the stream is complete.
                    # We do not need to queue any downstream tasks.
                    return await self._handle_gather(task, stmt, is_skipping=True)
                return await self._handle_skip_path(task)

            # 2) Then we check if the task is reachable
            if not self._is_reachable(task, stmt):
                self.logger.info("Task cannot proceed, unreachable", task=task)
                raise TaskUnreachable(f"Task {task} is unreachable")

            # 3) Check if the task should self-skip based on its `run_if` condition
            if self._task_should_skip(task, stmt):
                self.logger.info("Task should self-skip", task=task)
                return await self._handle_skip_path(task)

            # 4) If we made it here, the task is reachable and not force-skipped.

            # -- If this is a control flow action (scatter), we need to
            # handle it differently.
            if stmt.action == PlatformAction.TRANSFORM_EXPLODE:
                return await self._handle_scatter(task, stmt)
            # 0) Always handle gather first - its a synchronization barrier that needs
            # to run regardless of dependency skip states
            if stmt.action == PlatformAction.TRANSFORM_IMPLODE:
                return await self._handle_gather(task, stmt)

            # -- Otherwise, time to execute the task!
            # NOTE: If an exception is thrown from this coroutine, it signals that
            # the task failed after all attempts. Adding the exception to the task
            # exceptions set will cause the workflow to fail.
            token = ctx_stream_id.set(task.stream_id)
            try:
                await self.executor(stmt)
            finally:
                ctx_stream_id.reset(token)
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
            self.logger.info("Task completed", task=task)
            self.completed_tasks.add(task)

    async def start(self) -> dict[str, TaskExceptionInfo] | None:
        """Run the scheduler and return any exceptions that occurred."""
        # Instead of explicitly setting the entrypoint, we set all zero-indegree
        # tasks to the queue.
        for task_instance, indegree in self.indegrees.items():
            if indegree == 0:
                self.queue.put_nowait(task_instance)

        pending_tasks: set[asyncio.Task[None]] = set()

        while not self.task_exceptions and (
            not self.queue.empty()
            # NOTE: I'm unsure if this will still hold, as task cardinality is no longer constant
            # or len(self.completed_tasks) < len(self.tasks)
            or pending_tasks
        ):
            self.logger.debug(
                "Waiting for tasks.",
                qsize=self.queue.qsize(),
                n_visited=len(self.completed_tasks),
                n_tasks=len(self.tasks),
                n_pending=len(pending_tasks),
            )

            # Clean up completed tasks
            done_tasks = {t for t in pending_tasks if t.done()}
            pending_tasks.difference_update(done_tasks)

            if not self.queue.empty():
                task_instance = await self.queue.get()
                self.logger.warning("Scheduling task", task=task_instance)
                task = asyncio.create_task(self._schedule_task(task_instance))
                pending_tasks.add(task)
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
                scheduler=self,
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
            visited_tasks=self.completed_tasks,
            tasks=self.tasks,
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

        logger.debug("Check task reachability", task=task, marked_edges=self.edges)
        n_deps = len(stmt.depends_on)
        if n_deps == 0:
            # Root nodes are always reachable
            return True
        elif n_deps == 1:
            logger.debug("Task has only 1 dependency", task=task)
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
        scope_id: StreamID | None = None,
    ) -> bool:
        edge = self._get_edge_by_refs(src_ref_path, dst_ref, scope_id)
        return self.edges[edge] == marker

    def _get_edge_components(self, ref_path: str) -> AdjDst:
        return edge_components_from_dep(ref_path)

    def _get_edge_by_refs(
        self, src_ref_path: str, dst_ref: str, scope_id: StreamID | None = None
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
            src=base_src_ref, dst=dst_ref, type=edge_type, stream_id=scope_id
        )

    def _mark_edge(self, edge: DSLEdge, marker: EdgeMarker) -> None:
        logger.debug("Marking edge", edge=edge, marker=marker)
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

    def _task_should_skip(self, task: Task, stmt: ActionStatement) -> bool:
        """Check if a task should be skipped based on its `run_if` condition."""
        run_if = stmt.run_if
        if run_if is not None:
            context = self.get_context(task.stream_id)
            expr = TemplateExpression(run_if, operand=context)
            self.logger.debug("`run_if` condition", run_if=run_if)
            if not bool(expr.result()):
                self.logger.info("Task `run_if` condition was not met, skipped")
                return True
        return False

    async def _handle_scatter(self, task: Task, stmt: ActionStatement) -> None:
        """
        Handle scatter action with proper stream creation.

        When exploding a collection, len(collection) scopes are created.
        Each stream has a unique stream ID and contains a single item from the collection.

        The tasks in each stream are queued in the order of the collection.

        The tasks in each stream are executed in the order of the collection.

        """
        # Our current location, before creating any new streams
        curr_stream_id = task.stream_id
        curr_scope_id = task.scope_id
        self.logger.info("EXPLODE", task=task)

        # 1) Create a new scope (ALWAYS)
        new_scope_id = task.ref
        self.scope_hierarchy[new_scope_id] = curr_scope_id

        args = ScatterArgs(**stmt.args)
        context = self.get_context(curr_stream_id)
        collection = eval_templated_object(args.collection, operand=context)

        if not isinstance(collection, list):
            raise ApplicationError(
                f"Collection is not a list: {type(collection)}: {collection}",
                non_retryable=True,
            )

        # ALWAYS initialize tracking structures (even for empty collections)
        # This ensures that _handle_gather can find the scatter task in tracking structures

        streams: list[StreamID] = []
        self.task_streams[task] = streams
        # -- SKIP STREAM
        if not collection:
            self.open_streams[task] = 0
            self.logger.warning("Empty collection for scatter", task=task)
            # Handle empty collection - mark scatter as completed and continue
            # The tracking structures are already initialized above with empty values

            new_stream_id = StreamID.skip(task.ref, base_stream_id=curr_stream_id)
            self.stream_hierarchy[new_stream_id] = curr_stream_id
            self.streams[new_stream_id] = {ExprContext.ACTIONS: {}}
            unreachable = {
                DSLEdge(src=task.ref, dst=dst, type=edge_type, stream_id=new_stream_id)
                for dst, edge_type in self.adj[task.ref]
            }
            skip_task = Task(
                ref=task.ref, stream_id=new_stream_id, scope_id=new_scope_id
            )
            self.logger.warning("Queueing skip stream", skip_task=skip_task)
            # Acknowledge the new scope
            return await self._queue_tasks(skip_task, unreachable=unreachable)

        # -- EXECUTION STREAM
        self.logger.warning("Exploding collection", task=task, collection=collection)

        # Create stream for each collection item
        async for i, item in cooperative(enumerate(collection)):
            new_stream_id = StreamID.new(task.ref, i, base_stream_id=curr_stream_id)
            streams.append(new_stream_id)

            # Initialize stream with single item
            self.stream_hierarchy[new_stream_id] = curr_stream_id
            self.streams[new_stream_id] = {
                ExprContext.ACTIONS: {
                    task.ref: TaskResult(
                        result=item,
                        result_typename=type(item).__name__,
                    ),
                }
            }

            # Create tasks for all tasks in this stream
            new_scoped_task = Task(
                ref=task.ref, stream_id=new_stream_id, scope_id=new_scope_id
            )
            self.logger.info(
                "Creating stream",
                item=item,
                stream_id=new_stream_id,
                task=new_scoped_task,
            )
            # This will queue the task for execution stream
            # TODO: Handle unreachable tasks??
            coro = self._queue_tasks(new_scoped_task, unreachable=None)
            _ = asyncio.create_task(coro)

        self.open_streams[task] = len(streams)
        # Get the next tasks to queue
        self.logger.warning(
            "Scatter completed",
            task=task,
            collection_size=len(collection),
            scopes_created=len(streams),
        )

    def get_context(self, stream_id: StreamID | None = None) -> ExecutionContext:
        if stream_id is None:
            self.logger.warning(
                f"Getting global context {to_json(self.context, indent=2).decode()}"
            )
            return self.context
        context = self.streams[stream_id]
        self.logger.warning(
            f"Getting scoped context {stream_id} {to_json(context, indent=2).decode()}"
        )
        return context

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
        self.logger.info("Handling gather", task=task)
        match task:
            # Global execution stream. No scope (no scatter fired)
            case Task(stream_id=None, scope_id=None):
                self.logger.warning(
                    "Gather in global stream, no corresponding scope",
                    task=task,
                    is_skipping=is_skipping,
                )
                if not is_skipping:
                    raise RuntimeError(
                        f"Gather in global stream, no corresponding scope, but not skipping: {task}. User has probably made a mistake"
                    )
                return

            # Global execution stream. Has scope
            case Task(stream_id=None, scope_id=scope_id) if scope_id:
                self.logger.warning("Gather in global stream, doing nothing", task=task)
                # We saw the scatter `scope_id` but we aren't in a scoped execution stream.
                # This means we're in the cleanup flow.

                # The result is known beforehand -- it's an empty array
                # But in which context should we set it?
                # Since stream_id is None, we're in the global context.

                parent_action_context = self._get_action_context(None)
                parent_action_context[task.ref] = TaskResult(
                    result=[], result_typename="list"
                )

                # Now we can exit the scope and queue next tasks
                parent_scope = self.scope_hierarchy[scope_id]
                # Stream ID here is wrong. It should be the parent stream ID.
                next_task = Task(ref=task.ref, stream_id=None, scope_id=parent_scope)
                return await self._queue_tasks(next_task, unreachable=None)

            # Has execution stream + no scope -- scatter never ran
            case Task(stream_id=stream_id, scope_id=None) if stream_id:
                self.logger.warning(
                    "Gather in execution stream, doing nothing", task=task
                )
                raise NotImplementedError(
                    f"Gather in execution stream, no scope, but not skipping: {task}. User has probably made a mistake"
                )

            case Task(stream_id=stream_id, scope_id=scope_id) if (
                stream_id and scope_id and stream_id.endswith(":skip")
            ):
                self.logger.warning("Gather in skip stream. Perform cleanup", task=task)

                parent_stream = self.stream_hierarchy[stream_id]
                parent_action_context = self._get_action_context(parent_stream)
                parent_action_context[task.ref] = TaskResult(
                    result=[], result_typename="list"
                )

                # Now we can exit the scope and queue next tasks
                parent_scope = self.scope_hierarchy[scope_id]
                next_task = Task(
                    ref=task.ref, stream_id=parent_stream, scope_id=parent_scope
                )
                self.logger.warning(
                    "Queueing next task",
                    next_task=next_task,
                    parent_stream=parent_stream,
                    parent_scope=parent_scope,
                )
                return await self._queue_tasks(next_task, unreachable=None)

            # Has execution stream + has scope: default path
            case Task(stream_id=stream_id, scope_id=scope_id) if stream_id and scope_id:
                self.logger.warning(
                    "Default gather path", scope_id=scope_id, stream_id=stream_id
                )
                pass

            case _:
                self.logger.info("Invalid gather state", task=task)
                raise ApplicationError("Invalid gather state")
        # =============
        # We only reach this point if we are in an execution stream.
        # =============

        # We are inside an execution stream. Use gather to synchronize with the other execution streams.
        self.logger.info("Handling gather in execution stream", task=task)

        # What are we doing here?
        # We are in an execution stream. Need to close it now.
        ex_ref, stream_idx = stream_id.leaf
        # This operation should return the parent stream
        parent_stream_id = self.stream_hierarchy[stream_id]
        parent_scope_id = self.scope_hierarchy[scope_id]
        parent_ex_task = Task(
            # This is a scope mismatch?
            ref=ex_ref,
            stream_id=parent_stream_id,
            scope_id=parent_scope_id,
        )

        # Close the stream regardless of whether we skipped our way to this point.
        self.logger.warning(
            "Closing stream",
            task=task,
            parent_ex_task=parent_ex_task,
            open_streams=to_json(self.open_streams, indent=2).decode(),
        )
        self.logger.warning(to_json(self.open_streams, indent=2).decode())
        self.open_streams[parent_ex_task] -= 1
        if is_skipping:
            self.logger.warning("Skipped gather in execution stream", task=task)
            return

        # Here onwards, we are not skipping.
        # This means we must compute a return value for the gather.
        # We should only compute the items to store if we aren't skipping
        args = GatherArgs(**stmt.args)
        current_context = self.get_context(stream_id)
        items = TemplateExpression(args.items, operand=current_context).result()

        # Once we have the item, we go down 1 level in the stream hierarchy
        # and set the item as the result of the action in that stream
        # We do this for each item in the collection
        parent_action_context = self._get_action_context(parent_stream_id)

        # We set the item as the result of the action in that stream
        # We should actually be placing this in parent.result[i]

        im_ref = task.ref

        # Set result array if this is the first stream
        if im_ref not in parent_action_context:
            # NOTE: This block is executed by the first execution stream that finishes.
            # We need to initialize the result with the cardinality of the scatter
            # This is the number of execution streams that will be synchronized by this gather
            size = len(self.task_streams[parent_ex_task])
            result = [Sentinel.IMPLODE_UNSET for _ in range(size)]
            parent_action_context[im_ref] = TaskResult(
                result=result,
                result_typename=type(result).__name__,
            )

        # Only add  if we didn't skip
        parent_action_context[im_ref]["result"][stream_idx] = items
        self.logger.info(
            "Set item as result",
            task=task,
            scope_id=stream_id,
            parent_action_context=parent_action_context,
        )

        if self.open_streams[parent_ex_task] == 0:
            # NOTE: This block is executed by the last execution stream.
            self.logger.warning(
                "FINALIZING IMPLODE",
                task=task,
                parent_action_context=parent_action_context,
                args=args,
            )
            # We have closed all execution streams for this scatter. The gather is now complete.
            # Apply filtering if requested

            # Inline filter for gather operation.
            # Keeps items unless drop_nulls is True and item is None.
            # Automatically remove unset values (Sentinel.IMPLODE_UNSET).
            final_result = [
                item
                for item in cast(list[Any], parent_action_context[im_ref]["result"])
                if not (
                    (item == Sentinel.IMPLODE_UNSET)
                    or (args.drop_nulls and item is None)
                )
            ]
            logger.warning(
                "IMPLODE FILTERED RESULT",
                task=task,
                final_result=final_result,
                args=args,
            )

            # Update the result with the filtered version
            parent_action_context[im_ref].update(result=final_result)

            self.logger.warning(
                "Gather complete. Go back up to parent stream",
                task=task,
                parent_scope_id=parent_stream_id,
            )
            parent_im_task = Task(
                ref=im_ref, stream_id=parent_stream_id, scope_id=parent_scope_id
            )
            # TODO: Handle unreachable tasks??
            await self._queue_tasks(parent_im_task, unreachable=None)
        else:
            self.logger.info(
                "The execution stream is complete but the gather isn't.",
                task=task,
                remaining_open_scopes=self.open_streams[parent_ex_task],
            )

    def _get_action_context(
        self, stream_id: StreamID | None = None
    ) -> dict[str, TaskResult]:
        context = self.get_context(stream_id)
        return cast(dict[str, TaskResult], context[ExprContext.ACTIONS])

    def get_scope_aware_action_result(
        self, action_ref: str, scope_id: StreamID | None = None
    ) -> Any | None:
        """
        Resolve an action expression in a stream-aware manner.

        Traverses from the current stream up through the hierarchy until it finds
        the action result or reaches the global stream.

        Args:
            action_ref: The action reference to resolve (e.g., "webhook", "transform_1")
            scope_id: The current stream ID to start resolution from. If None, uses global stream.

        Returns:
            The action result if found, None otherwise.

        Example:
            # In a scoped context
            result = scheduler.resolve_action_expression("webhook", current_scope_id)

            # In global context
            result = scheduler.resolve_action_expression("webhook")

        Performance
        -----------
        WE SHOULD TOTALLY CACHE THIS FUNCTION BTW
        """
        self.logger.debug(
            "Resolving action expression",
            action_ref=action_ref,
            scope_id=scope_id,
            available_scopes=list(self.streams.keys()),
        )

        # Start from the current stream and work upwards
        current_scope = scope_id

        while current_scope is not None:
            # Check if the action exists in the current stream
            scope_context = self.streams.get(current_scope)
            if scope_context is not None:
                actions_context = scope_context.get(ExprContext.ACTIONS, {})
                if action_ref in actions_context:
                    self.logger.debug(
                        "Found action in stream",
                        action_ref=action_ref,
                        scope_id=current_scope,
                        result=actions_context[action_ref],
                    )
                    return actions_context[action_ref]

            # Move to parent stream
            current_scope = self.stream_hierarchy.get(current_scope)
            self.logger.trace(
                "Moving to parent stream",
                action_ref=action_ref,
                parent_scope=current_scope,
            )

        # Finally, check the global context
        global_actions = self.context.get(ExprContext.ACTIONS, {})
        if action_ref in global_actions:
            self.logger.debug(
                "Found action in global context",
                action_ref=action_ref,
                result=global_actions[action_ref],
            )
            return global_actions[action_ref]

        # Action not found in any stream
        self.logger.warning(
            "Action not found in any stream",
            action_ref=action_ref,
            scope_id=scope_id,
            available_global_actions=list(global_actions.keys()),
            available_scopes=list(self.streams.keys()),
        )
        return None
