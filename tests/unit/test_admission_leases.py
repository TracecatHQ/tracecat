"""Lease store scripts against a real Redis."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as redis

from tracecat.admission.leases import LeaseStore, Waiter, lease_key, waiter_key
from tracecat.admission.types import Admitted, Busy, ConcurrencyResource

ACTIONS = ConcurrencyResource.ACTIONS


@pytest.fixture
async def client(redis_server: None) -> AsyncIterator[redis.Redis]:
    del redis_server
    redis_client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    yield redis_client
    await redis_client.aclose()


@pytest.fixture
def store(client: redis.Redis) -> LeaseStore:
    return LeaseStore(client)


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.mark.anyio
async def test_acquire_admits_under_limit_and_refuses_at_limit(
    store: LeaseStore, org_id: uuid.UUID
) -> None:
    first = await store.try_acquire(
        org_id, ACTIONS, "a", units=1, limit=2, ttl_seconds=30
    )
    second = await store.try_acquire(
        org_id, ACTIONS, "b", units=1, limit=2, ttl_seconds=30
    )
    third = await store.try_acquire(
        org_id, ACTIONS, "c", units=1, limit=2, ttl_seconds=30
    )

    assert first == Admitted(in_use=1, limit=2)
    assert second == Admitted(in_use=2, limit=2)
    assert third == Busy(in_use=2, limit=2)
    assert await store.in_use(org_id, ACTIONS) == 2


@pytest.mark.anyio
async def test_units_are_reserved_together(
    store: LeaseStore, client: redis.Redis, org_id: uuid.UUID
) -> None:
    wide = await store.try_acquire(
        org_id, ACTIONS, "wide", units=3, limit=4, ttl_seconds=30
    )
    too_wide = await store.try_acquire(
        org_id, ACTIONS, "two", units=2, limit=4, ttl_seconds=30
    )
    fits = await store.try_acquire(
        org_id, ACTIONS, "one", units=1, limit=4, ttl_seconds=30
    )

    assert wide == Admitted(in_use=3, limit=4)
    assert too_wide == Busy(in_use=3, limit=4)
    assert fits == Admitted(in_use=4, limit=4)
    members = await client.zrange(lease_key(org_id, ACTIONS), 0, -1)
    assert sorted(members) == ["one#0", "wide#0", "wide#1", "wide#2"]


@pytest.mark.anyio
async def test_acquire_is_idempotent_per_lease_id(
    store: LeaseStore, org_id: uuid.UUID
) -> None:
    await store.try_acquire(org_id, ACTIONS, "a", units=2, limit=2, ttl_seconds=30)

    # The org is full, but the same attempt retrying its acquire still holds it.
    again = await store.try_acquire(
        org_id, ACTIONS, "a", units=2, limit=2, ttl_seconds=30
    )

    assert again == Admitted(in_use=2, limit=2)
    assert await store.in_use(org_id, ACTIONS) == 2


@pytest.mark.anyio
async def test_renew_extends_held_lease_only(
    store: LeaseStore, client: redis.Redis, org_id: uuid.UUID
) -> None:
    key = lease_key(org_id, ACTIONS)
    await store.try_acquire(org_id, ACTIONS, "a", units=2, limit=5, ttl_seconds=5)
    before = await client.zscore(key, "a#1")

    renewed = await store.renew(org_id, ACTIONS, "a", units=2, ttl_seconds=60)
    missing = await store.renew(org_id, ACTIONS, "nope", units=1, ttl_seconds=60)

    after = await client.zscore(key, "a#1")
    assert renewed is True
    assert missing is False
    assert before is not None and after is not None
    assert after - before > 50_000
    # Renewal never adds members.
    assert await client.zscore(key, "nope#0") is None


@pytest.mark.anyio
async def test_release_removes_every_unit(
    store: LeaseStore, client: redis.Redis, org_id: uuid.UUID
) -> None:
    await store.try_acquire(org_id, ACTIONS, "a", units=3, limit=5, ttl_seconds=30)
    await store.try_acquire(org_id, ACTIONS, "b", units=1, limit=5, ttl_seconds=30)

    await store.release(org_id, ACTIONS, "a", units=3)
    await store.release(org_id, ACTIONS, "missing", units=2)

    assert await client.zrange(lease_key(org_id, ACTIONS), 0, -1) == ["b#0"]


@pytest.mark.anyio
async def test_expired_leases_are_pruned(
    store: LeaseStore, client: redis.Redis, org_id: uuid.UUID
) -> None:
    await store.try_acquire(
        org_id, ACTIONS, "crashed", units=1, limit=1, ttl_seconds=0.05
    )
    assert (
        await store.try_acquire(
            org_id, ACTIONS, "next", units=1, limit=1, ttl_seconds=30
        )
    ) == Busy(in_use=1, limit=1)

    await asyncio.sleep(0.1)

    assert await store.in_use(org_id, ACTIONS) == 0
    assert (
        await store.try_acquire(
            org_id, ACTIONS, "next", units=1, limit=1, ttl_seconds=30
        )
    ) == Admitted(in_use=1, limit=1)
    assert await client.zscore(lease_key(org_id, ACTIONS), "crashed#0") is None
    # The whole set expires when idle.
    assert await client.pttl(lease_key(org_id, ACTIONS)) > 0


@pytest.mark.anyio
async def test_orgs_and_resources_are_separate(
    store: LeaseStore, org_id: uuid.UUID
) -> None:
    other_org = uuid.uuid4()
    await store.try_acquire(org_id, ACTIONS, "a", units=1, limit=1, ttl_seconds=30)

    other = await store.try_acquire(
        other_org, ACTIONS, "a", units=1, limit=1, ttl_seconds=30
    )
    tools = await store.try_acquire(
        org_id, ConcurrencyResource.TOOL_CALLS, "a", units=1, limit=1, ttl_seconds=30
    )

    assert other == Admitted(in_use=1, limit=1)
    assert tools == Admitted(in_use=1, limit=1)


def _waiter(name: str) -> Waiter:
    return Waiter(workflow_id=f"wf_{name}/exec_{name}", run_id=str(uuid.uuid4()))


async def _fill(store: LeaseStore, org_id: uuid.UUID, *lease_ids: str) -> None:
    for lease_id in lease_ids:
        await store.try_acquire(
            org_id, ACTIONS, lease_id, units=1, limit=len(lease_ids), ttl_seconds=30
        )


@pytest.mark.anyio
async def test_busy_registers_a_run_once_and_keeps_its_place(
    store: LeaseStore, client: redis.Redis, org_id: uuid.UUID
) -> None:
    await _fill(store, org_id, "held")
    first, second = _waiter("first"), _waiter("second")

    for lease_id, waiter in [("a1", first), ("b1", second), ("a2", first)]:
        assert await store.try_acquire(
            org_id, ACTIONS, lease_id, units=1, limit=1, ttl_seconds=30, waiter=waiter
        ) == Busy(in_use=1, limit=1)
    # Refused without opting in, and admitted: neither touches the waiters.
    await store.try_acquire(org_id, ACTIONS, "c", units=1, limit=1, ttl_seconds=30)

    key = waiter_key(org_id, ACTIONS)
    # One entry per run, in the order each run first joined.
    assert await client.zrange(key, 0, -1) == [first.member(), second.member()]
    assert await client.pttl(key) > 0


@pytest.mark.anyio
async def test_admission_leaves_a_queued_run_in_place(
    store: LeaseStore, client: redis.Redis, org_id: uuid.UUID
) -> None:
    waiter = _waiter("w")
    await _fill(store, org_id, "held")
    await store.try_acquire(
        org_id, ACTIONS, "probe", units=1, limit=1, ttl_seconds=30, waiter=waiter
    )
    await store.release(org_id, ACTIONS, "held", units=1)

    admitted = await store.try_acquire(
        org_id, ACTIONS, "probe-2", units=1, limit=1, ttl_seconds=30, waiter=waiter
    )

    assert admitted == Admitted(in_use=1, limit=1)
    assert await client.zrange(waiter_key(org_id, ACTIONS), 0, -1) == [waiter.member()]


@pytest.mark.anyio
async def test_release_pops_the_oldest_waiter_only_when_it_freed_units(
    store: LeaseStore, client: redis.Redis, org_id: uuid.UUID
) -> None:
    await _fill(store, org_id, "a", "b")
    first, second = _waiter("first"), _waiter("second")
    for lease_id, waiter in [("x", first), ("y", second)]:
        await store.try_acquire(
            org_id, ACTIONS, lease_id, units=1, limit=2, ttl_seconds=30, waiter=waiter
        )

    popped = await store.release_and_pop_waiter(org_id, ACTIONS, "a", units=1)
    duplicate = await store.release_and_pop_waiter(org_id, ACTIONS, "a", units=1)
    missing = await store.release_and_pop_waiter(org_id, ACTIONS, "nope", units=3)

    assert popped == first
    assert duplicate is None
    assert missing is None
    assert await client.zrange(waiter_key(org_id, ACTIONS), 0, -1) == [second.member()]
    assert await client.zrange(lease_key(org_id, ACTIONS), 0, -1) == ["b#0"]
    # The next release pops the next run; then nobody is left.
    assert await store.release_and_pop_waiter(org_id, ACTIONS, "b", units=1) == second
    assert await store.in_use(org_id, ACTIONS) == 0
    assert await client.exists(waiter_key(org_id, ACTIONS)) == 0


@pytest.mark.anyio
async def test_plain_release_never_pops(
    store: LeaseStore, client: redis.Redis, org_id: uuid.UUID
) -> None:
    waiter = _waiter("w")
    await _fill(store, org_id, "held")
    await store.try_acquire(
        org_id, ACTIONS, "probe", units=1, limit=1, ttl_seconds=30, waiter=waiter
    )

    await store.release(org_id, ACTIONS, "held", units=1)

    assert await client.zrange(waiter_key(org_id, ACTIONS), 0, -1) == [waiter.member()]


@pytest.mark.anyio
async def test_pop_waiter_takes_the_next_run(
    store: LeaseStore, org_id: uuid.UUID
) -> None:
    await _fill(store, org_id, "held")
    first, second = _waiter("first"), _waiter("second")
    for lease_id, waiter in [("x", first), ("y", second)]:
        await store.try_acquire(
            org_id, ACTIONS, lease_id, units=1, limit=1, ttl_seconds=30, waiter=waiter
        )

    assert await store.pop_waiter(org_id, ACTIONS) == first
    assert await store.pop_waiter(org_id, ACTIONS) == second
    assert await store.pop_waiter(org_id, ACTIONS) is None


def test_waiter_member_round_trips_a_workflow_id_with_colons() -> None:
    waiter = Waiter(workflow_id="wf_a/exec_b:c", run_id=str(uuid.uuid4()))

    assert Waiter.from_member(waiter.member()) == waiter
    assert Waiter.from_member(waiter.member().encode()) == waiter
    assert Waiter.from_member("no-separator") is None
