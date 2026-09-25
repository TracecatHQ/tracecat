"""Unit tests for the AWS Secrets Manager runtime resolver.

All AWS SDK calls are stubbed; no network access is performed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Self

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from tracecat_ee.secrets.providers import aws_secrets_manager as asm
from tracecat_ee.secrets.providers.aws_secrets_manager import (
    AwsSecretResolutionError,
    check_aws_secret_reference,
    project_secret_string,
    resolve_aws_secret_references,
)

from tracecat.secrets.enums import (
    AwsSecretMappingMode,
    AwsSecretResolutionErrorCode,
    SecretStoreProvider,
)
from tracecat.secrets.schemas import (
    AwsSecretJsonField,
    AwsSecretKeyMapping,
    AwsSecretsManagerStoreConfig,
)
from tracecat.secrets.types import CheckResult, ExternalSecretReference

pytestmark = pytest.mark.anyio

ROLE_ARN = "arn:aws:iam::123456789012:role/tracecat-secrets-reader"
REGION = "us-east-1"
SECRET_ARN = f"arn:aws:secretsmanager:{REGION}:123456789012:secret:app/api-AbCdEf"
EXTERNAL_ID = "tracecat-external-id"
STORE_ID = uuid.uuid4()


def make_reference(
    *,
    alias: str = "api",
    mode: AwsSecretMappingMode = AwsSecretMappingMode.WHOLE_STRING,
    whole_string_key: str = "TOKEN",
    fields: tuple[AwsSecretJsonField, ...] = (),
    secret_arn: str = SECRET_ARN,
    region: str = REGION,
    store_enabled: bool = True,
    role_arn: str = ROLE_ARN,
    external_id: str = EXTERNAL_ID,
) -> ExternalSecretReference:
    return ExternalSecretReference(
        secret_id=uuid.uuid4(),
        alias=alias,
        environment="default",
        store_id=STORE_ID,
        provider=SecretStoreProvider.AWS_SECRETS_MANAGER,
        store_enabled=store_enabled,
        store_config=AwsSecretsManagerStoreConfig(
            role_arn=role_arn, region=region, external_id=external_id
        ),
        key=secret_arn,
        mapping=AwsSecretKeyMapping(
            mode=mode,
            keys=[whole_string_key]
            if mode == AwsSecretMappingMode.WHOLE_STRING
            else [],
            fields=list(fields),
        ),
    )


class _FakeClient:
    def __init__(self, recorder: _FakeSession, service: str) -> None:
        self._recorder = recorder
        self._service = service

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def assume_role(self, **kwargs: Any) -> dict[str, Any]:
        self._recorder.sts_calls.append(kwargs)
        if self._recorder.sts_error is not None:
            raise self._recorder.sts_error
        return {
            "Credentials": {
                "AccessKeyId": "ASIA-TEST",
                "SecretAccessKey": "test-secret",
                "SessionToken": "test-token",
            }
        }

    async def get_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        self._recorder.sm_calls.append(kwargs)
        if self._recorder.sm_error is not None:
            raise self._recorder.sm_error
        return self._recorder.sm_response


class _FakeSession:
    """Stands in for ``aioboto3.Session`` and records every SDK call."""

    sts_calls: list[dict[str, Any]] = []
    sm_calls: list[dict[str, Any]] = []
    client_kwargs: list[dict[str, Any]] = []
    sts_error: BaseException | None = None
    sm_error: BaseException | None = None
    sm_response: dict[str, Any] = {"SecretString": "plain-value"}

    def __init__(self, region_name: str | None = None) -> None:
        self.region_name = region_name
        type(self).client_kwargs.append({"region_name": region_name})

    def client(self, service: str, **kwargs: Any) -> _FakeClient:
        type(self).client_kwargs.append({"service": service, **kwargs})
        return _FakeClient(self, service)

    @classmethod
    def reset(cls) -> None:
        cls.sts_calls = []
        cls.sm_calls = []
        cls.client_kwargs = []
        cls.sts_error = None
        cls.sm_error = None
        cls.sm_response = {"SecretString": "plain-value"}


@pytest.fixture
def fake_aws(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSession]:
    _FakeSession.reset()
    monkeypatch.setattr(asm.aioboto3, "Session", _FakeSession)
    return _FakeSession


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "redacted"}}, "Op")


async def test_whole_string_exact_sdk_calls(fake_aws: type[_FakeSession]) -> None:
    fake_aws.sm_response = {"SecretString": '  {"looks": "json"} \n'}
    ref = make_reference()

    resolved = await resolve_aws_secret_references([ref])

    assert resolved == {"api": {"TOKEN": '  {"looks": "json"} \n'}}
    assert fake_aws.sts_calls == [
        {
            "RoleArn": ROLE_ARN,
            "RoleSessionName": asm._role_session_name(ref),
            "ExternalId": EXTERNAL_ID,
        }
    ]
    assert fake_aws.sm_calls == [{"SecretId": SECRET_ARN, "VersionStage": "AWSCURRENT"}]
    sm_kwargs = next(
        k for k in fake_aws.client_kwargs if k.get("service") == "secretsmanager"
    )
    assert sm_kwargs["aws_access_key_id"] == "ASIA-TEST"
    assert sm_kwargs["aws_session_token"] == "test-token"
    assert "endpoint_url" not in sm_kwargs
    assert {"region_name": REGION} in fake_aws.client_kwargs


async def test_json_mapping_selects_declared_fields(
    fake_aws: type[_FakeSession],
) -> None:
    fake_aws.sm_response = {
        "SecretString": '{"username": "u", "password": "p", "extra": 1}'
    }
    ref = make_reference(
        mode=AwsSecretMappingMode.JSON,
        fields=(
            AwsSecretJsonField(key="USER", field="username"),
            AwsSecretJsonField(key="PASS", field="password"),
        ),
    )

    resolved = await resolve_aws_secret_references([ref])

    assert resolved == {"api": {"USER": "u", "PASS": "p"}}


@pytest.mark.parametrize(
    ("secret_string", "expected"),
    [
        ("not json", AwsSecretResolutionErrorCode.MALFORMED_JSON),
        ("[1, 2]", AwsSecretResolutionErrorCode.MALFORMED_JSON),
        ('{"other": "x"}', AwsSecretResolutionErrorCode.MISSING_FIELD),
        ('{"username": 42}', AwsSecretResolutionErrorCode.NON_STRING_FIELD),
    ],
)
def test_json_projection_failures(
    secret_string: str, expected: AwsSecretResolutionErrorCode
) -> None:
    ref = make_reference(
        mode=AwsSecretMappingMode.JSON,
        fields=(AwsSecretJsonField(key="USER", field="username"),),
    )
    assert project_secret_string(ref, secret_string) == expected


def test_whole_string_projection_is_verbatim() -> None:
    ref = make_reference()
    assert project_secret_string(ref, " a b ") == {"TOKEN": " a b "}


async def test_binary_value_rejected(fake_aws: type[_FakeSession]) -> None:
    fake_aws.sm_response = {"SecretBinary": b"\x00\x01"}
    with pytest.raises(AwsSecretResolutionError) as exc_info:
        await resolve_aws_secret_references([make_reference()])
    assert exc_info.value.code == AwsSecretResolutionErrorCode.BINARY_VALUE


async def test_disabled_store_and_region_mismatch_skip_network(
    fake_aws: type[_FakeSession],
) -> None:
    with pytest.raises(AwsSecretResolutionError) as disabled:
        await resolve_aws_secret_references([make_reference(store_enabled=False)])
    assert disabled.value.code == AwsSecretResolutionErrorCode.STORE_DISABLED

    with pytest.raises(AwsSecretResolutionError) as mismatch:
        await resolve_aws_secret_references([make_reference(region="eu-west-1")])
    assert mismatch.value.code == AwsSecretResolutionErrorCode.REGION_MISMATCH
    assert fake_aws.sts_calls == []
    assert fake_aws.sm_calls == []


async def test_friendly_name_reference_skips_region_check(
    fake_aws: type[_FakeSession],
) -> None:
    fake_aws.sm_response = {"SecretString": "by-name"}
    resolved = await resolve_aws_secret_references(
        [make_reference(secret_arn="prod/app/api-key", region="eu-west-1")]
    )
    assert resolved == {"api": {"TOKEN": "by-name"}}
    assert fake_aws.sm_calls == [
        {"SecretId": "prod/app/api-key", "VersionStage": "AWSCURRENT"}
    ]


@pytest.mark.parametrize(
    ("make_error", "target", "expected", "aws_code"),
    [
        (
            lambda: _client_error("AccessDenied"),
            "sts",
            AwsSecretResolutionErrorCode.ASSUME_ROLE_FAILED,
            "AccessDenied",
        ),
        (
            lambda: _client_error("ResourceNotFoundException"),
            "sm",
            AwsSecretResolutionErrorCode.NOT_FOUND,
            "ResourceNotFoundException",
        ),
        (
            lambda: _client_error("AccessDeniedException"),
            "sm",
            AwsSecretResolutionErrorCode.ACCESS_DENIED,
            "AccessDeniedException",
        ),
        (
            lambda: _client_error("DecryptionFailure"),
            "sm",
            AwsSecretResolutionErrorCode.DECRYPTION_FAILED,
            "DecryptionFailure",
        ),
        (
            lambda: _client_error("ThrottlingException"),
            "sm",
            AwsSecretResolutionErrorCode.THROTTLED,
            "ThrottlingException",
        ),
        (
            lambda: EndpointConnectionError(endpoint_url="https://example.invalid"),
            "sm",
            AwsSecretResolutionErrorCode.TIMEOUT,
            None,
        ),
        (
            NoCredentialsError,
            "sts",
            AwsSecretResolutionErrorCode.ASSUME_ROLE_FAILED,
            None,
        ),
    ],
)
async def test_sdk_errors_are_classified_by_type_and_code(
    fake_aws: type[_FakeSession],
    make_error: Callable[[], BaseException],
    target: str,
    expected: AwsSecretResolutionErrorCode,
    aws_code: str | None,
) -> None:
    if target == "sts":
        fake_aws.sts_error = make_error()
    else:
        fake_aws.sm_error = make_error()

    with pytest.raises(AwsSecretResolutionError) as exc_info:
        await resolve_aws_secret_references([make_reference()])

    err = exc_info.value
    assert err.code == expected
    assert err.aws_error_code == aws_code
    assert err.__cause__ is None
    assert err.__context__ is None
    assert "redacted" not in str(err)


async def test_failure_message_never_contains_payload(
    fake_aws: type[_FakeSession],
) -> None:
    fake_aws.sm_response = {"SecretString": "super-secret-payload"}
    ref = make_reference(
        mode=AwsSecretMappingMode.JSON,
        fields=(AwsSecretJsonField(key="K", field="k"),),
    )
    with pytest.raises(AwsSecretResolutionError) as exc_info:
        await resolve_aws_secret_references([ref])
    err = exc_info.value
    assert err.code == AwsSecretResolutionErrorCode.MALFORMED_JSON
    assert "super-secret-payload" not in str(err)
    assert "super-secret-payload" not in repr(err.detail)
    assert err.__cause__ is None and err.__context__ is None


async def test_traceback_frame_locals_never_contain_payload(
    fake_aws: type[_FakeSession],
) -> None:
    """A later reference failing must not leave earlier plaintext in the traceback."""
    payload = "resolved-plaintext-payload"
    fake_aws.sm_response = {"SecretString": payload}
    ok = make_reference(alias="ok", secret_arn=SECRET_ARN)
    bad = make_reference(
        alias="bad",
        secret_arn=SECRET_ARN.replace("api", "creds"),
        mode=AwsSecretMappingMode.JSON,
        fields=(AwsSecretJsonField(key="K", field="k"),),
    )
    with pytest.raises(AwsSecretResolutionError) as exc_info:
        await resolve_aws_secret_references([ok, bad])

    tb = exc_info.value.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") != asm.__name__:
            tb = tb.tb_next
            continue
        for name, value in tb.tb_frame.f_locals.items():
            assert payload not in repr(value), (
                f"{tb.tb_frame.f_code.co_name}.{name} retains a fetched value"
            )
        tb = tb.tb_next


async def test_reads_deduplicated_per_store_and_reference(
    fake_aws: type[_FakeSession],
) -> None:
    fake_aws.sm_response = {"SecretString": '{"a": "1", "b": "2"}'}
    first = make_reference(
        alias="alpha",
        mode=AwsSecretMappingMode.JSON,
        fields=(AwsSecretJsonField(key="A", field="a"),),
    )
    second = make_reference(
        alias="beta",
        mode=AwsSecretMappingMode.JSON,
        fields=(AwsSecretJsonField(key="B", field="b"),),
    )
    other_arn = SECRET_ARN.replace("app/api", "app/other")
    third = make_reference(alias="gamma", secret_arn=other_arn)

    resolved = await resolve_aws_secret_references([first, second, third])

    assert resolved == {
        "alpha": {"A": "1"},
        "beta": {"B": "2"},
        "gamma": {"TOKEN": '{"a": "1", "b": "2"}'},
    }
    assert len(fake_aws.sts_calls) == 2
    assert sorted(c["SecretId"] for c in fake_aws.sm_calls) == sorted(
        [SECRET_ARN, other_arn]
    )

    # No cross-operation caching: a second call hits AWS again.
    await resolve_aws_secret_references([first])
    assert len(fake_aws.sm_calls) == 3


async def test_stores_use_their_own_region_and_external_id(
    fake_aws: type[_FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_reference(alias="east", secret_arn="app/api")
    second = replace(
        first,
        alias="west",
        store_id=uuid.uuid4(),
        store_config=AwsSecretsManagerStoreConfig(
            role_arn=ROLE_ARN,
            region="eu-west-1",
            external_id="tracecat-other-external-id",
        ),
    )

    async def get_secret_value(client: _FakeClient, **kwargs: Any) -> dict[str, str]:
        client._recorder.sm_calls.append(kwargs)
        return {"SecretString": client._recorder.region_name or ""}

    monkeypatch.setattr(_FakeClient, "get_secret_value", get_secret_value)
    resolved = await resolve_aws_secret_references([first, second])
    assert resolved == {"east": {"TOKEN": REGION}, "west": {"TOKEN": "eu-west-1"}}
    assert {call["ExternalId"] for call in fake_aws.sts_calls} == {
        first.store_config.external_id,
        second.store_config.external_id,
    }
    assert len(fake_aws.sm_calls) == 2


async def test_empty_references_do_not_touch_aws(
    fake_aws: type[_FakeSession],
) -> None:
    assert await resolve_aws_secret_references([]) == {}
    assert fake_aws.client_kwargs == []


async def test_check_reference_returns_keys_only(
    fake_aws: type[_FakeSession],
) -> None:
    fake_aws.sm_response = {"SecretString": '{"username": "u"}'}
    ref = make_reference(
        mode=AwsSecretMappingMode.JSON,
        fields=(AwsSecretJsonField(key="USER", field="username"),),
    )
    result = await check_aws_secret_reference(ref)
    assert result == CheckResult(ok=True, resolved_keys=["USER"])

    fake_aws.sm_error = _client_error("ResourceNotFoundException")
    result = await check_aws_secret_reference(ref)
    assert result == CheckResult(
        ok=False,
        error_code=AwsSecretResolutionErrorCode.NOT_FOUND,
        provider_error_code="ResourceNotFoundException",
    )

    fake_aws.sm_error = None
    fake_aws.sm_response = {"SecretString": '{"username": 1}'}
    result = await check_aws_secret_reference(ref)
    assert result == CheckResult(
        ok=False, error_code=AwsSecretResolutionErrorCode.NON_STRING_FIELD
    )


def test_output_keys_are_metadata_only() -> None:
    ref = make_reference(
        mode=AwsSecretMappingMode.JSON,
        fields=(
            AwsSecretJsonField(key="USER", field="username"),
            AwsSecretJsonField(key="PASS", field="password"),
        ),
    )
    assert ref.mapping.output_keys() == ["USER", "PASS"]
    assert make_reference().mapping.output_keys() == ["TOKEN"]
