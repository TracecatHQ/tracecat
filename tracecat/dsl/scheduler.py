import asyncio
from collections import defaultdict
from collections.abc import Coroutine
from typing import Any

from temporalio.exceptions import ApplicationError

from tracecat.contexts import ctx_logger
from tracecat.dsl.common import AdjDst, DSLEdge, DSLInput, get_edge_components
from tracecat.dsl.enums import EdgeMarker, EdgeType, JoinStrategy, SkipStrategy
from tracecat.dsl.models import ActionStatement, ArgsT, DSLContext
from tracecat.expressions.core import TemplateExpression
from tracecat.logger import logger


class DSLScheduler:
    """Manage only scheduling of tasks in a topological-like order."""

    _queue_wait_timeout = 1
    skip_strategy: SkipStrategy
    """Decide how to handle tasks that are marked for skipping."""

    def __init__(
        self,
        *,
        executor: Coroutine[Any, Any, Any],
        dsl: DSLInput,
        skip_strategy: SkipStrategy = SkipStrategy.PROPAGATE,
        context: DSLContext,
    ):
        self.dsl = dsl
        self.context = context
        self.tasks: dict[str, ActionStatement] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.indegrees: dict[str, int] = {}
        self.adj: dict[str, set[AdjDst]] = defaultdict(set)
        """Graph connectivity information."""
        self.completed_tasks: set[str] = set()
        self.edges: dict[DSLEdge, EdgeMarker] = {}
        """Marked edges are used to track the state of edges in the graph.

        When a task succeeds/fails, we need to update all its outgoing edges.
        This is because we are eliminating node executions from the graph
        - if task `a` succeeds
            - edges `a.error -> b` should be marked as skipped
            - all child edges `a -> <child>` should be marked as completed (taken?)
        - if task `a` fails
            - edges `a.success -> b` should be marked as skipped
            - edges `a.error -> b` should be marked as completed

        """
        self.skip_strategy = skip_strategy
        self.task_exceptions: dict[str, BaseException] = {}

        self.executor = executor
        self.logger = ctx_logger.get(logger).bind(unit="dsl-scheduler")
        self.logger.warning("Context obj", id_=id(self.context))

        for task in dsl.actions:
            self.tasks[task.ref] = task
            # This remains the same regardless of error paths, as each error path counts as an indegree
            self.indegrees[task.ref] = len(task.depends_on)
            for dep_ref in task.depends_on:
                src_ref, edge_type = self._get_edge_components(dep_ref)
                self.adj[src_ref].add((task.ref, edge_type))

        self.logger.debug(
            "Scheduler config",
            adj=self.adj,
            indegrees=self.indegrees,
            tasks=self.tasks,
            edges=self.edges,
        )

    async def _handle_error_path(self, ref: str, exc: BaseException) -> None:
        self.logger.debug("Handling error path", ref=ref)
        # Prune any non-error paths and queue the rest
        non_err_edges: set[DSLEdge] = {
            DSLEdge(src=ref, dst=dst, type=edge_type)
            for dst, edge_type in self.adj[ref]
            if edge_type != EdgeType.ERROR
        }
        if len(non_err_edges) < len(self.adj[ref]):
            await self._queue_tasks(ref, unreachable=non_err_edges)
        else:
            self.logger.error("Task failed with no error paths", ref=ref)
            self.task_exceptions[ref] = exc

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
        self.logger.debug(
            "Queueing tasks",
            ref=ref,
            marked_edges=self.edges,
            visited_tasks=self.completed_tasks,
            next_tasks=next_tasks,
            unreachable=unreachable,
        )
        async with asyncio.TaskGroup() as tg:
            for next_ref, edge_type in next_tasks:
                self.logger.warning("Processing next task", ref=ref, next_ref=next_ref)
                edge = DSLEdge(src=ref, dst=next_ref, type=edge_type)
                if unreachable and edge in unreachable:
                    self._mark_edge(edge, EdgeMarker.SKIPPED)
                else:
                    self._mark_edge(edge, EdgeMarker.VISITED)
                # Mark the edge as processed
                self.indegrees[next_ref] -= 1
                self.logger.debug(
                    "Indegree", next_ref=next_ref, indegree=self.indegrees[next_ref]
                )
                if self.indegrees[next_ref] == 0:
                    # Schedule the next task
                    self.logger.debug(
                        "Adding task to queue; mark visited", next_ref=next_ref
                    )
                    tg.create_task(self.queue.put(next_ref))
        self.logger.warning(
            "Queued tasks",
            visited_tasks=list(self.completed_tasks),
            tasks=list(self.tasks.keys()),
            queue_size=self.queue.qsize(),
        )

    async def _schedule_task(self, ref: str) -> None:
        """Schedule a task for execution."""
        task = self.tasks[ref]
        try:
            if self._should_skip_task(task):
                return await self._handle_skip_path(ref)
            # NOTE: If an exception is thrown from this coroutine, it signals that
            # the task failed after all attempts. Adding the exception to the task
            # exceptions set will cause the workflow to fail.
            await self.executor(task)  # type: ignore
        except Exception as e:
            kind = e.__class__.__name__
            non_retryable = getattr(e, "non_retryable", True)
            self.logger.error(
                f"{kind} in DSLScheduler", ref=ref, error=e, non_retryable=non_retryable
            )
            await self._handle_error_path(ref, e)
        else:
            await self._handle_success_path(ref)
        finally:
            self.logger.info("Task completed", ref=ref)
            self.completed_tasks.add(ref)

    async def start(self) -> None:
        """Run the scheduler in dynamic mode."""
        self.queue.put_nowait(self.dsl.entrypoint.ref)
        while not self.task_exceptions and (
            not self.queue.empty() or len(self.completed_tasks) < len(self.tasks)
        ):
            self.logger.debug(
                "Waiting for tasks.",
                qsize=self.queue.qsize(),
                n_visited=len(self.completed_tasks),
                n_tasks=len(self.tasks),
            )
            try:
                task_ref = await asyncio.wait_for(
                    self.queue.get(), timeout=self._queue_wait_timeout
                )
            except TimeoutError:
                continue

            asyncio.create_task(self._schedule_task(task_ref))
        if self.task_exceptions:
            self.logger.error(
                "DSLScheduler got task exceptions, stopping...",
                n_exceptions=len(self.task_exceptions),
                exceptions=self.task_exceptions,
                n_visited=len(self.completed_tasks),
                n_tasks=len(self.tasks),
            )
            raise ApplicationError(
                "Task exceptions occurred", *self.task_exceptions.values()
            )
        self.logger.info(
            "All tasks completed",
            visited_tasks=self.completed_tasks,
            tasks=self.tasks,
        )

    def _task_reachable(self, task_ref: str) -> bool:
        """Check whether a task is reachable based on its dependencies' states.

        Under JoinStrategy.ANY, a task is considered reachable if:
        - Any of its dependencies completed successfully

        Under JoinStrategy.ALL, a task is considered reachable if:
        - All of its dependencies completed successfully

        Args:
            task_ref (str): The reference of the task to check

        Returns:
            bool: True if the task is reachable, False otherwise
        """

        task = self.tasks[task_ref]
        logger.debug("Check task reachability", task=task, marked_edges=self.edges)
        if not task.depends_on:
            # Covers any root nodes
            return True
        join_strategy = JoinStrategy.ANY

        # Logically, this function should check that there was at least
        # one successful path (edge) taken to the task.
        def edge_visited(dep_ref: str) -> bool:
            # dep_ref might have a path, so we need to check for that
            src_ref, edge_type = self._get_edge_components(dep_ref)
            edge = DSLEdge(src=src_ref, dst=task_ref, type=edge_type)
            return self.edges[edge] == EdgeMarker.VISITED

        outcomes = {parent: edge_visited(parent) for parent in task.depends_on}
        logger.debug("Check outcomes", outcomes=outcomes, edges=self.edges)
        if join_strategy == JoinStrategy.ANY:
            return any(outcomes.values())
        elif join_strategy == JoinStrategy.ALL:
            return all(outcomes.values())
        raise ValueError(f"Unknown join strategy: {join_strategy}")

    def _get_edge_components(self, dep_ref: str) -> AdjDst:
        return get_edge_components(dep_ref)

    def _mark_edge(self, edge: DSLEdge, marker: EdgeMarker) -> None:
        logger.debug("Marking edge", edge=edge, marker=marker)
        self.edges[edge] = marker

    def _should_skip_task(self, task: ActionStatement[ArgsT]) -> bool:
        if not self._task_reachable(task.ref):
            self.logger.info("Task is unreachable, skipped")
            return True
        # Evaluate the `run_if` condition
        if task.run_if is not None:
            expr = TemplateExpression(task.run_if, operand=self.context)
            self.logger.debug("`run_if` condition", task_run_if=task.run_if)
            if not bool(expr.result()):
                self.logger.info("Task `run_if` condition was not met, skipped")
                return True
        return False
