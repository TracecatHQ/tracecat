from __future__ import annotations

import asyncio
import uuid

import pytest

from tracecat.db.exceptions import TenantBusyError
from tracecat.db.org_gate import TENANT_BUSY_RETRY_AFTER_SECONDS, OrgSessionGate

pytestmark = pytest.mark.anyio


async def test_org_over_cap_waits_then_is_busy_while_other_org_proceeds() -> None:
    gate = OrgSessionGate(cap=1, wait_seconds=0.2)
    busy_org, other_org = uuid.uuid4(), uuid.uuid4()
    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder() -> None:
        async with gate.hold(busy_org):
            holder_entered.set()
            await release_holder.wait()

    holder_task = asyncio.create_task(holder())
    await holder_entered.wait()

    async def second_for_busy_org() -> None:
        async with gate.hold(busy_org):
            pytest.fail("The org's second operation must not be admitted")

    async def other_org_operation() -> str:
        async with gate.hold(other_org):
            return "ran"

    loop = asyncio.get_running_loop()
    started = loop.time()
    busy_result, other_result = await asyncio.gather(
        second_for_busy_org(),
        other_org_operation(),
        return_exceptions=True,
    )

    assert isinstance(busy_result, TenantBusyError)
    # It waited before refusing. asyncio may fire the timeout up to one clock
    # resolution early, so allow slack below the 0.2 s wait.
    assert loop.time() - started >= 0.15
    assert busy_result.organization_id == busy_org
    assert busy_result.cap == 1
    assert busy_result.retry_after_seconds == TENANT_BUSY_RETRY_AFTER_SECONDS
    assert other_result == "ran"

    release_holder.set()
    await holder_task


async def test_waiter_is_admitted_when_holder_releases_within_wait() -> None:
    gate = OrgSessionGate(cap=1, wait_seconds=5)
    org = uuid.uuid4()
    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder() -> None:
        async with gate.hold(org):
            holder_entered.set()
            await release_holder.wait()

    holder_task = asyncio.create_task(holder())
    await holder_entered.wait()

    async def waiter() -> str:
        async with gate.hold(org):
            return "ran"

    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    assert not waiter_task.done()

    release_holder.set()
    assert await asyncio.wait_for(waiter_task, timeout=1) == "ran"
    await holder_task


async def test_nested_holds_in_same_task_take_one_unit() -> None:
    # With a cap of 1, a second unit would wait on the task's own unit and
    # time out.
    gate = OrgSessionGate(cap=1, wait_seconds=0.1)
    org = uuid.uuid4()

    async with gate.hold(org):
        async with gate.hold(org):
            async with gate.hold(org):
                pass
        # Leaving the inner holds keeps the outer unit held.
        async with gate.hold(org):
            pass

    assert gate._entries == {}


async def test_reentry_survives_an_interleaved_org() -> None:
    # hold(A) -> hold(B) -> hold(A): the inner A must reuse the outer unit,
    # or with a cap of 1 it would wait on itself and be refused.
    gate = OrgSessionGate(cap=1, wait_seconds=0.1)
    org_a, org_b = uuid.uuid4(), uuid.uuid4()

    async with gate.hold(org_a):
        async with gate.hold(org_b):
            async with gate.hold(org_a):
                async with gate.hold(org_b):
                    pass

    assert gate._entries == {}


async def test_spawned_task_does_not_inherit_the_unit() -> None:
    gate = OrgSessionGate(cap=1, wait_seconds=0.1)
    org = uuid.uuid4()

    async def child() -> None:
        async with gate.hold(org):
            pytest.fail("A spawned task must take its own unit")

    async with gate.hold(org):
        # The child inherits the contextvar copy, but not the unit.
        with pytest.raises(TenantBusyError):
            await asyncio.create_task(child())


async def test_spawned_task_takes_its_own_unit_when_capacity_allows() -> None:
    gate = OrgSessionGate(cap=2, wait_seconds=0.1)
    org = uuid.uuid4()
    child_holding = asyncio.Event()
    release_child = asyncio.Event()

    async def child() -> None:
        async with gate.hold(org):
            child_holding.set()
            await release_child.wait()

    async with gate.hold(org):
        child_task = asyncio.create_task(child())
        await child_holding.wait()

        async def third() -> None:
            async with gate.hold(org):
                pytest.fail("Parent and child hold both units")

        with pytest.raises(TenantBusyError):
            await asyncio.create_task(third())
        release_child.set()
        await child_task


async def test_idle_entries_are_dropped() -> None:
    gate = OrgSessionGate(cap=1, wait_seconds=0.05)
    org = uuid.uuid4()

    async with gate.hold(org):
        assert org in gate._entries
    assert gate._entries == {}

    async def holder(entered: asyncio.Event, release: asyncio.Event) -> None:
        async with gate.hold(org):
            entered.set()
            await release.wait()

    entered, release = asyncio.Event(), asyncio.Event()
    holder_task = asyncio.create_task(holder(entered, release))
    await entered.wait()
    with pytest.raises(TenantBusyError):
        async with gate.hold(org):
            pass
    # A timed-out waiter leaves the holder's entry in place.
    assert org in gate._entries
    release.set()
    await holder_task
    assert gate._entries == {}


async def test_unit_is_released_when_the_block_raises() -> None:
    gate = OrgSessionGate(cap=1, wait_seconds=0.05)
    org = uuid.uuid4()

    with pytest.raises(RuntimeError):
        async with gate.hold(org):
            raise RuntimeError("boom")

    async with gate.hold(org):
        pass


def test_cap_must_be_positive() -> None:
    with pytest.raises(ValueError):
        OrgSessionGate(cap=0, wait_seconds=1)
