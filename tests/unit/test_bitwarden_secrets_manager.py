"""Bitwarden Secrets Manager provider, with the SDK replaced by a fake.

The child module's ``run`` is exercised in-process with a recording client;
the parent's process handling is exercised with ``create_subprocess_exec``
recorded. The error messages are the ones the real service (EU cloud,
``bitwarden-sdk`` 2.1.0) returned.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import uuid
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import orjson
import pytest
from loguru import logger
from pydantic import ValidationError
from tracecat_ee.secrets.errors import SecretResolutionError
from tracecat_ee.secrets.providers import bitwarden_secrets_manager as bw
from tracecat_ee.secrets.providers import bitwarden_sm_child as child
from tracecat_ee.secrets.resolver import (
    decrypt_access_tokens,
    resolve_external_references,
)

from tracecat.auth import sandbox as auth_sandbox
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.db.models import OrganizationSecretStore, Secret
from tracecat.exceptions import EntitlementRequired
from tracecat.secrets.encryption import encrypt_value
from tracecat.secrets.enums import (
    SecretMappingMode,
    SecretResolutionErrorCode,
    SecretSource,
    SecretStoreProvider,
)
from tracecat.secrets.schemas import (
    BITWARDEN_CLOUD_REGIONS,
    BitwardenSecretsManagerStoreConfig,
    SecretJsonField,
    SecretKeyMapping,
)
from tracecat.secrets.service import SecretsService
from tracecat.secrets.types import ExternalSecretReference, Failure

pytestmark = pytest.mark.anyio

ORG_ID = uuid.uuid4()
STORE_ID = uuid.uuid4()
TOKEN = "0.planted-client-id.planted-secret:cGxhbnRlZC1rZXktMTIzNA=="
# Fixed (synthetic) so parametrized test IDs match across xdist workers.
WHOLE_A = "0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0"
WHOLE_B = "1a2b3c4d-5e6f-4a0b-9c1d-2e3f4a5b6c7d"
JSON_C = "2b3c4d5e-6f70-4b1c-8d2e-3f4a5b6c7d8e"
VALUES = {
    WHOLE_A: "value-a",
    WHOLE_B: "value-b",
    JSON_C: '{"username": "user-c", "password": "pass-c"}',
}

# Recorded from the real service.
MESSAGE_BAD_TOKEN = (
    'Received error message from server: [400 Bad Request] {"error":"invalid_client"}'
)
MESSAGE_NOT_FOUND = (
    "Received error message from server: [404 Not Found] "
    '{"object":"error","message":"Resource not found.","validationErrors":null,'
    '"exceptionMessage":null,"exceptionStackTrace":null,"innerExceptionMessage":null}'
)
# Bitwarden answers a secret the machine account may not read with the same 404.
MESSAGE_DENIED = MESSAGE_NOT_FOUND
MESSAGE_UNREACHABLE = (
    "error sending request for url (https://api.bitwarden.eu/identity/connect/token)"
)
MESSAGE_THROTTLED = (
    "Received error message from server: [429 Too Many Requests] "
    '{"object":"error","message":"Slow down! Too many requests. Try again in 1s.",'
    '"validationErrors":null}'
)
# Raised by the SDK itself, before any request.
MESSAGE_NO_KEY = "Doesn't contain a decryption key"
MESSAGE_BAD_KEY = "Invalid base64 length: expected 16, got 2"
MESSAGE_PLAIN_HTTP = (
    "builder error for url (http://vault.example.com/identity/connect/token)"
)
MESSAGE_BAD_ID = (
    "Invalid command value: UUID parsing failed: invalid character: found `n` at 1"
)
# Not a recorded message: stands in for anything the classifier does not know.
MESSAGE_UNRECOGNIZED = "planted-unrecognized-sdk-failure"


class RecordingClient:
    """Stands in for ``BitwardenClient`` and records every call."""

    def __init__(
        self,
        *,
        login_error: str | None = None,
        values: dict[str, str] | None = None,
        unreadable: frozenset[str] = frozenset(),
        organization_id: uuid.UUID = ORG_ID,
    ) -> None:
        self.logins: list[str] = []
        self.batches: list[list[str]] = []
        self.singles: list[str] = []
        self.login_error = login_error
        self.values = VALUES if values is None else values
        self.unreadable = unreadable
        self.organization_id = organization_id

    def auth(self) -> RecordingClient:
        return self

    def secrets(self) -> RecordingClient:
        return self

    def login_access_token(self, access_token: str) -> None:
        self.logins.append(access_token)
        if self.login_error:
            raise Exception(self.login_error)

    def _secret(self, secret_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.UUID(secret_id),
            value=self.values[secret_id],
            organization_id=self.organization_id,
        )

    def get_by_ids(self, uuids: list[uuid.UUID]) -> SimpleNamespace:
        ids = [str(i) for i in uuids]
        self.batches.append(ids)
        if any(i in self.unreadable for i in ids):
            raise Exception(MESSAGE_DENIED)
        if any(secret_id not in self.values for secret_id in ids):
            raise Exception(MESSAGE_NOT_FOUND)
        return SimpleNamespace(
            data=SimpleNamespace(data=[self._secret(i) for i in ids])
        )

    def get(self, secret_id: str) -> SimpleNamespace:
        self.singles.append(secret_id)
        if secret_id in self.unreadable:
            raise Exception(MESSAGE_DENIED)
        if secret_id not in self.values:
            raise Exception(MESSAGE_NOT_FOUND)
        return SimpleNamespace(data=self._secret(secret_id))


BW_CONFIG = BitwardenSecretsManagerStoreConfig(server="eu", organization_id=ORG_ID)


def _reference(
    alias: str, key: str, mapping: SecretKeyMapping
) -> ExternalSecretReference:
    return ExternalSecretReference(
        secret_id=uuid.uuid4(),
        alias=alias,
        environment="default",
        store_id=STORE_ID,
        provider=SecretStoreProvider.BITWARDEN_SECRETS_MANAGER,
        store_enabled=True,
        store_config=BW_CONFIG,
        key=key,
        mapping=mapping,
    )


def _whole(key: str) -> SecretKeyMapping:
    return SecretKeyMapping(mode=SecretMappingMode.WHOLE_STRING, keys=[key])


REFERENCES = [
    _reference("alpha", WHOLE_A, _whole("A_TOKEN")),
    _reference("beta", WHOLE_B, _whole("B_TOKEN")),
    _reference(
        "gamma",
        JSON_C,
        SecretKeyMapping(
            mode=SecretMappingMode.JSON,
            fields=[
                SecretJsonField(key="USER", field="username"),
                SecretJsonField(key="PASS", field="password"),
            ],
        ),
    ),
]


def use_client(monkeypatch: pytest.MonkeyPatch, client: RecordingClient) -> None:
    """Make the child's ``BitwardenClient`` the given fake."""
    monkeypatch.setattr(child, "BitwardenClient", lambda settings: client)


def run_child_in_process(
    monkeypatch: pytest.MonkeyPatch, client: RecordingClient
) -> list[dict[str, Any]]:
    """Replace the child process with ``child.run`` on a fake client."""
    payloads: list[dict[str, Any]] = []
    use_client(monkeypatch, client)

    async def fake_run_child(payload: bytes) -> tuple[bytes, int]:
        document = orjson.loads(payload)
        payloads.append(document)
        return orjson.dumps(child.run(document)), 0

    monkeypatch.setattr(bw, "_run_child", fake_run_child)
    return payloads


def with_token(
    references: Sequence[ExternalSecretReference],
) -> list[ExternalSecretReference]:
    """The references with their store's token sealed on them, as loaded."""
    sealed = encrypt_value(TOKEN.encode(), key=get_db_encryption_key())
    return [
        dataclasses.replace(reference, encrypted_access_token=sealed)
        for reference in references
    ]


async def resolve(references: Sequence[ExternalSecretReference] = REFERENCES) -> Any:
    return await resolve_external_references(with_token(references))


@pytest.fixture
def captured_logs() -> Any:
    lines: list[str] = []
    sink = logger.add(lines.append, level="TRACE", format="{message} | {extra}")
    yield lines
    logger.remove(sink)


async def test_one_login_one_batch_and_mapped_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given four keys from three references in one store (two whole_string, one json)
    client = RecordingClient()
    payloads = run_child_in_process(monkeypatch, client)

    # When the run resolves them
    resolved = await resolve()

    # Then the SDK saw one login and one get_by_ids with the three IDs
    assert client.logins == [TOKEN]
    assert client.batches == [[WHOLE_A, WHOLE_B, JSON_C]]
    assert client.singles == []
    assert payloads[0]["api_url"] == "https://api.bitwarden.eu"
    # and the keys hold the right values
    assert resolved == {
        "alpha": {"A_TOKEN": "value-a"},
        "beta": {"B_TOKEN": "value-b"},
        "gamma": {"USER": "user-c", "PASS": "pass-c"},
    }


async def test_run_time_decrypts_the_token_stored_on_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given references whose store holds the token encrypted
    client = RecordingClient()
    run_child_in_process(monkeypatch, client)
    references = with_token(REFERENCES)

    # When a run resolves them through the run-time entry point
    resolved = await resolve_external_references(references)

    # Then the child logged in with the decrypted token
    assert client.logins == [TOKEN]
    assert resolved["alpha"] == {"A_TOKEN": "value-a"}


async def test_run_time_skips_tokens_it_does_not_need_or_cannot_decrypt(
    monkeypatch: pytest.MonkeyPatch, captured_logs: list[str]
) -> None:
    run_child_in_process(monkeypatch, RecordingClient())
    sealed = encrypt_value(TOKEN.encode(), key=get_db_encryption_key())
    disabled = dataclasses.replace(
        REFERENCES[0], encrypted_access_token=b"not-a-token", store_enabled=False
    )
    # A disabled store's token is never decrypted
    assert decrypt_access_tokens([disabled]) == {}

    # A token that does not decrypt fails the run sanitized, not with the
    # cryptography error, and is tried and logged once per store
    corrupt = [
        dataclasses.replace(reference, encrypted_access_token=sealed[:-4])
        for reference in REFERENCES
    ]
    with pytest.raises(SecretResolutionError) as exc_info:
        await resolve_external_references(corrupt)
    assert exc_info.value.code == SecretResolutionErrorCode.CREDENTIAL_MISSING
    assert "\n".join(captured_logs).count("could not be decrypted") == 1


class FakeProcess:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self.pid = 2**22 + 7  # never a live PID; killpg finds nothing
        self.returncode: int | None = None
        self._stdout = stdout
        self._final = returncode
        self.stdin_payloads: list[bytes] = []

    async def communicate(self, payload: bytes) -> tuple[bytes, None]:
        self.stdin_payloads.append(payload)
        self.returncode = self._final
        return self._stdout, None

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self._final


def record_spawns(
    monkeypatch: pytest.MonkeyPatch, make_process: Callable[[], FakeProcess]
) -> list[tuple[tuple[Any, ...], dict[str, Any], FakeProcess]]:
    calls: list[tuple[tuple[Any, ...], dict[str, Any], FakeProcess]] = []

    async def create_subprocess_exec(*args: Any, **kwargs: Any) -> FakeProcess:
        process = make_process()
        calls.append((args, kwargs, process))
        return process

    monkeypatch.setattr(bw.asyncio, "create_subprocess_exec", create_subprocess_exec)
    return calls


async def test_token_reaches_the_child_on_stdin_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT__DB_ENCRYPTION_KEY", "planted-tracecat-key")
    monkeypatch.setenv("BITWARDEN_PLANTED", TOKEN)
    ok = {
        secret_id: {"value": value, "organization_id": str(ORG_ID)}
        for secret_id, value in VALUES.items()
    }
    stdout = orjson.dumps({"results": ok, "fatal": False})
    calls = record_spawns(monkeypatch, lambda: FakeProcess(stdout))

    await resolve()

    assert len(calls) == 1
    args, kwargs, process = calls[0]
    assert all(TOKEN not in str(arg) for arg in args)
    assert args[1] == "-I"
    env = kwargs["env"]
    assert TOKEN not in orjson.dumps(env).decode()
    assert not any(key.startswith("TRACECAT__") for key in env)
    assert kwargs["start_new_session"] is True
    assert TOKEN in process.stdin_payloads[0].decode()


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        (RecordingClient(login_error=MESSAGE_BAD_TOKEN), "auth_failed"),
        (RecordingClient(values={WHOLE_B: "b", JSON_C: "{}"}), "not_found"),
        (RecordingClient(unreadable=frozenset({WHOLE_A})), "not_found"),
        (RecordingClient(login_error=MESSAGE_UNREACHABLE), "unavailable"),
        (RecordingClient(login_error=MESSAGE_UNRECOGNIZED), "unknown"),
    ],
    ids=["bad-token", "missing-id", "denied", "unreachable", "unrecognized"],
)
async def test_sdk_errors_fail_the_run_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    captured_logs: list[str],
    client: RecordingClient,
    expected: str,
) -> None:
    run_child_in_process(monkeypatch, client)

    with pytest.raises(SecretResolutionError) as exc_info:
        await resolve()

    error = exc_info.value
    assert error.code == expected
    # The missing or unreadable ID is the one the error names (per-ID retry).
    if client.values.keys() != VALUES.keys() or client.unreadable:
        assert error.alias == "alpha"
    history = str(error) + repr(error.detail)
    for leaked in (
        TOKEN,
        "invalid_client",
        "Resource not found",
        "api.bitwarden.eu",
        MESSAGE_UNRECOGNIZED,
    ):
        assert leaked not in history
        assert leaked not in "\n".join(captured_logs)


async def test_throttled_read_is_retried_within_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    real_get_by_ids = client.get_by_ids
    attempts: list[int] = []

    def flaky(ids: list[uuid.UUID]) -> SimpleNamespace:
        attempts.append(1)
        if len(attempts) == 1:
            raise Exception(MESSAGE_THROTTLED)
        return real_get_by_ids(ids)

    monkeypatch.setattr(client, "get_by_ids", flaky)
    monkeypatch.setattr(child.time, "sleep", lambda _: None)
    run_child_in_process(monkeypatch, client)

    resolved = await resolve()

    assert len(attempts) == 2
    assert resolved["alpha"] == {"A_TOKEN": "value-a"}


async def test_missing_access_token_fails_before_the_provider_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_spawns(monkeypatch, lambda: FakeProcess(b""))

    with pytest.raises(SecretResolutionError) as exc_info:
        await resolve_external_references(REFERENCES)

    assert exc_info.value.code == SecretResolutionErrorCode.CREDENTIAL_MISSING
    assert calls == []


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [
        (b"", 1),
        (orjson.dumps({"results": {}, "fatal": {"type": "PanicException"}}), 0),
        (b"not json at all " + TOKEN.encode(), 0),
    ],
    ids=["non-zero-exit", "fatal-record", "unparseable"],
)
async def test_broken_child_fails_every_reference_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    captured_logs: list[str],
    stdout: bytes,
    returncode: int,
) -> None:
    record_spawns(monkeypatch, lambda: FakeProcess(stdout, returncode))
    outcomes = await bw.fetch_with_child(BW_CONFIG, TOKEN, [WHOLE_A, WHOLE_B])

    assert all(
        isinstance(o, Failure) and o.code == SecretResolutionErrorCode.UNAVAILABLE
        for o in outcomes.values()
    )
    with pytest.raises(SecretResolutionError) as exc_info:
        await resolve()
    assert exc_info.value.code == SecretResolutionErrorCode.UNAVAILABLE
    # A child's exception type is never forwarded as the vendor code
    assert "PanicException" not in str(exc_info.value)
    assert TOKEN not in str(exc_info.value) + "\n".join(captured_logs)


async def test_entitlement_off_fails_before_any_sdk_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-executor",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        scopes=frozenset({"secret:read"}),
    )
    secret = Secret(
        id=uuid.uuid4(),
        name="bw_ref",
        environment="default",
        source=SecretSource.BITWARDEN_SECRETS_MANAGER,
        encrypted_keys=b"",
        remote_reference=WHOLE_A,
        remote_key_mapping={"mode": "whole_string", "keys": ["TOKEN"]},
        store=OrganizationSecretStore(
            id=STORE_ID,
            enabled=True,
            provider=SecretStoreProvider.BITWARDEN_SECRETS_MANAGER,
            config={
                "provider": "bitwarden_secrets_manager",
                "server": "eu",
                "organization_id": str(ORG_ID),
            },
        ),
    )
    monkeypatch.setattr(
        SecretsService, "search_secrets", AsyncMock(return_value=[secret])
    )
    monkeypatch.setattr(
        auth_sandbox,
        "check_entitlement",
        AsyncMock(side_effect=EntitlementRequired("external_secret_stores")),
    )
    calls = record_spawns(monkeypatch, lambda: FakeProcess(b""))
    session = AsyncMock()

    class _Ctx:
        async def __aenter__(self) -> SecretsService:
            return SecretsService(session, role=role)

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(SecretsService, "with_session", lambda *, role: _Ctx())

    with pytest.raises(EntitlementRequired):
        async with auth_sandbox.AuthSandbox(role=role, secrets=["bw_ref"]):
            pass
    assert calls == []


async def test_concurrent_fetches_never_exceed_one_cap_across_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bw, "_SLOTS", asyncio.Semaphore(bw.MAX_CONCURRENT_FETCHES))
    running = 0
    peak = 0

    async def slow_child(payload: bytes) -> tuple[bytes, int]:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        ids = orjson.loads(payload)["secret_ids"]
        results = {i: {"value": "v", "organization_id": str(ORG_ID)} for i in ids}
        return orjson.dumps({"results": results, "fatal": False}), 0

    monkeypatch.setattr(bw, "_run_child", slow_child)
    monkeypatch.setattr(bw, "server_resolves_public", AsyncMock(return_value=True))
    servers = ["us", "eu"] + [f"https://vault{i}.example" for i in range(10)]
    await asyncio.gather(
        *(
            bw.fetch_with_child(
                BitwardenSecretsManagerStoreConfig(
                    server=server, organization_id=ORG_ID
                ),
                TOKEN,
                [str(uuid.uuid4())],
            )
            for server in servers
        )
    )
    assert peak == bw.MAX_CONCURRENT_FETCHES


@pytest.mark.parametrize(
    "reference",
    [WHOLE_A.upper(), WHOLE_A.replace("-", ""), "{" + WHOLE_A + "}", "not-an-id"],
)
def test_only_canonical_secret_ids_are_saved(reference: str) -> None:
    backend = bw.BitwardenSecretsManagerBackend()
    with pytest.raises(ValueError, match="lowercase"):
        backend.validate_reference(BW_CONFIG, reference)
    backend.validate_reference(BW_CONFIG, WHOLE_A)


def test_cloud_regions_match_the_provider_endpoints() -> None:
    # Core validates the region names; the provider maps them to endpoints.
    assert set(bw.BITWARDEN_CLOUD_SERVERS) == BITWARDEN_CLOUD_REGIONS


@pytest.mark.parametrize(
    "server",
    [
        "https://vault.example.com/?",
        "https://vault.example.com/#",
        "https://exa mple.com",
        "https://vault.example.com\n",
        "https://user:pass@vault.example.com",
        "http://vault.example.com",
        # Python's host is the whole string; the SDK's parser stops at "\".
        "https://internal-host\\x.example.com",
        "https://vault.example.com\\path",
        "https://[::1]",
        "https://vault_example.com",
        "https://vault.example.com:notaport",
    ],
)
def test_self_hosted_server_must_be_a_plain_https_base(server: str) -> None:
    with pytest.raises(ValidationError):
        BitwardenSecretsManagerStoreConfig(server=server, organization_id=ORG_ID)


@pytest.mark.parametrize(
    ("server", "stored"),
    [
        ("https://vault.example.com/bitwarden/", "https://vault.example.com/bitwarden"),
        ("https://Vault.Example.COM", "https://vault.example.com"),
        ("https://vault.example.com:443/", "https://vault.example.com"),
        ("https://vault.example.com:8443", "https://vault.example.com:8443"),
    ],
)
def test_self_hosted_server_is_stored_normalized(server: str, stored: str) -> None:
    # One server, one spelling: the concurrency cap is keyed on it.
    config = BitwardenSecretsManagerStoreConfig(server=server, organization_id=ORG_ID)
    assert config.server == stored


@pytest.mark.parametrize(
    ("message", "phase", "expected", "vendor_code"),
    [
        (MESSAGE_BAD_TOKEN, "login", "auth_failed", "http_400"),
        (MESSAGE_NO_KEY, "login", "auth_failed", "invalid_token"),
        (MESSAGE_BAD_KEY, "login", "auth_failed", "invalid_token"),
        (MESSAGE_NOT_FOUND, "read", "not_found", "http_404"),
        (MESSAGE_NOT_FOUND, "login", "unknown", "http_404"),
        (MESSAGE_THROTTLED, "login", "throttled", "http_429"),
        (MESSAGE_THROTTLED, "read", "throttled", "http_429"),
        (MESSAGE_UNREACHABLE, "login", "unavailable", "connection"),
        (MESSAGE_UNREACHABLE, "read", "unavailable", "connection"),
        (MESSAGE_PLAIN_HTTP, "login", "unavailable", "connection"),
        (MESSAGE_BAD_ID, "read", "invalid_reference", "invalid_command"),
        # A token is parsed only at login; the same text later is not a bad token.
        (MESSAGE_NO_KEY, "read", "unknown", "unclassified"),
        (MESSAGE_UNRECOGNIZED, "login", "unknown", "unclassified"),
        (MESSAGE_UNRECOGNIZED, "read", "unknown", "unclassified"),
    ],
)
def test_each_recorded_message_is_pinned_to_its_code(
    message: str, phase: child.Phase, expected: str, vendor_code: str
) -> None:
    classified = child.classify_sdk_error(message, phase=phase)
    assert (classified.code, classified.vendor_code) == (expected, vendor_code)


@pytest.mark.parametrize(
    ("status", "phase", "expected"),
    [
        (400, "login", "auth_failed"),
        (400, "read", "unknown"),
        (401, "login", "auth_failed"),
        (401, "read", "auth_failed"),
        (403, "login", "auth_failed"),
        (403, "read", "access_denied"),
        (409, "login", "unknown"),
        (409, "read", "unknown"),
        (502, "login", "unavailable"),
        (503, "read", "unavailable"),
    ],
)
def test_http_status_maps_by_phase(
    status: int, phase: child.Phase, expected: str
) -> None:
    message = f"Received error message from server: [{status} Reason] body"
    assert child.classify_sdk_error(message, phase=phase).code == expected


@pytest.mark.parametrize(
    "message",
    [MESSAGE_BAD_TOKEN, MESSAGE_NO_KEY, MESSAGE_PLAIN_HTTP, MESSAGE_UNRECOGNIZED],
)
def test_sdk_message_never_leaves_the_child(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    use_client(monkeypatch, RecordingClient(login_error=message))
    result = child.run(
        {
            "api_url": "https://api.bitwarden.eu",
            "identity_url": "https://identity.bitwarden.eu",
            "access_token": TOKEN,
            "secret_ids": [WHOLE_A],
            "deadline_seconds": 5,
        }
    )

    assert message not in orjson.dumps(result).decode()
    assert set(result["results"][WHOLE_A]) == {"error", "vendor_code"}


async def test_fetch_rechecks_that_a_self_hosted_server_is_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A name that passed the check on save can resolve elsewhere by fetch time
    run_child = AsyncMock()
    monkeypatch.setattr(bw, "_run_child", run_child)
    private = BitwardenSecretsManagerStoreConfig(
        server="https://127.0.0.1:8443", organization_id=ORG_ID
    )

    outcomes = await bw.fetch_with_child(private, TOKEN, [WHOLE_A])

    assert outcomes == {
        WHOLE_A: Failure(SecretResolutionErrorCode.UNAVAILABLE, "server_not_public")
    }
    run_child.assert_not_awaited()


@pytest.mark.slow
async def test_child_ends_itself_at_the_deadline_without_a_parent() -> None:
    """A child whose parent died must not hang on with the token in memory."""
    connections: list[asyncio.StreamWriter] = []

    async def hold(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(writer)

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    payload = orjson.dumps(
        {
            "api_url": f"https://127.0.0.1:{port}/api",
            "identity_url": f"https://127.0.0.1:{port}/identity",
            "access_token": "0.5f1c9a3e-2b7d-4c8e-9f10-3a4b5c6d7e8f.planted:cGxhbnRlZGtleTEyMzQ1Ng==",
            "secret_ids": [WHOLE_A],
            "deadline_seconds": 1,
        }
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(bw._CHILD_SCRIPT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": os.environ["PATH"]},
    )
    try:
        assert process.stdin is not None
        process.stdin.write(payload)
        process.stdin.close()
        # Never killed by us: it must exit on its own (deadline + 2 s grace).
        returncode = await asyncio.wait_for(process.wait(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        server.close()
        for writer in connections:
            writer.close()
    assert connections, "the child never reached the server"
    assert returncode != 0


def test_per_id_fallback_stops_at_the_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient(values={WHOLE_B: "b", JSON_C: "{}"})
    use_client(monkeypatch, client)
    result = child.run(
        {
            "api_url": "https://api.example",
            "identity_url": "https://identity.example",
            "access_token": TOKEN,
            "secret_ids": [WHOLE_A, WHOLE_B, JSON_C],
            "deadline_seconds": 5,
        }
    )
    assert client.singles == [WHOLE_A]
    assert result["results"][WHOLE_A]["error"] == "not_found"
    assert result["results"][JSON_C]["vendor_code"] == "skipped"
