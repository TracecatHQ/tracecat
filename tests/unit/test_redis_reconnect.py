"""Redis consumers recover when the server drops every connection.

Moving ElastiCache from an auth token to IAM (and any failover) closes every
open client connection once. These tests reproduce that against the local
Redis with ``CLIENT KILL`` and check each kind of Redis user keeps working
(idle connections are replaced transparently; a command in flight fails and
is retried only where the caller already retries):
pooled commands, blocking stream reads, the case-trigger and case-duration
consumers, the factory clients used by MCP, and IAM-mode pools (token login
and Entra re-AUTH) on top of the same factory.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from types import TracebackType
from typing import Self, cast

import boto3
import orjson
import pytest
import redis
from azure.core.credentials import AccessToken
from redis.asyncio.connection import Connection
from redis.auth.token import SimpleToken
from redis.exceptions import ConnectionError as RedisConnectionError

from tracecat import config
from tracecat.cases.durations.consumer import CaseDurationSyncConsumer
from tracecat.cases.triggers.consumer import CaseTriggerConsumer
from tracecat.redis import auth
from tracecat.redis import client as redis_client_module
from tracecat.redis.auth import (
    IamConnectionPool,
    IamHandshakeRetry,
    close_redis_client,
    create_redis_client,
)
from tracecat.redis.aws import AwsIamCredentialProvider
from tracecat.redis.azure import AzureEntraCredentialProvider
from tracecat.redis.client import RedisClient
from tracecat.redis.types import (
    AzureEntraTokenPolicy,
    RedisAwsIamAuth,
    RedisAzureEntraAuth,
)

pytestmark = pytest.mark.usefixtures("redis_server")


def _admin() -> redis.Redis:
    return redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)


def _db() -> int:
    return int(os.environ["REDIS_URL"].rsplit("/", 1)[1])


def _test_clients(admin: redis.Redis) -> list[dict[str, str]]:
    """Connections on this worker's database, excluding the admin connection."""
    # The sync client's annotations cover the asyncio client too; narrow them.
    own_id = cast(int, admin.client_id())
    clients = cast(list[dict[str, str]], admin.client_list())
    return [
        info
        for info in clients
        if int(info["db"]) == _db() and int(info["id"]) != own_id
    ]


def kill_connections() -> int:
    """Drop every client connection on this worker's database.

    The cutover runs ``CLIENT KILL TYPE normal``; killing by ID keeps other
    xdist workers sharing this Redis unaffected.
    """
    with _admin() as admin:
        killed = 0
        for info in _test_clients(admin):
            killed += cast(int, admin.client_kill_filter(_id=info["id"]))
        return killed


async def drop_connections() -> int:
    """Kill connections, then let the event loop see the server close them.

    In production the loop keeps running after a cutover; the kill above is a
    blocking call, so without this pause no connection has noticed yet.
    """
    killed = kill_connections()
    await asyncio.sleep(0.05)
    return killed


async def wait_for(
    condition: Callable[[], bool | Awaitable[bool]], timeout: float = 5.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if isinstance(result, Awaitable):
            result = await result
        if result:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def blocked_in(command: str) -> Callable[[], bool]:
    def check() -> bool:
        with _admin() as admin:
            return any(info["cmd"] == command for info in _test_clients(admin))

    return check


@pytest.fixture
async def redis_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[RedisClient]:
    """A fresh RedisClient singleton on this worker's test database."""
    monkeypatch.setattr(redis_client_module, "REDIS_URL", os.environ["REDIS_URL"])
    monkeypatch.setattr(redis_client_module, "REDIS_URL__ARN", None)
    await RedisClient().close()
    RedisClient._instance = None
    client = RedisClient()
    try:
        yield client
    finally:
        await client.close()
        RedisClient._instance = None


@pytest.mark.anyio
async def test_pooled_commands_recover_after_connections_are_killed(
    redis_client: RedisClient,
) -> None:
    await redis_client.set("key", "before")
    raw = await redis_client._get_client()
    client_id_before = await raw.client_id()

    assert await drop_connections() >= 1

    assert await redis_client.get("key") == "before"
    await redis_client.set("key", "after")
    assert await redis_client.get("key") == "after"
    raw = await redis_client._get_client()
    assert await raw.client_id() != client_id_before


@pytest.mark.anyio
async def test_raw_client_users_recover_after_connections_are_killed(
    redis_client: RedisClient,
) -> None:
    """Callers holding ``_get_client()`` (tiers, search, dedup) reconnect too."""
    raw = await redis_client._get_client()
    await raw.set("raw", "1")
    script = raw.register_script("return redis.call('INCR', KEYS[1])")

    assert await drop_connections() >= 1

    # The pool replaces a dead idle connection before handing it out, so
    # callers without retries (no command is ever replayed) keep working.
    assert await script(keys=["raw"]) == 2
    pipe = raw.pipeline(transaction=False)
    pipe.set("dedup", "1", nx=True, ex=60)
    pipe.get("raw")
    assert await pipe.execute() == [True, "2"]


@pytest.mark.anyio
async def test_blocking_stream_read_recovers_after_connection_is_killed(
    redis_client: RedisClient,
) -> None:
    await redis_client.xgroup_create("events", "group", id="$", mkstream=True)
    read = asyncio.create_task(
        redis_client.xreadgroup(
            "group", "consumer", streams={"events": ">"}, count=1, block=10_000
        )
    )
    await wait_for(blocked_in("xreadgroup"))

    assert kill_connections() >= 1
    await wait_for(blocked_in("xreadgroup"))
    with _admin() as admin:
        message_id = admin.xadd("events", {"n": "1"})

    result = await asyncio.wait_for(read, timeout=5)
    assert result == [["events", [(message_id, {"n": "1"})]]]


@pytest.mark.anyio
async def test_case_trigger_consumer_recovers_after_connections_are_killed(
    redis_client: RedisClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__CASE_TRIGGERS_ENABLED", True)
    stop = asyncio.Event()
    consumer = CaseTriggerConsumer(redis_client, stop_event=stop)
    consumer.block_ms = 500
    handled: asyncio.Queue[str] = asyncio.Queue()

    async def handle(message_id: str, fields: dict[str, str]) -> None:
        del fields
        await handled.put(message_id)
        await redis_client.xack(consumer.stream_key, consumer.group, [message_id])

    monkeypatch.setattr(consumer, "_handle_message", handle)
    task = asyncio.create_task(consumer.run())
    try:
        await wait_for(blocked_in("xreadgroup"))
        with _admin() as admin:
            first = admin.xadd(consumer.stream_key, {"event_id": "1"})
        assert await asyncio.wait_for(handled.get(), timeout=5) == first

        await wait_for(blocked_in("xreadgroup"))
        assert kill_connections() >= 1

        with _admin() as admin:
            second = admin.xadd(consumer.stream_key, {"event_id": "2"})
        assert await asyncio.wait_for(handled.get(), timeout=5) == second
        assert not task.done()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.anyio
async def test_case_trigger_consumer_survives_a_redis_outage(
    redis_client: RedisClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Errors that outlast the client's retries back off instead of exiting."""
    monkeypatch.setattr(config, "TRACECAT__CASE_TRIGGERS_ENABLED", True)
    monkeypatch.setattr(
        "tracecat.cases.triggers.consumer.REDIS_RETRY_BACKOFF_BASE_SECONDS", 0.01
    )
    stop = asyncio.Event()
    consumer = CaseTriggerConsumer(redis_client, stop_event=stop)
    calls = 0

    async def flaky_read(**kwargs: object) -> list[object]:
        nonlocal calls
        del kwargs
        calls += 1
        if calls <= 2:
            raise RedisConnectionError("Connection refused")
        stop.set()
        return []

    captured: list[BaseException] = []
    monkeypatch.setattr(
        "tracecat.cases.triggers.consumer.capture_api_background_task_failure",
        lambda error, *, task_name: captured.append(error),
    )
    monkeypatch.setattr(redis_client, "xreadgroup", flaky_read)
    await asyncio.wait_for(consumer.run(), timeout=5)
    assert calls == 3
    # One report per failure streak, not one per retry.
    assert len(captured) == 1


@pytest.mark.anyio
async def test_case_duration_consumer_recovers_after_connections_are_killed(
    redis_client: RedisClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_backfill() -> bool:
        return True

    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.enqueue_rollout_backfill_once",
        no_backfill,
    )
    stop = asyncio.Event()
    consumer = CaseDurationSyncConsumer(redis_client, stop_event=stop)
    consumer.block_ms = 500
    handled: asyncio.Queue[str] = asyncio.Queue()

    async def handle(entries: list[tuple[str, dict[str, str]]]) -> None:
        for message_id, _fields in entries:
            await handled.put(message_id)
        await redis_client.xack(
            consumer.stream_key, consumer.group, [m for m, _ in entries]
        )

    monkeypatch.setattr(consumer, "_handle_entries", handle)
    task = asyncio.create_task(consumer.run())
    try:
        await wait_for(blocked_in("xreadgroup"))
        assert kill_connections() >= 1

        with _admin() as admin:
            message_id = admin.xadd(consumer.stream_key, {"job": "1"})
        assert await asyncio.wait_for(handled.get(), timeout=10) == message_id
        assert not task.done()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.anyio
async def test_factory_client_recovers_after_connections_are_killed() -> None:
    """The MCP stores use ``create_redis_client`` directly, without RedisClient."""
    client = create_redis_client(os.environ["REDIS_URL"], decode_responses=True)
    try:
        await client.set("mcp", "state")
        assert await drop_connections() >= 1
        assert await client.get("mcp") == "state"
    finally:
        await close_redis_client(client)


@pytest.mark.anyio
async def test_factory_client_does_not_replay_in_flight_commands() -> None:
    """A command whose reply is lost fails instead of being re-sent.

    Re-sending a ``SET NX`` batch whose replies were lost would report every
    key as already seen, and dedup callers would silently drop those items.
    """
    client = create_redis_client(os.environ["REDIS_URL"], decode_responses=True)
    try:
        # execute_command: blpop/ping are annotated for the sync and asyncio clients.
        read = asyncio.create_task(client.execute_command("BLPOP", "never-pushed", 10))
        await wait_for(blocked_in("blpop"))
        assert kill_connections() >= 1
        with pytest.raises(RedisConnectionError):
            await asyncio.wait_for(read, timeout=5)
        assert await client.execute_command("PING")
    finally:
        await close_redis_client(client)


# --- IAM modes against local Redis (no TLS, no cloud) ---


class PlainIamConnection(IamHandshakeRetry, Connection):
    """``IamSSLConnection`` without TLS, for the local Redis."""


@pytest.fixture
def plain_iam_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run IAM-mode pools over plain TCP so the local Redis can serve them.

    Keeps ``IamSSLConnection``'s handshake retry; only TLS is dropped.
    """

    monkeypatch.setattr(auth, "IamSSLConnection", PlainIamConnection)
    monkeypatch.setattr(auth, "_validate_iam_url", lambda url, options, mode: None)


@pytest.fixture
def acl_user() -> Iterator[Callable[[str, str], None]]:
    """Create Redis ACL users whose password is a given token; delete after."""
    created: list[str] = []

    def create(user: str, password: str) -> None:
        with _admin() as admin:
            admin.acl_setuser(
                user,
                enabled=True,
                passwords=[f"+{password}"],
                keys=["*"],
                commands=["+@all"],
            )
        created.append(user)

    yield create
    with _admin() as admin:
        for user in created:
            admin.acl_deluser(user)


class ScriptedCredential:
    """Azure credential double: returns tokens in order, then the last one."""

    def __init__(self, tokens: list[AccessToken]) -> None:
        self.tokens = tokens
        self.closed = False

    async def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: object,
    ) -> AccessToken:
        del scopes, claims, tenant_id, enable_cae, kwargs
        return self.tokens.pop(0) if len(self.tokens) > 1 else self.tokens[0]

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self.close()


def _entra_token(oid: str, *, expires_in: float) -> AccessToken:
    expires_on = int(time.time() + expires_in)
    claims = orjson.dumps({"oid": oid, "exp": expires_on})
    payload = base64.urlsafe_b64encode(claims).rstrip(b"=").decode()
    return AccessToken(f"e30.{payload}.sig", expires_on)


@pytest.mark.anyio
@pytest.mark.usefixtures("plain_iam_connection")
async def test_aws_iam_pool_logs_in_with_token_and_reconnects(
    monkeypatch: pytest.MonkeyPatch, acl_user: Callable[[str, str], None]
) -> None:
    user = f"aws-app-{uuid.uuid4().hex[:8]}"
    aws_auth = RedisAwsIamAuth(
        user_id=user, replication_group_id="tracecat-redis", region="us-east-1"
    )
    provider = AwsIamCredentialProvider(
        aws_auth,
        session=boto3.Session(
            aws_access_key_id="AKIDEXAMPLE",
            aws_secret_access_key="secret",
            region_name="us-east-1",
        ),
    )
    _, token = provider.get_credentials()  # cached for the pool's logins
    acl_user(user, token)
    monkeypatch.setattr(auth, "build_credential_provider", lambda _auth: provider)

    client = create_redis_client(
        os.environ["REDIS_URL"], auth=aws_auth, decode_responses=True
    )
    try:
        assert await client.acl_whoami() == user
        assert await drop_connections() >= 1
        assert await client.acl_whoami() == user
    finally:
        await close_redis_client(client)


@pytest.mark.anyio
@pytest.mark.usefixtures("plain_iam_connection")
async def test_azure_entra_pool_moves_onto_refreshed_token(
    monkeypatch: pytest.MonkeyPatch, acl_user: Callable[[str, str], None]
) -> None:
    """A refreshed token replaces the pool's connections with new logins.

    The second token carries a different object ID, so ``ACL WHOAMI`` proves
    the pool logged in again with it.
    """
    first_user = f"entra-a-{uuid.uuid4().hex[:8]}"
    second_user = f"entra-b-{uuid.uuid4().hex[:8]}"
    policy = AzureEntraTokenPolicy(refresh_retry_seconds=0.05)
    # The first token reaches the refresh lead about 1 s from now.
    first = _entra_token(first_user, expires_in=policy.refresh_lead_seconds + 1)
    second = _entra_token(second_user, expires_in=3600)
    acl_user(first_user, first.token)
    acl_user(second_user, second.token)

    credential = ScriptedCredential([first, second])
    entra_auth = RedisAzureEntraAuth(token=policy)
    provider = AzureEntraCredentialProvider(
        entra_auth, credential_factory=lambda: credential
    )
    monkeypatch.setattr(auth, "build_credential_provider", lambda _auth: provider)

    client = create_redis_client(
        os.environ["REDIS_URL"], auth=entra_auth, decode_responses=True
    )
    try:
        assert await client.acl_whoami() == first_user
        connection_id = await client.client_id()

        async def reauthed() -> bool:
            return await client.acl_whoami() == second_user

        await wait_for(reauthed)
        assert await client.client_id() != connection_id

        assert await drop_connections() >= 1
        assert await client.acl_whoami() == second_user
    finally:
        await close_redis_client(client)
    assert credential.closed


@pytest.mark.anyio
async def test_refresh_recycles_busy_connections_on_release() -> None:
    """A connection busy during a refresh is closed on release, not re-AUTHed.

    redis-py's own deferred re-AUTH runs after the connection is back in the
    pool, where another task can check it out mid-AUTH.
    """
    pool = IamConnectionPool.from_url(
        os.environ["REDIS_URL"], connection_class=PlainIamConnection
    )
    try:
        busy = await pool.get_connection()
        idle = await pool.get_connection()
        await pool.release(idle)
        assert idle.is_connected

        await pool.re_auth_callback(SimpleToken("new-token", -1, -1, {"oid": "app"}))

        assert not idle.is_connected
        assert busy.is_connected and busy.should_reconnect()
        await pool.release(busy)
        assert not busy.is_connected

        reused = await pool.get_connection()
        assert reused.is_connected
        assert not reused.should_reconnect()
        await pool.release(reused)
    finally:
        await pool.disconnect()
