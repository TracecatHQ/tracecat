"""Schedule registration must never take the shared DSL worker down."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from temporalio.client import Client, ScheduleAlreadyRunningError

from tracecat.search import indexing_schedule as schedule
from tracecat.search.embeddings.types import EmbeddingErrorCode
from tracecat.search.indexing_types import IndexingOutcome, IndexingProgress
from tracecat.search.types import SearchErrorCode


@pytest.mark.parametrize(
    "outcome", [*IndexingOutcome, *SearchErrorCode, *EmbeddingErrorCode]
)
def test_progress_preserves_existing_wire_values(outcome):
    progress = IndexingProgress(outcome=outcome)
    assert progress.model_dump(mode="json")["outcome"] == outcome.value
    assert IndexingProgress.model_validate_json(progress.model_dump_json()) == progress


def test_progress_rejects_unknown_outcomes():
    with pytest.raises(ValidationError):
        IndexingProgress.model_validate({"outcome": "synthetic typo"})


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["create", "update", "timeout"])
async def test_schedule_failure_retries_without_exiting_worker(monkeypatch, failure):
    client = Mock(spec=Client)
    recovered = asyncio.Event()
    calls = 0

    async def create(*args):
        nonlocal calls
        calls += 1
        if failure == "update":
            raise ScheduleAlreadyRunningError()
        if calls == 1:
            if failure == "timeout":
                await asyncio.Event().wait()
            raise RuntimeError("synthetic private RPC details")
        recovered.set()

    async def update(*args):
        if calls == 1:
            raise RuntimeError("synthetic private update details")
        recovered.set()

    client.create_schedule = AsyncMock(side_effect=create)
    client.get_schedule_handle.return_value.update = AsyncMock(side_effect=update)
    monkeypatch.setattr(schedule, "_RECONCILE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(schedule, "_RETRY_DELAY_SECONDS", 0)
    warning = Mock()
    monkeypatch.setattr(schedule.logger, "warning", warning)
    async with schedule.search_schedule_lifespan(client, "synthetic-queue"):
        await asyncio.wait_for(recovered.wait(), 1)
    assert calls == 2
    warning.assert_called_once_with(
        "Search schedule reconciliation failed; retrying in 30 seconds",
        error_type="TimeoutError" if failure == "timeout" else "RuntimeError",
    )


@pytest.mark.anyio
async def test_worker_shutdown_cancels_hung_schedule_registration(monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def hung(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(schedule, "ensure_search_schedule", hung)
    async with asyncio.timeout(1):
        async with schedule.search_schedule_lifespan(
            Mock(spec=Client), "synthetic-queue"
        ):
            await entered.wait()
        assert cancelled.is_set()
