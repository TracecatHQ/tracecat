"""Reconcile the semantic-search schedule whenever a DSL worker starts."""

from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from tracecat.search.indexing_workflow import SearchIndexDispatcher

SCHEDULE_ID = "semantic-search-dispatch-v1"


async def ensure_search_schedule(client: Client, task_queue: str) -> None:
    """Idempotent across replicas; preserve an operator's schedule pause."""
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            SearchIndexDispatcher.run,
            None,
            id="semantic-search-dispatch",
            task_queue=task_queue,
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=10))]
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
    try:
        await client.create_schedule(SCHEDULE_ID, schedule)
    except ScheduleAlreadyRunningError:

        def update(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule.state = current.description.schedule.state
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(SCHEDULE_ID).update(update)
