"""Lease store scripts against a real Redis."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as redis

from tracecat.admission.leases import LeaseStore, lease_key
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
