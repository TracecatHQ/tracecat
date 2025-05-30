import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Self

from pydantic_core import to_json
from temporalio import workflow
from temporalio.exceptions import ApplicationError

from tracecat.contexts import ctx_logger
from tracecat.dsl.common import AdjDst, DSLInput, edge_components_from_dep
from tracecat.dsl.control_flow import (
    ExplodeArgs,
    ScopeAnalyzer,
)
from tracecat.dsl.enums import (
    EdgeMarker,
    EdgeType,
    JoinStrategy,
    PlatformAction,
    SkipStrategy,
)
from tracecat.dsl.models import (
    ActionErrorInfo,
    ActionStatement,
    ExecutionContext,
    TaskExceptionInfo,
)
from tracecat.expressions.core import TemplateExpression
from tracecat.logger import logger
from tracecat.types.exceptions import TaskUnreachable


class ScopeID(str):
    """Hierarchical scope identifier: 'root/explode_1/item_2'"""

    @classmethod
    def new(cls, explode_ref: str, item_index: int) -> Self:
        """Create a scope ID for an exploded item"""
        return cls(f"{explode_ref}/{item_index}")

    @classmethod
    def global_scope(cls) -> Self:
        """Return the global scope identifier"""
        return cls("global")


@dataclass(frozen=True, slots=True)
class Task:
    """Unique identifier for a task instance within a specific scope"""

    ref: str
    scope_id: ScopeID | None = None  # None for global scope


@dataclass(frozen=True, slots=True)
class DSLEdge:
    src: str
    dst: str
    type: EdgeType

    def __repr__(self) -> str:
        return f"{self.src}-[{self.type.value}]->{self.dst}"


class DSLScheduler:
    """Manage only scheduling and control flow of tasks in a topological-like order."""

    _queue_wait_timeout = 1
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
        self.dsl = dsl
        self.context = context
        self.executor = executor
        self.skip_strategy = skip_strategy
        self.logger = ctx_logger.get(logger).bind(unit="dsl-scheduler")

        # Const: Definitions
        self.tasks: dict[str, ActionStatement] = {}
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
        # Const. Map task ref to its dependencies
        self.adj: dict[str, set[AdjDst]] = defaultdict(set)

        for task in dsl.actions:
            self.tasks[task.ref] = task
            # This remains the same regardless of error paths, as each error path counts as an indegree
            self.indegrees[Task(task.ref)] = len(task.depends_on)
            for dep_ref in task.depends_on:
                src_ref, edge_type = self._get_edge_components(dep_ref)
                self.adj[src_ref].add((task.ref, edge_type))

        # Scope management
        self.scopes: dict[ScopeID, dict[str, Any]] = {}
        self.scope_hierarchy: dict[ScopeID, ScopeID | None] = {}
        self.explode_contexts: dict[str, list[ScopeID]] = {}  # explode_ref -> scope_ids

        # Discover scope boundaries
        self.scope_boundaries = ScopeAnalyzer(dsl).discover_scope_boundaries()

        # Pre-compute which tasks need scoping
        self.scoped_tasks = set()
        for boundary in self.scope_boundaries.values():
            self.scoped_tasks.update(boundary.scoped_tasks)

        self.logger.warning(
            "Scheduler config",
            scope_boundaries=self.scope_boundaries,
            scoped_tasks=self.scoped_tasks,
            adj=self.adj,
            indegrees=self.indegrees,
            tasks=self.tasks,
            edges=self.edges,
            scheduler=self,
        )

    def __repr__(self) -> str:
        return to_json(self.__dict__, fallback=str, indent=2).decode()

    async def _handle_error_path(self, ref: str, exc: Exception) -> None:
        self.logger.debug(
            "Handling error path", ref=ref, type=exc.__class__.__name__, exc=exc
        )
        # Prune any non-error paths and queue the rest
        non_err_edges: set[DSLEdge] = {
            DSLEdge(src=ref, dst=dst, type=edge_type)
            for dst, edge_type in self.adj[ref]
            if edge_type != EdgeType.ERROR
        }
        if len(non_err_edges) < len(self.adj[ref]):
            await self._queue_tasks(ref, unreachable=non_err_edges)
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

    async def _handle_success_path(self, ref: str) -> None:
        self.logger.debug("Handling success path", ref=ref)
        # Prune any non-success paths and queue the rest
        non_ok_edges: set[DSLEdge] = {
            DSLEdge(src=ref, dst=dst, type=edge_type)
            for dst, edge_type in self.adj[ref]
            if edge_type != EdgeType.SUCCESS
        }
        await self._queue_tasks(ref, unreachable=non_ok_edges)

    async def _handle_skip_path(self, task_ref: str) -> None:
        self.logger.debug("Handling skip path")
        # If we skip a task, we need to mark all its outgoing edges as skipped
        all_edges: set[DSLEdge] = {
            DSLEdge(src=task_ref, dst=dst, type=edge_type)
            for dst, edge_type in self.adj[task_ref]
        }
        await self._queue_tasks(task_ref, unreachable=all_edges)

    async def _queue_tasks(
        self, ref: str, unreachable: set[DSLEdge] | None = None
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
        next_tasks = self.adj[ref]
        self.logger.warning(
            "Queueing tasks",
            ref=ref,
            marked_edges=self.edges,
            visited_tasks=self.completed_tasks,
            next_tasks=next_tasks,
            unreachable=unreachable,
        )
        async with asyncio.TaskGroup() as tg:
            for next_ref, edge_type in next_tasks:
                self.logger.debug("Processing next task", ref=ref, next_ref=next_ref)
                edge = DSLEdge(src=ref, dst=next_ref, type=edge_type)
                if unreachable and edge in unreachable:
                    self._mark_edge(edge, EdgeMarker.SKIPPED)
                else:
                    self._mark_edge(edge, EdgeMarker.VISITED)
                # Mark the edge as processed
                next_task = Task(next_ref)
                self.indegrees[next_task] -= 1
                self.logger.debug(
                    "Indegree", next_ref=next_ref, indegree=self.indegrees[next_task]
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
        task_defn = self.tasks[ref]
        self.logger.warning("Scheduling task", task=task)
        try:
            # 1) Skip propagation (force-skip) takes highest precedence over reachability
            if self._skip_should_propagate(task_defn):
                self.logger.info("Task should be force-skipped, propagating", ref=ref)
                return await self._handle_skip_path(ref)

            # 2) Then we check if the task is reachable - i.e. do we have
            # enough successful paths to reach this task?
            if not self._is_reachable(task_defn):
                self.logger.info("Task cannot proceed, unreachable", ref=ref)
                raise TaskUnreachable(f"Task {task} is unreachable")

            # 3) At this point the task is reachable and not force-skipped.
            # Check if the task should self-skip based on its `run_if` condition
            if self._task_should_skip(task_defn):
                self.logger.info("Task should self-skip", ref=ref)
                return await self._handle_skip_path(ref)

            # 4) If we made it here, the task is reachable and not force-skipped.

            # -- If this is a control flow action (explode/implode), we need to
            # handle it differently.
            if task_defn.action == PlatformAction.TRANSFORM_EXPLODE:
                # Handle explode with proper scope creation
                return await self._handle_explode(task_defn)

            if task_defn.action == PlatformAction.TRANSFORM_IMPLODE:
                # Handle implode with synchronization
                return await self._handle_implode(task_defn)

            # -- Otherwise, time to execute the task!
            # NOTE: If an exception is thrown from this coroutine, it signals that
            # the task failed after all attempts. Adding the exception to the task
            # exceptions set will cause the workflow to fail.
            await self.executor(task_defn)
            # NOTE: Moved this here to handle single success path
            await self._handle_success_path(ref)
        except Exception as e:
            kind = e.__class__.__name__
            non_retryable = getattr(e, "non_retryable", True)
            self.logger.warning(
                f"{kind} in DSLScheduler", ref=ref, error=e, non_retryable=non_retryable
            )
            await self._handle_error_path(ref, e)
        finally:
            # 5) Regardless of the outcome, the task is now complete
            self.logger.info("Task completed", task=task)
            self.completed_tasks.add(task)

    async def start(self) -> dict[str, TaskExceptionInfo] | None:
        """Run the scheduler and return any exceptions that occurred."""
        # Instead of explicitly setting the entrypoint, we set all zero-indegree
        # tasks to the queue.
        for task_ref, indegree in self.indegrees.items():
            if indegree == 0:
                self.queue.put_nowait(task_ref)

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
                task_ref = await self.queue.get()
                task = asyncio.create_task(self._schedule_task(task_ref))
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

    def _is_reachable(self, task: ActionStatement) -> bool:
        """Check whether a task is reachable based on its dependencies' outcomes.

        Args:
            task_ref (str): The reference of the task to check

        Returns:
            bool: True if the task is reachable, False otherwise

        Raises:
            ValueError: If the join strategy is invalid
        """

        logger.debug("Check task reachability", task=task, marked_edges=self.edges)
        n_deps = len(task.depends_on)
        if n_deps == 0:
            # Root nodes are always reachable
            return True
        elif n_deps == 1:
            logger.debug("Task has only 1 dependency", task=task)
            # If there's only 1 dependency, the node is reachable only if the
            # dependency was successful ignoring the join strategy.
            dep_ref = task.depends_on[0]
            return self._edge_has_marker(dep_ref, task.ref, EdgeMarker.VISITED)
        else:
            # If there's more than 1 dependency, the node is reachable depending
            # on the join strategy
            n_success_paths = sum(
                self._edge_has_marker(dep_ref, task.ref, EdgeMarker.VISITED)
                for dep_ref in task.depends_on
            )
            if task.join_strategy == JoinStrategy.ANY:
                return n_success_paths > 0
            if task.join_strategy == JoinStrategy.ALL:
                return n_success_paths == n_deps
            raise ValueError(f"Invalid join strategy: {task.join_strategy}")

    def _edge_has_marker(
        self, src_ref_path: str, dst_ref: str, marker: EdgeMarker
    ) -> bool:
        edge = self._get_edge_by_refs(src_ref_path, dst_ref)
        return self.edges[edge] == marker

    def _get_edge_components(self, ref_path: str) -> AdjDst:
        return edge_components_from_dep(ref_path)

    def _get_edge_by_refs(self, src_ref_path: str, dst_ref: str) -> DSLEdge:
        """Get an edge by its source and destination references.

        Args:
            src_ref_path: The source reference path
            dst_ref: The destination reference

        Returns:
            The edge
        """
        base_src_ref, edge_type = self._get_edge_components(src_ref_path)
        return DSLEdge(src=base_src_ref, dst=dst_ref, type=edge_type)

    def _mark_edge(self, edge: DSLEdge, marker: EdgeMarker) -> None:
        logger.debug("Marking edge", edge=edge, marker=marker)
        self.edges[edge] = marker

    def _skip_should_propagate(self, task: ActionStatement) -> bool:
        """Check if a task's skip should propagate to its dependents.

        Args:
            task: The task to check

        Returns:
            bool: True if the task's skip should propagate, False otherwise
        """
        # If all of a task's dependencies are skipped, then the task should be skipped
        # regardless of its `run_if` condition.
        deps = task.depends_on
        if not deps:
            return False
        return all(
            self._edge_has_marker(dep_ref, task.ref, EdgeMarker.SKIPPED)
            for dep_ref in deps
        )

    def _task_should_skip(self, task: ActionStatement) -> bool:
        """Check if a task should be skipped based on its `run_if` condition.

        Args:
            task: The task to check

        Returns:
            bool: True if the task should be skipped, False otherwise
        """
        # Evaluate the `run_if` condition
        if task.run_if is not None:
            expr = TemplateExpression(task.run_if, operand=self.context)
            self.logger.debug("`run_if` condition", task_run_if=task.run_if)
            if not bool(expr.result()):
                self.logger.info("Task `run_if` condition was not met, skipped")
                return True
        return False

    async def _handle_explode(self, task_defn: ActionStatement) -> None:
        """
        Handle explode action with proper scope creation.

        When exploding a collection, len(collection) scopes are created.
        Each scope has a unique scope ID and contains a single item from the collection.

        The tasks in each scope are queued in the order of the collection.

        The tasks in each scope are executed in the order of the collection.

        """
        ref = task_defn.ref
        self.logger.info("EXPLODE", ref=ref)

        args = ExplodeArgs(**task_defn.args)
        collection = TemplateExpression(args.collection, operand=self.context).result()

        if not isinstance(collection, list):
            raise ApplicationError("Collection is not a list", non_retryable=True)

        if not collection:
            # TODO: proper handling
            self.logger.warning("Empty collection for explode", ref=ref)
            # Handle empty collection - mark explode as completed and continue
            # await self._handle_success_path(ref)
            return

        # Get scope boundary information
        boundary = self.scope_boundaries.get(ref)
        if not boundary:
            self.logger.warning("No scope boundary found for explode", ref=ref)
            # Fall back to legacy behavior for now
            await self._handle_success_path(ref)
            return

        scopes: list[ScopeID] = []

        # Create scope for each collection item
        for i, item in enumerate(collection):
            scope_id = ScopeID.new(ref, i)
            self.logger.info("Creating scope", scope_id=scope_id, item=item)
            scopes.append(scope_id)

            # Initialize scope with iterator variable
            self.scopes[scope_id] = {args.to: item}
            # NOTE: We probably want to manage this with a contextvar
            # so we can stack this meaningfully
            self.scope_hierarchy[scope_id] = None  # Global scope parent

            # Create tasks for all tasks in this scope
            for ref in boundary.scoped_tasks:
                task_defn = self.tasks[ref]
                task = Task(ref, scope_id)
                # Calculate scope-aware indegrees (same as original for now)
                self.indegrees[task] = len(task_defn.depends_on)

        # Track explode context for implode synchronization
        self.explode_contexts[ref] = scopes

        # Queue initial tasks in each scope
        self.logger.warning("Queueing initial tasks", ref=ref, scopes=scopes)
        for scope_id in scopes:
            # Find tasks that should be queued (those with no dependencies within scope)
            for task_ref in boundary.scoped_tasks:
                task = Task(task_ref, scope_id)
                if self.indegrees.get(task, 0) == 0:
                    # Queue this task instance (for now, just queue the task ref)
                    self.queue.put_nowait(task)

        self.logger.warning(
            "Explode completed",
            ref=ref,
            collection_size=len(collection),
            scopes_created=len(scopes),
            scheduler=self,
        )

    async def _handle_implode(self, task: ActionStatement) -> None:
        """Handle implode with proper synchronization"""
        self.logger.info("IMPLODE", ref=task.ref)

        # For now, implement a basic implode that just executes the task
        # TODO: Add proper result collection from exploded scopes

        # Find the corresponding explode operation
        explode_ref = self._find_upstream_explode(task.ref)
        if not explode_ref:
            self.logger.warning("Implode without corresponding explode", ref=task.ref)
            # Execute normally
            await self.executor(task)
            await self._handle_success_path(task.ref)
            return

        exploded_scopes = self.explode_contexts.get(explode_ref, [])

        # For now, just execute the implode task normally
        # TODO: Check if all exploded instances are complete and collect results
        await self.executor(task)
        await self._handle_success_path(task.ref)

        self.logger.info(
            "Implode completed",
            ref=task.ref,
            explode_ref=explode_ref,
            scopes_processed=len(exploded_scopes),
        )

    def _find_upstream_explode(self, task_ref: str) -> str | None:
        """Find the explode task that feeds into this task"""
        for explode_ref, boundary in self.scope_boundaries.items():
            if boundary.implode_ref == task_ref:
                return explode_ref
        return None
