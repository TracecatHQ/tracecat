"""Core resolution of externally backed secrets, independent of any provider.

A fake provider stands in for a second provider, so the shared safety rules
are proven without AWS.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock

import aioboto3
import orjson
import pytest
from botocore.exceptions import ClientError
from tracecat_ee.secrets import resolver
from tracecat_ee.secrets.errors import SecretResolutionError
from tracecat_ee.secrets.providers.aws_secrets_manager import (
    AwsSecretResolutionError,
)

from tests.unit.test_aws_secrets_manager import _FakeSession
from tests.unit.test_aws_secrets_manager import make_reference as make_aws_reference
from tracecat.auth import sandbox as auth_sandbox
from tracecat.auth.types import Role
from tracecat.db.models import Secret
from tracecat.secrets.enums import (
    SecretMappingMode,
    SecretResolutionErrorCode,
)
from tracecat.secrets.schemas import SecretJsonField, SecretKeyMapping
from tracecat.secrets.types import (
    ExternalSecretReference,
    Failure,
    FetchOutcome,
    StoreContext,
)

pytestmark = pytest.mark.anyio

FAKE_PROVIDER: Any = "fake_kv"
"""Not a ``SecretStoreProvider`` member: the provider only exists in this test."""
FAKE_CONFIG: Any = object()
"""Core passes the config through to the provider without reading it."""
USER_VALUE = "fetched-user-value"
PASS_VALUE = "fetched-pass-value"
STORE_ID = uuid.uuid4()


class FakeKvBackend:
    """A provider that records its calls and keeps the outcomes it returned."""

    provider = FAKE_PROVIDER
    display_name = "FakeKV"

    def __init__(self) -> None:
        self.calls: list[tuple[StoreContext, list[str]]] = []
        self.outcomes: dict[str, FetchOutcome] = {}

    def resolution_error(
        self,
        code: SecretResolutionErrorCode,
        *,
        alias: str,
        environment: str,
        vendor_code: str | None,
    ) -> SecretResolutionError:
        return SecretResolutionError(
            code,
            alias=alias,
            environment=environment,
            provider_label=self.display_name,
            vendor_code=vendor_code,
        )

    async def fetch(
        self, store: StoreContext, references: Sequence[str]
    ) -> dict[str, FetchOutcome]:
        self.calls.append((store, list(references)))
        self.outcomes = {
            "kv/app": orjson.dumps({"user": USER_VALUE, "pass": PASS_VALUE}).decode(),
            "kv/missing": Failure(SecretResolutionErrorCode.NOT_FOUND, "http_404"),
        }
        return self.outcomes


def _reference(alias: str, key: str, fields: dict[str, str]) -> ExternalSecretReference:
    return ExternalSecretReference(
        secret_id=uuid.uuid4(),
        alias=alias,
        environment="default",
        store_id=STORE_ID,
        provider=FAKE_PROVIDER,
        store_enabled=True,
        store_config=FAKE_CONFIG,
        key=key,
        mapping=SecretKeyMapping(
            mode=SecretMappingMode.JSON,
            fields=[SecretJsonField(key=k, field=f) for k, f in fields.items()],
        ),
    )


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"secret:read"}),
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeKvBackend:
    backend = FakeKvBackend()
    registry = {FAKE_PROVIDER: backend}
    monkeypatch.setattr(resolver, "get_backend", registry.__getitem__)
    return backend


@pytest.fixture
def references() -> list[ExternalSecretReference]:
    # Three keys from two references in one store; the second is missing.
    return [
        _reference("app", "kv/app", {"USER": "user", "PASS": "pass"}),
        _reference("other", "kv/missing", {"TOKEN": "token"}),
    ]


def _assert_no_frame_holds_values(error: BaseException) -> None:
    tb = error.__traceback__
    while tb is not None:
        if not tb.tb_frame.f_globals.get("__name__", "").startswith("tracecat"):
            tb = tb.tb_next
            continue
        for name, value in tb.tb_frame.f_locals.items():
            for payload in (USER_VALUE, PASS_VALUE):
                assert payload not in repr(value), (
                    f"{tb.tb_frame.f_code.co_name}.{name} retains a fetched value"
                )
        tb = tb.tb_next


async def test_second_provider_fails_whole_batch_sanitized_and_cleaned_up(
    fake: FakeKvBackend, references: list[ExternalSecretReference]
) -> None:
    # Given a fake provider returning a JSON value and a not_found
    # When a run resolves three keys from two references in that store
    with pytest.raises(SecretResolutionError) as exc_info:
        await resolver.resolve_external_references(references)

    # Then core fetched once with the two distinct references
    assert [(store.store_id, refs) for store, refs in fake.calls] == [
        (STORE_ID, ["kv/app", "kv/missing"])
    ]
    # and the run fails with the sanitized not_found message and no vendor text
    error = exc_info.value
    assert error.code == SecretResolutionErrorCode.NOT_FOUND
    assert str(error) == (
        "Failed to resolve FakeKV-backed secret 'other' in environment 'default':"
        " not_found (FakeKV error code http_404)"
    )
    assert error.__cause__ is None and error.__context__ is None
    for payload in (USER_VALUE, PASS_VALUE):
        assert payload not in str(error) and payload not in repr(error.detail)
    # and the fetched values were dropped,
    assert fake.outcomes == {}
    # and no frame in the traceback holds a fetched value
    _assert_no_frame_holds_values(error)


async def test_failed_resolution_leaves_no_values_in_the_environment(
    fake: FakeKvBackend,
    references: list[ExternalSecretReference],
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
) -> None:
    rows = [
        Secret(id=ref.secret_id, name=ref.alias, source="fake_kv") for ref in references
    ]
    by_name = {ref.alias: ref for ref in references}
    monkeypatch.setattr(
        auth_sandbox.AuthSandbox, "_get_secrets", AsyncMock(return_value=rows)
    )
    monkeypatch.setattr(
        auth_sandbox,
        "build_external_secret_reference",
        lambda secret: by_name[secret.name],
    )
    sandbox = auth_sandbox.AuthSandbox(role=role, secrets=["app", "other"])

    with pytest.raises(SecretResolutionError) as exc_info:
        await sandbox.__aenter__()

    assert sandbox.secrets == {}
    assert sandbox._external_values == {}
    assert fake.outcomes == {}
    _assert_no_frame_holds_values(exc_info.value)


async def test_provider_bug_fails_as_unknown_without_its_type_or_message(
    fake: FakeKvBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a provider whose fetch raises instead of returning outcomes
    async def explode(
        store: StoreContext, references: Sequence[str]
    ) -> dict[str, FetchOutcome]:
        raise RuntimeError("vendor text that must not surface")

    monkeypatch.setattr(fake, "fetch", explode)

    # When a run resolves a reference in its store
    with pytest.raises(SecretResolutionError) as exc_info:
        await resolver.resolve_external_references(
            [_reference("app", "kv/app", {"USER": "user"})]
        )

    # Then it fails as unknown, naming neither the exception type nor its text
    error = exc_info.value
    assert error.code == SecretResolutionErrorCode.UNKNOWN
    assert str(error) == (
        "Failed to resolve FakeKV-backed secret 'app' in environment 'default': unknown"
    )
    assert error.detail == {
        "secret_name": "app",
        "environment": "default",
        "error_code": "unknown",
        "provider_error_code": None,
    }


async def test_json_value_projects_onto_declared_keys(fake: FakeKvBackend) -> None:
    resolved = await resolver.resolve_external_references(
        [_reference("app", "kv/app", {"USER": "user", "PASS": "pass"})]
    )
    assert resolved == {"app": {"USER": USER_VALUE, "PASS": PASS_VALUE}}
    assert fake.calls[0][1] == ["kv/app"]
    # The provider's outcomes are dropped on success too
    assert fake.outcomes == {}


async def test_aws_run_time_error_keeps_the_aws_error_type_and_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run raises the AWS error type with ``aws_error_code``, as before."""
    _FakeSession.reset()
    _FakeSession.sm_error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "redacted"}}, "Op"
    )
    monkeypatch.setattr(aioboto3, "Session", _FakeSession)

    with pytest.raises(AwsSecretResolutionError) as exc_info:
        await resolver.resolve_external_references([make_aws_reference()])

    # The run's error record carries the type name, as it did before.
    assert type(exc_info.value).__name__ == "AwsSecretResolutionError"
    assert exc_info.value.aws_error_code == "AccessDeniedException"
    assert exc_info.value.detail == {
        "secret_name": "api",
        "environment": "default",
        "error_code": "access_denied",
        "aws_error_code": "AccessDeniedException",
    }
