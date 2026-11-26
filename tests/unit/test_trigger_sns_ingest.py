"""Tests for the SNS ingest endpoint of event sources."""

from __future__ import annotations

import base64
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import orjson
import pytest
import respx
from botocore import UNSIGNED
from botocore.exceptions import ClientError
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.api.app import app as tracecat_app
from tracecat.db.models import EventOutbox, EventSource, Workspace
from tracecat.triggers.enums import (
    EventSourceKind,
    SubscriptionStatus,
    TriggerStatus,
)
from tracecat.triggers.ingest import rate_limit, sns
from tracecat.triggers.ingest import service as ingest_service
from tracecat.triggers.ingest.router import MAX_BODY_BYTES
from tracecat.triggers.ingest.router import router as ingest_router
from tracecat.triggers.ingest.types import IngestError, IngestErrorCode, SnsTopic
from tracecat.triggers.types import build_occurrence_key, event_source_endpoint_url

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

TOPIC = "arn:aws:sns:us-east-1:123456789012:bucket-events"
OTHER_TOPIC = "arn:aws:sns:us-east-1:210987654321:attacker-events"
CERT_URL = (
    "https://sns.us-east-1.amazonaws.com/"
    "SimpleNotificationService-0123456789abcdef0123456789abcdef.pem"
)
SUBSCRIBE_URL = (
    "https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription"
    f"&TopicArn={TOPIC}&Token=synthetic-token"
)
SUBSCRIPTION_ARN = f"{TOPIC}:11111111-2222-3333-4444-555555555555"


# ----- Signing material: a synthetic "SNS" key and certificate -----


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _cert_pem(
    key: rsa.RSAPrivateKey, *, not_before: datetime, not_after: datetime
) -> bytes:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def cert_pem(signing_key: rsa.RSAPrivateKey) -> bytes:
    now = datetime.now(UTC)
    return _cert_pem(
        signing_key,
        not_before=now - timedelta(days=1),
        not_after=now + timedelta(days=1),
    )


def _canonical(message: dict[str, str]) -> bytes:
    """The signed text, written out from the AWS docs, not from the code."""
    if message["Type"] == "Notification":
        names = ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"]
    else:
        names = [
            "Message",
            "MessageId",
            "SubscribeURL",
            "Timestamp",
            "Token",
            "TopicArn",
            "Type",
        ]
    return "".join(
        f"{name}\n{message[name]}\n" for name in names if name in message
    ).encode()


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


SignFn = Callable[..., dict[str, str]]


@pytest.fixture
def sign(signing_key: rsa.RSAPrivateKey) -> SignFn:
    def _sign(
        message_type: str,
        message: str,
        *,
        topic: str = TOPIC,
        cert_url: str = CERT_URL,
        version: str = "2",
        timestamp: str | None = None,
        subject: str | None = None,
    ) -> dict[str, str]:
        envelope = {
            "Type": message_type,
            "MessageId": str(uuid.uuid4()),
            "TopicArn": topic,
            "Message": message,
            "Timestamp": timestamp or _now_iso(),
            "SignatureVersion": version,
            "SigningCertURL": cert_url,
        }
        if subject is not None:
            envelope["Subject"] = subject
        if message_type != "Notification":
            envelope["Token"] = "synthetic-token"
            envelope["SubscribeURL"] = SUBSCRIBE_URL
        algorithm = hashes.SHA1() if version == "1" else hashes.SHA256()
        signature = signing_key.sign(
            _canonical(envelope), padding.PKCS1v15(), algorithm
        )
        envelope["Signature"] = base64.b64encode(signature).decode()
        return envelope

    return _sign


def _s3_record(
    key: str = "uploads/report+2026%2F11.csv",
    *,
    event_name: str = "ObjectCreated:Put",
    sequencer: str = "0055AED6DCD90281E5",
    version_id: str | None = "v1",
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "key": key,
        "size": 1024,
        "eTag": "d41d8cd98f00b204e9800998ecf8427e",
        "sequencer": sequencer,
    }
    if version_id is not None:
        obj["versionId"] = version_id
    return {
        "eventVersion": "2.1",
        "eventSource": "aws:s3",
        "awsRegion": "us-east-1",
        "eventTime": "2026-11-27T10:00:00.000Z",
        "eventName": event_name,
        "s3": {
            "s3SchemaVersion": "1.0",
            "bucket": {
                "name": "synthetic-bucket",
                "arn": "arn:aws:s3:::synthetic-bucket",
            },
            "object": obj,
        },
    }


def _s3_message(*records: dict[str, Any]) -> str:
    return orjson.dumps({"Records": list(records)}).decode()


# ----- Fixtures: source, sessions, entitlement, HTTP -----


@pytest.fixture
async def source(session: AsyncSession, svc_workspace: Workspace) -> EventSource:
    row = EventSource(
        workspace_id=svc_workspace.id,
        kind=EventSourceKind.AWS_SNS.value,
        name="bucket",
        config={"topic_arn": TOPIC},
        status=TriggerStatus.ONLINE.value,
        endpoint_token=secrets.token_urlsafe(32),
    )
    session.add(row)
    await session.commit()
    return row


@pytest.fixture(autouse=True)
def ingest_env(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Route ingest's own sessions to the test session; entitle the org."""

    @asynccontextmanager
    async def _session_cm() -> AsyncIterator[AsyncSession]:
        yield session

    async def _entitled(*_: object) -> bool:
        return True

    async def _within_cap(_: uuid.UUID) -> int | None:
        return None

    monkeypatch.setattr(
        ingest_service, "get_async_session_bypass_rls_context_manager", _session_cm
    )
    monkeypatch.setattr(ingest_service, "is_org_entitled", _entitled)
    monkeypatch.setattr(ingest_service, "check_source_rate", _within_cap)
    monkeypatch.setattr(ingest_service, "check_confirmation_rate", _within_cap)
    sns._cert_cache.clear()
    yield
    sns._cert_cache.clear()


@pytest.fixture
def aws(cert_pem: bytes) -> Iterator[respx.MockRouter]:
    """Every outbound httpx request; only the certificate URL is served."""
    with respx.mock(assert_all_called=False) as router:
        router.get(CERT_URL).mock(return_value=httpx.Response(200, content=cert_pem))
        yield router


@pytest.fixture
def confirm_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def _confirm(*, topic: SnsTopic, token: str) -> str:
        calls.append((topic.arn, token))
        return SUBSCRIPTION_ARN

    monkeypatch.setattr(ingest_service, "confirm_subscription", _confirm)
    return calls


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(ingest_router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
        yield c


async def _post(
    client: httpx.AsyncClient,
    source: EventSource,
    envelope: dict[str, str] | bytes,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    body = envelope if isinstance(envelope, bytes) else orjson.dumps(envelope)
    sent_headers = {"content-type": "text/plain; charset=UTF-8"}
    if isinstance(envelope, dict):
        sent_headers["x-amz-sns-message-type"] = envelope["Type"]
    sent_headers.update(headers or {})
    return await client.post(
        f"/ingest/sns/{source.endpoint_token}", content=body, headers=sent_headers
    )


async def _outbox(session: AsyncSession, source: EventSource) -> list[EventOutbox]:
    result = await session.execute(
        select(EventOutbox)
        .where(EventOutbox.source_id == source.id)
        .order_by(EventOutbox.id)
    )
    return list(result.scalars().all())


def _code(response: httpx.Response) -> str:
    return response.json()["detail"]["code"]


# ----- Notifications -----


async def test_app_serves_the_url_handed_to_customers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_API_URL", "https://api.example")
    token = secrets.token_urlsafe(32)

    route_path = tracecat_app.url_path_for("ingest_sns_message", endpoint_token=token)

    url = event_source_endpoint_url(EventSourceKind.AWS_SNS, token)
    assert url == f"https://api.example{route_path}"


@pytest.mark.parametrize("version", ["1", "2"])
async def test_valid_notification_queues_one_row(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    version: str,
) -> None:
    envelope = sign("Notification", _s3_message(_s3_record()), version=version)

    response = await _post(client, source, envelope)

    assert response.status_code == 200, response.text
    assert response.json()["queued"] == 1
    rows = await _outbox(session, source)
    assert len(rows) == 1
    row = rows[0]
    assert row.workspace_id == source.workspace_id
    assert row.kind == "object_storage"
    key = "uploads/report 2026/11.csv"
    expected_key = build_occurrence_key(
        bucket="synthetic-bucket",
        key=key,
        version_id="v1",
        sequencer="0055AED6DCD90281E5",
    )
    assert row.occurrence_key == expected_key
    assert row.payload["type"] == "object.created"
    assert row.payload["provider"] == "aws"
    assert row.payload["account"] == "123456789012"
    assert row.payload["region"] == "us-east-1"
    assert row.payload["bucket"] == "synthetic-bucket"
    assert row.payload["key"] == key
    assert row.payload["version_id"] == "v1"
    assert row.payload["size"] == 1024
    assert row.payload["event_id"] == expected_key
    assert row.payload["occurred_at"].startswith("2026-11-27T10:00:00")
    assert "received_at" in row.payload


async def test_notification_with_subject_verifies(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    envelope = sign(
        "Notification", _s3_message(_s3_record()), subject="Amazon S3 Notification"
    )
    response = await _post(client, source, envelope)
    assert response.status_code == 200, response.text


async def test_redelivery_queues_one_row(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    envelope = sign("Notification", _s3_message(_s3_record()))

    first = await _post(client, source, envelope)
    second = await _post(client, source, envelope)

    assert first.status_code == second.status_code == 200
    assert first.json()["queued"] == 1
    assert second.json() == {
        "queued": 0,
        "duplicates": 1,
        "ignored": 0,
        "confirmed": False,
    }
    assert len(await _outbox(session, source)) == 1


async def test_multi_record_message_queues_created_records_only(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    message = _s3_message(
        _s3_record("a.csv", sequencer="01"),
        _s3_record(
            "b.csv", event_name="ObjectCreated:CompleteMultipartUpload", sequencer="02"
        ),
        _s3_record("a.csv", event_name="ObjectRemoved:Delete", sequencer="03"),
        # The same object twice in one message: one row.
        _s3_record("a.csv", sequencer="01"),
    )

    response = await _post(client, source, sign("Notification", message))

    assert response.status_code == 200, response.text
    assert response.json() == {
        "queued": 2,
        "duplicates": 1,
        "ignored": 1,
        "confirmed": False,
    }
    rows = await _outbox(session, source)
    assert sorted(row.payload["key"] for row in rows) == ["a.csv", "b.csv"]


async def test_s3_test_event_is_ignored(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    message = orjson.dumps(
        {
            "Service": "Amazon S3",
            "Event": "s3:TestEvent",
            "Time": "2026-11-27T10:00:00.000Z",
            "Bucket": "synthetic-bucket",
        }
    ).decode()

    response = await _post(client, source, sign("Notification", message))

    assert response.status_code == 200
    assert response.json()["ignored"] == 1
    assert await _outbox(session, source) == []


@pytest.mark.parametrize(
    "message",
    [
        "not json",
        orjson.dumps({"hello": "world"}).decode(),
        _s3_message({"eventName": "ObjectCreated:Put", "eventSource": "aws:s3"}),
        _s3_message(_s3_record("k" * 1025)),
        orjson.dumps({"Records": []}).decode(),
        _s3_message(_s3_record() | {"awsRegion": "us-east-1\u0000"}),
        # orjson cannot encode the value either, so splice it into the text.
        _s3_message(_s3_record()).replace('"size":1024', f'"size":{10**30}'),
    ],
    ids=[
        "not-json",
        "no-records",
        "missing-object-fields",
        "key-too-long",
        "empty-records",
        "nul-in-region",
        "huge-size",
    ],
)
async def test_unusable_message_is_refused_without_rows(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    message: str,
) -> None:
    response = await _post(client, source, sign("Notification", message))

    assert response.status_code == 400
    assert _code(response) == "malformed"
    assert await _outbox(session, source) == []


async def test_failed_insert_answers_5xx(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail(*_: object) -> int:
        raise OperationalError("INSERT", {}, Exception("database is down"))

    monkeypatch.setattr(ingest_service, "_queue_events", _fail)

    response = await _post(
        client, source, sign("Notification", _s3_message(_s3_record()))
    )

    assert response.status_code == 500


# ----- Signature and certificate -----


async def test_bad_signature_is_refused(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    envelope = sign("Notification", _s3_message(_s3_record("a.csv")))
    envelope["Message"] = _s3_message(_s3_record("tampered.csv"))

    response = await _post(client, source, envelope)

    assert response.status_code == 403
    assert _code(response) == "signature_invalid"
    assert await _outbox(session, source) == []


@pytest.mark.parametrize("signature", ["é", "not base64!"])
async def test_undecodable_signature_is_refused(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    signature: str,
) -> None:
    envelope = sign("Notification", _s3_message(_s3_record()))
    envelope["Signature"] = signature

    response = await _post(client, source, envelope)

    assert response.status_code == 403
    assert _code(response) == "signature_invalid"
    assert await _outbox(session, source) == []


async def test_signature_from_another_key_is_refused(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    aws: respx.MockRouter,
) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    envelope = {
        "Type": "Notification",
        "MessageId": str(uuid.uuid4()),
        "TopicArn": TOPIC,
        "Message": _s3_message(_s3_record()),
        "Timestamp": _now_iso(),
        "SignatureVersion": "2",
        "SigningCertURL": CERT_URL,
    }
    signature = other_key.sign(
        _canonical(envelope), padding.PKCS1v15(), hashes.SHA256()
    )
    envelope["Signature"] = base64.b64encode(signature).decode()

    response = await _post(client, source, envelope)

    assert response.status_code == 403
    assert await _outbox(session, source) == []


@pytest.mark.parametrize(
    "cert_url",
    [
        CERT_URL.replace("https://", "http://"),
        CERT_URL.replace("sns.us-east-1.amazonaws.com", "attacker.example"),
        CERT_URL.replace(
            "sns.us-east-1.amazonaws.com",
            "sns.us-east-1.amazonaws.com.attacker.example",
        ),
        CERT_URL.replace("sns.us-east-1.amazonaws.com", "sns.eu-west-1.amazonaws.com"),
        CERT_URL.replace("https://", "https://user@"),
        CERT_URL.replace("amazonaws.com/", "amazonaws.com:8443/"),
        CERT_URL.replace("sns.us-east-1.amazonaws.com", "169.254.169.254"),
        CERT_URL + "?x=1",
        CERT_URL + "#frag",
        "https://sns.us-east-1.amazonaws.com/other.pem",
        "https://sns.us-east-1.amazonaws.com/a/../SimpleNotificationService-abc.pem",
        "https://sns.us-east-1.amazonaws.com\\@attacker.example/SimpleNotificationService-abc.pem",
    ],
    ids=[
        "http",
        "non-aws-host",
        "aws-suffix-trick",
        "other-region",
        "userinfo",
        "port",
        "metadata-ip",
        "query",
        "fragment",
        "wrong-path",
        "dot-segments",
        "backslash",
    ],
)
async def test_untrusted_cert_url_is_refused_without_fetch(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    cert_url: str,
) -> None:
    envelope = sign("Notification", _s3_message(_s3_record()), cert_url=cert_url)

    response = await _post(client, source, envelope)

    assert response.status_code == 403
    assert _code(response) == "signature_invalid"
    assert aws.calls.call_count == 0
    assert await _outbox(session, source) == []


async def test_cert_redirect_is_not_followed(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(CERT_URL).mock(
            return_value=httpx.Response(
                302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
            )
        )
        response = await _post(
            client, source, sign("Notification", _s3_message(_s3_record()))
        )
        assert router.calls.call_count == 1

    assert response.status_code == 403
    assert await _outbox(session, source) == []


@pytest.mark.parametrize(
    ("mock", "status_code"),
    [
        (httpx.Response(503), 503),
        (httpx.ConnectTimeout("slow"), 503),
        (httpx.Response(200, content=b"x" * (sns.MAX_CERT_BYTES + 1)), 403),
        (httpx.Response(200, content=b"not a certificate"), 403),
        (
            httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                # A raw stream: the body is only decoded when read.
                stream=httpx.ByteStream(b"not gzip"),
            ),
            503,
        ),
    ],
    ids=["host-5xx", "timeout", "oversized", "not-pem", "bad-encoding"],
)
async def test_cert_fetch_failures(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    mock: httpx.Response | Exception,
    status_code: int,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(CERT_URL)
        if isinstance(mock, Exception):
            route.mock(side_effect=mock)
        else:
            route.mock(return_value=mock)
        response = await _post(
            client, source, sign("Notification", _s3_message(_s3_record()))
        )

    assert response.status_code == status_code
    assert await _outbox(session, source) == []


async def test_expired_cert_is_refused(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    now = datetime.now(UTC)
    expired = _cert_pem(
        signing_key,
        not_before=now - timedelta(days=10),
        not_after=now - timedelta(days=1),
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(CERT_URL).mock(return_value=httpx.Response(200, content=expired))
        response = await _post(
            client, source, sign("Notification", _s3_message(_s3_record()))
        )

    assert response.status_code == 403


async def test_cert_is_fetched_once(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    for key in ("a.csv", "b.csv"):
        response = await _post(
            client, source, sign("Notification", _s3_message(_s3_record(key)))
        )
        assert response.status_code == 200
    assert aws.calls.call_count == 1


async def test_delayed_message_is_accepted(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    # A subscription throttled by its delivery policy can deliver hours late.
    old = (datetime.now(UTC) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    envelope = sign("Notification", _s3_message(_s3_record()), timestamp=old)

    response = await _post(client, source, envelope)

    assert response.status_code == 200, response.text
    assert len(await _outbox(session, source)) == 1


@pytest.mark.parametrize(
    "offset",
    [timedelta(hours=-25), timedelta(hours=1)],
    ids=["older-than-a-day", "future"],
)
async def test_message_outside_the_dedupe_horizon_is_refused(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    offset: timedelta,
) -> None:
    sent = (datetime.now(UTC) + offset).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    envelope = sign("Notification", _s3_message(_s3_record()), timestamp=sent)

    response = await _post(client, source, envelope)

    assert response.status_code == 400
    assert _code(response) == "stale_message"
    assert await _outbox(session, source) == []


async def test_nul_in_object_key_is_refused_not_retried(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    message = _s3_message(
        _s3_record("ok.csv", sequencer="01"), _s3_record("bad%00.csv")
    )

    response = await _post(client, source, sign("Notification", message))

    # 4xx: SNS moves it to the dead-letter queue instead of retrying an
    # insert PostgreSQL always rejects.
    assert response.status_code == 400
    assert _code(response) == "malformed"
    assert await _outbox(session, source) == []


# ----- Topic pin, raw delivery, size, token, entitlement, state -----


async def test_wrong_topic_is_refused_before_any_fetch(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    envelope = sign("Notification", _s3_message(_s3_record()), topic=OTHER_TOPIC)

    response = await _post(client, source, envelope)

    assert response.status_code == 403
    assert _code(response) == "topic_mismatch"
    assert aws.calls.call_count == 0
    assert await _outbox(session, source) == []


async def test_raw_delivery_header_is_refused(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    aws: respx.MockRouter,
) -> None:
    response = await _post(
        client,
        source,
        _s3_message(_s3_record()).encode(),
        headers={"x-amz-sns-rawdelivery": "true"},
    )

    assert response.status_code == 400
    assert _code(response) == "raw_delivery"
    assert aws.calls.call_count == 0


async def test_raw_body_without_envelope_is_refused(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    aws: respx.MockRouter,
) -> None:
    response = await _post(client, source, _s3_message(_s3_record()).encode())

    assert response.status_code == 400
    assert _code(response) == "malformed"
    assert await _outbox(session, source) == []


async def test_message_type_header_must_match(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    envelope = sign("Notification", _s3_message(_s3_record()))
    response = await _post(
        client,
        source,
        envelope,
        headers={"x-amz-sns-message-type": "SubscriptionConfirmation"},
    )
    assert response.status_code == 400
    assert _code(response) == "malformed"
    assert await _outbox(session, source) == []


async def test_oversized_body_is_refused(
    client: httpx.AsyncClient, source: EventSource, aws: respx.MockRouter
) -> None:
    response = await _post(client, source, b"{" + b" " * MAX_BODY_BYTES + b"}")

    assert response.status_code == 413
    assert _code(response) == "payload_too_large"


async def test_oversized_streamed_body_is_refused(
    client: httpx.AsyncClient, source: EventSource, aws: respx.MockRouter
) -> None:
    async def _chunks() -> AsyncIterator[bytes]:
        for _ in range(MAX_BODY_BYTES // 1024 + 2):
            yield b" " * 1024

    response = await client.post(
        f"/ingest/sns/{source.endpoint_token}", content=_chunks()
    )
    assert response.status_code == 413


@pytest.mark.parametrize("token", ["x" * 43, "short", "bad token with spaces"])
async def test_unknown_token_is_not_found(
    client: httpx.AsyncClient, sign: SignFn, aws: respx.MockRouter, token: str
) -> None:
    response = await client.post(
        f"/ingest/sns/{token}",
        content=orjson.dumps(sign("Notification", _s3_message(_s3_record()))),
    )
    assert response.status_code == 404
    assert aws.calls.call_count == 0


async def test_entitlement_off_is_refused(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _not_entitled(*_: object) -> bool:
        return False

    monkeypatch.setattr(ingest_service, "is_org_entitled", _not_entitled)

    response = await _post(
        client, source, sign("Notification", _s3_message(_s3_record()))
    )

    assert response.status_code == 403
    assert _code(response) == "not_entitled"
    assert aws.calls.call_count == 0
    assert await _outbox(session, source) == []


async def test_offline_source_refuses_notifications(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    source.status = TriggerStatus.OFFLINE.value
    await session.commit()

    response = await _post(
        client, source, sign("Notification", _s3_message(_s3_record()))
    )

    assert response.status_code == 409
    assert _code(response) == "source_offline"
    assert await _outbox(session, source) == []


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"config": {"topic_arn": OTHER_TOPIC}}, "topic_mismatch"),
        ({"status": TriggerStatus.OFFLINE.value}, "source_offline"),
    ],
    ids=["repointed", "taken-offline"],
)
async def test_source_changed_during_verification_queues_nothing(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
    code: str,
) -> None:
    verify = ingest_service.verify_envelope

    async def _verify_then_change(*args: Any, **kwargs: Any) -> None:
        await verify(*args, **kwargs)
        # An editor changes the source while the message is in flight.
        for name, value in change.items():
            setattr(source, name, value)
        await session.commit()

    monkeypatch.setattr(ingest_service, "verify_envelope", _verify_then_change)

    response = await _post(
        client, source, sign("Notification", _s3_message(_s3_record()))
    )

    assert response.status_code in (403, 409)
    assert _code(response) == code
    assert await _outbox(session, source) == []


async def test_rate_cap_answers_503_with_retry_after(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _over_cap(_: uuid.UUID) -> int | None:
        return 17

    monkeypatch.setattr(ingest_service, "check_source_rate", _over_cap)

    response = await _post(
        client, source, sign("Notification", _s3_message(_s3_record()))
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "17"
    assert _code(response) == "rate_limited"
    assert await _outbox(session, source) == []


async def test_replayed_queued_message_does_not_use_the_cap(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charged: list[uuid.UUID] = []

    async def _count(source_id: uuid.UUID) -> int | None:
        charged.append(source_id)
        # Over the cap from the second charge on.
        return 30 if len(charged) > 1 else None

    monkeypatch.setattr(ingest_service, "check_source_rate", _count)
    envelope = sign("Notification", _s3_message(_s3_record()))

    first = await _post(client, source, envelope)
    replays = [await _post(client, source, envelope) for _ in range(3)]

    assert first.status_code == 200
    assert [r.status_code for r in replays] == [200, 200, 200]
    assert all(r.json()["duplicates"] == 1 for r in replays)
    assert charged == [source.id]
    # A new event is still charged, and refused once over the cap.
    other = await _post(
        client, source, sign("Notification", _s3_message(_s3_record("new.csv")))
    )
    assert other.status_code == 503
    assert len(await _outbox(session, source)) == 1


async def test_confirmations_have_their_own_cap(
    client: httpx.AsyncClient,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    confirm_calls: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifications_charged: list[uuid.UUID] = []

    async def _over_cap(_: uuid.UUID) -> int | None:
        return 5

    async def _count_notification(source_id: uuid.UUID) -> int | None:
        notifications_charged.append(source_id)
        return None

    monkeypatch.setattr(ingest_service, "check_confirmation_rate", _over_cap)
    monkeypatch.setattr(ingest_service, "check_source_rate", _count_notification)

    response = await _post(client, source, sign("SubscriptionConfirmation", "confirm"))

    assert response.status_code == 503
    assert confirm_calls == []
    # Confirmations never use the notification budget.
    assert notifications_charged == []


# ----- Subscription confirmation -----


async def test_confirmation_uses_the_api_and_never_fetches_subscribe_url(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    confirm_calls: list[tuple[str, str]],
) -> None:
    source.status = TriggerStatus.OFFLINE.value
    await session.commit()

    response = await _post(client, source, sign("SubscriptionConfirmation", "confirm"))

    assert response.status_code == 200, response.text
    assert response.json()["confirmed"] is True
    assert confirm_calls == [(TOPIC, "synthetic-token")]
    # The only outbound HTTP request was the certificate.
    assert [str(call.request.url) for call in aws.calls] == [CERT_URL]
    await session.refresh(source)
    assert source.subscription_status == SubscriptionStatus.CONFIRMED.value
    assert source.subscription_ref == SUBSCRIPTION_ARN


async def test_confirmation_for_another_topic_changes_nothing(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    confirm_calls: list[tuple[str, str]],
) -> None:
    envelope = sign("SubscriptionConfirmation", "confirm", topic=OTHER_TOPIC)

    response = await _post(client, source, envelope)

    assert response.status_code == 403
    assert confirm_calls == []
    await session.refresh(source)
    assert source.config == {"topic_arn": TOPIC}
    assert source.subscription_status == SubscriptionStatus.PENDING.value
    assert source.subscription_ref is None


async def test_confirmation_rejected_by_sns(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _reject(*, topic: SnsTopic, token: str) -> str:
        raise IngestError(IngestErrorCode.CONFIRMATION_REJECTED, "no")

    monkeypatch.setattr(ingest_service, "confirm_subscription", _reject)

    response = await _post(client, source, sign("SubscriptionConfirmation", "confirm"))

    assert response.status_code == 400
    await session.refresh(source)
    assert source.subscription_status == SubscriptionStatus.PENDING.value


async def test_unsubscribe_confirmation_resets_the_subscription(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
    confirm_calls: list[tuple[str, str]],
) -> None:
    source.subscription_status = SubscriptionStatus.CONFIRMED.value
    source.subscription_ref = SUBSCRIPTION_ARN
    await session.commit()

    response = await _post(
        client,
        source,
        sign("UnsubscribeConfirmation", _unsubscribe_text(SUBSCRIPTION_ARN)),
    )

    assert response.status_code == 200
    assert confirm_calls == []
    await session.refresh(source)
    assert source.subscription_status == SubscriptionStatus.PENDING.value
    assert source.subscription_ref is None


def _unsubscribe_text(subscription_arn: str) -> str:
    # The shape of the Message SNS sends with an UnsubscribeConfirmation.
    return (
        f"You have chosen to deactivate subscription {subscription_arn}."
        "\nTo cancel this operation and restore the subscription, visit the"
        " SubscribeURL included in this message."
    )


async def test_late_unsubscribe_for_an_older_subscription_changes_nothing(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    source.subscription_status = SubscriptionStatus.CONFIRMED.value
    source.subscription_ref = SUBSCRIPTION_ARN
    await session.commit()
    older = f"{TOPIC}:00000000-0000-0000-0000-000000000000"

    response = await _post(
        client, source, sign("UnsubscribeConfirmation", _unsubscribe_text(older))
    )

    assert response.status_code == 200
    await session.refresh(source)
    assert source.subscription_status == SubscriptionStatus.CONFIRMED.value
    assert source.subscription_ref == SUBSCRIPTION_ARN


# ----- ConfirmSubscription client -----


class _FakeSnsClient:
    def __init__(self, outcome: dict[str, str] | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, str]] = []

    async def __aenter__(self) -> _FakeSnsClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def confirm_subscription(self, **kwargs: str) -> dict[str, str]:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeSession:
    def __init__(self, client: _FakeSnsClient) -> None:
        self.client_obj = client
        self.client_kwargs: dict[str, Any] = {}

    def client(self, service: str, **kwargs: Any) -> _FakeSnsClient:
        assert service == "sns"
        self.client_kwargs = kwargs
        return self.client_obj


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "synthetic"}}, "ConfirmSubscription"
    )


async def test_confirm_subscription_calls_the_topic_region_unsigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSnsClient({"SubscriptionArn": SUBSCRIPTION_ARN})
    fake_session = _FakeSession(fake)
    monkeypatch.setattr(sns.aioboto3, "Session", lambda: fake_session)
    topic = SnsTopic.parse("arn:aws:sns:eu-west-2:123456789012:bucket-events")

    arn = await sns.confirm_subscription(topic=topic, token="synthetic-token")

    assert arn == SUBSCRIPTION_ARN
    # AuthenticateOnUnsubscribe would need a signed request.
    assert fake.calls == [{"TopicArn": topic.arn, "Token": "synthetic-token"}]
    assert fake_session.client_kwargs["region_name"] == "eu-west-2"
    assert fake_session.client_kwargs["config"].signature_version is UNSIGNED


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (_client_error("InvalidParameter"), IngestErrorCode.CONFIRMATION_REJECTED),
        (_client_error("Throttling"), IngestErrorCode.UNAVAILABLE),
    ],
)
async def test_confirm_subscription_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: IngestErrorCode
) -> None:
    monkeypatch.setattr(
        sns.aioboto3, "Session", lambda: _FakeSession(_FakeSnsClient(error))
    )
    with pytest.raises(IngestError) as exc_info:
        await sns.confirm_subscription(topic=SnsTopic.parse(TOPIC), token="t")
    assert exc_info.value.code == code


def test_topic_hosts_per_partition() -> None:
    assert SnsTopic.parse(TOPIC).sns_host == "sns.us-east-1.amazonaws.com"
    gov = SnsTopic.parse("arn:aws-us-gov:sns:us-gov-west-1:123456789012:t")
    assert gov.sns_host == "sns.us-gov-west-1.amazonaws.com"
    china = SnsTopic.parse("arn:aws-cn:sns:cn-north-1:123456789012:t")
    assert china.sns_host == "sns.cn-north-1.amazonaws.com.cn"


# ----- Rate cap counter -----


class _FakePipeline:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts
        self.key = ""

    def incr(self, key: str) -> None:
        self.key = key

    def expire(self, key: str, ttl: int, nx: bool) -> None:
        assert key == self.key and nx

    async def execute(self) -> list[int | bool]:
        self.counts[self.key] = self.counts.get(self.key, 0) + 1
        return [self.counts[self.key], True]


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self.counts)


class _FakeRedisClient:
    def __init__(self, redis: _FakeRedis | None) -> None:
        self.redis = redis

    async def _get_client(self) -> _FakeRedis:
        if self.redis is None:
            raise ConnectionError("redis is down")
        return self.redis


async def test_rate_cap_counts_per_source(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedisClient(_FakeRedis())

    async def _get() -> _FakeRedisClient:
        return fake

    monkeypatch.setattr(rate_limit, "get_redis_client", _get)
    monkeypatch.setattr(config, "TRACECAT__TRIGGERS_INGEST_RATE_LIMIT_PER_MINUTE", 2)
    source_a, source_b = uuid.uuid4(), uuid.uuid4()

    assert await rate_limit.check_source_rate(source_a) is None
    assert await rate_limit.check_source_rate(source_a) is None
    retry_after = await rate_limit.check_source_rate(source_a)
    assert retry_after is not None and 1 <= retry_after <= rate_limit.WINDOW_SECONDS
    assert await rate_limit.check_source_rate(source_b) is None


async def test_confirmation_cap_is_separate_from_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRedisClient(_FakeRedis())

    async def _get() -> _FakeRedisClient:
        return fake

    monkeypatch.setattr(rate_limit, "get_redis_client", _get)
    source_id = uuid.uuid4()

    for _ in range(rate_limit.CONFIRMATIONS_PER_MINUTE):
        assert await rate_limit.check_confirmation_rate(source_id) is None
    assert await rate_limit.check_confirmation_rate(source_id) is not None
    assert await rate_limit.check_source_rate(source_id) is None


async def test_rate_cap_fails_open_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _get() -> _FakeRedisClient:
        return _FakeRedisClient(None)

    monkeypatch.setattr(rate_limit, "get_redis_client", _get)

    assert await rate_limit.check_source_rate(uuid.uuid4()) is None


async def test_outbox_count_matches_queued(
    client: httpx.AsyncClient,
    session: AsyncSession,
    source: EventSource,
    sign: SignFn,
    aws: respx.MockRouter,
) -> None:
    for sequencer in ("01", "02", "03"):
        await _post(
            client,
            source,
            sign("Notification", _s3_message(_s3_record(sequencer=sequencer))),
        )
    count = await session.scalar(
        select(func.count())
        .select_from(EventOutbox)
        .where(EventOutbox.source_id == source.id)
    )
    assert count == 3
