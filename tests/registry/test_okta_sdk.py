from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict, Field
from tracecat_registry import SecretNotFoundError
from tracecat_registry.integrations import okta_sdk


class FakeOktaModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    last_updated: datetime = Field(alias="lastUpdated")


def _secret_getter(values: dict[str, str]) -> Any:
    return lambda key, default=None: values.get(key, default)


def test_build_config_prefers_oauth_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        okta_sdk.secrets,
        "get_or_default",
        _secret_getter(
            {
                "OKTA_BASE_URL": "https://example.okta.com/",
                "OKTA_SERVICE_TOKEN": "service-token",
                "OKTA_API_TOKEN": "api-token",
            }
        ),
    )

    config = okta_sdk._build_okta_config()

    assert config["orgUrl"] == "https://example.okta.com"
    assert config["authorizationMode"] == "Bearer"
    assert config["token"] == "service-token"


def test_build_config_supports_ssws(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "OKTA_BASE_URL": "https://example.okta.com",
        "OKTA_API_TOKEN": "api-token",
    }
    monkeypatch.setattr(okta_sdk.secrets, "get_or_default", _secret_getter(values))
    monkeypatch.setattr(okta_sdk.secrets, "get", lambda key: values[key])

    config = okta_sdk._build_okta_config(auth_mode="ssws")

    assert config["authorizationMode"] == "SSWS"
    assert config["token"] == "api-token"


def test_build_config_supports_oauth_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "OKTA_BASE_URL": "https://example.okta.com",
        "OKTA_ACCESS_TOKEN": "access-token",
    }
    monkeypatch.setattr(okta_sdk.secrets, "get_or_default", _secret_getter(values))
    monkeypatch.setattr(okta_sdk.secrets, "get", lambda key: values[key])

    config = okta_sdk._build_okta_config(auth_mode="oauth")

    assert config["authorizationMode"] == "Bearer"
    assert config["token"] == "access-token"


def test_okta_secret_form_accepts_all_sdk_auth_keys() -> None:
    assert okta_sdk.okta_secret.optional_keys == [
        "OKTA_BASE_URL",
        "OKTA_API_TOKEN",
        "OKTA_ACCESS_TOKEN",
        "OKTA_SERVICE_TOKEN",
        "OKTA_CLIENT_ID",
        "OKTA_PRIVATE_KEY",
        "OKTA_SCOPES",
        "OKTA_KID",
        "OKTA_DPOP_ENABLED",
        "OKTA_DPOP_KEY_ROTATION_INTERVAL",
    ]
    assert [secret.name for secret in okta_sdk.OKTA_SDK_SECRETS] == [
        "okta",
        "okta_oauth",
    ]


def test_build_config_supports_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "OKTA_BASE_URL": "https://example.okta.com",
        "OKTA_CLIENT_ID": "client-id",
        "OKTA_PRIVATE_KEY": "pem",
        "OKTA_SCOPES": "okta.users.read, okta.groups.read",
        "OKTA_KID": "kid",
    }
    monkeypatch.setattr(okta_sdk.secrets, "get_or_default", _secret_getter(values))
    monkeypatch.setattr(okta_sdk.secrets, "get", lambda key: values[key])

    config = okta_sdk._build_okta_config(auth_mode="private_key")

    assert config["authorizationMode"] == "PrivateKey"
    assert config["clientId"] == "client-id"
    assert config["privateKey"] == "pem"
    assert config["scopes"] == ["okta.users.read", "okta.groups.read"]
    assert config["kid"] == "kid"


def test_build_config_supports_private_key_dpop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "OKTA_BASE_URL": "https://example.okta.com",
        "OKTA_CLIENT_ID": "client-id",
        "OKTA_PRIVATE_KEY": "pem",
        "OKTA_SCOPES": "okta.users.read",
        "OKTA_DPOP_ENABLED": "true",
        "OKTA_DPOP_KEY_ROTATION_INTERVAL": "7200",
    }
    monkeypatch.setattr(okta_sdk.secrets, "get_or_default", _secret_getter(values))
    monkeypatch.setattr(okta_sdk.secrets, "get", lambda key: values[key])

    config = okta_sdk._build_okta_config(auth_mode="private_key")

    assert config["authorizationMode"] == "PrivateKey"
    assert config["dpopEnabled"] is True
    assert config["dpopKeyRotationInterval"] == 7200


def test_build_config_requires_auth_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        okta_sdk.secrets,
        "get_or_default",
        _secret_getter({"OKTA_BASE_URL": "https://example.okta.com"}),
    )

    with pytest.raises(SecretNotFoundError, match="one auth source"):
        okta_sdk._build_okta_config()


def test_jsonable_preserves_okta_model_readonly_fields() -> None:
    model = FakeOktaModel(
        id="00u123",
        lastUpdated=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )

    assert okta_sdk._jsonable(model) == {
        "id": "00u123",
        "lastUpdated": "2026-01-02T03:04:05Z",
    }


@pytest.mark.anyio
async def test_call_method_uses_sdk_method(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SimpleNamespace(
        list_users=AsyncMock(
            return_value=(
                [
                    FakeOktaModel(
                        id="00u123",
                        lastUpdated=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                    )
                ],
                None,
                None,
            )
        )
    )
    monkeypatch.setattr(okta_sdk, "_build_okta_client", lambda **_kwargs: client)

    result = await okta_sdk.call_method(
        method_name="list_users",
        params={"limit": 1},
        base_url="https://example.okta.com",
    )

    assert result == [{"id": "00u123", "lastUpdated": "2026-01-02T03:04:05Z"}]
    client.list_users.assert_awaited_once_with(limit=1)


def test_add_group_request_preserves_custom_profile_attributes() -> None:
    # Custom (schema-unknown) profile attributes must survive into the request.
    # The SDK only routes unknown profile fields into `additional_properties`
    # when built via `AddGroupRequest.from_dict()`; a raw dict coerced by
    # `@validate_call` would silently drop them.
    from okta.models.add_group_request import AddGroupRequest

    request = AddGroupRequest.from_dict(
        {
            "profile": {
                "name": "Engineers",
                "description": "Eng team",
                "costCenter": "CC-42",
            }
        }
    )
    assert request is not None

    body = request.to_dict()
    assert body["profile"] == {
        "name": "Engineers",
        "description": "Eng team",
        "costCenter": "CC-42",
    }


@pytest.mark.anyio
async def test_add_group_passes_request_with_custom_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from okta.models.add_group_request import AddGroupRequest

    client = SimpleNamespace(add_group=AsyncMock(return_value=({"id": "00g1"}, None)))
    monkeypatch.setattr(okta_sdk, "_build_okta_client", lambda **_kwargs: client)

    await okta_sdk.add_group(
        group={"profile": {"name": "Engineers", "costCenter": "CC-42"}},
        base_url="https://example.okta.com",
    )

    client.add_group.assert_awaited_once()
    sent_group = client.add_group.await_args.kwargs["group"]
    assert isinstance(sent_group, AddGroupRequest)
    assert sent_group.to_dict()["profile"]["costCenter"] == "CC-42"


@pytest.mark.anyio
async def test_replace_group_passes_request_with_custom_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from okta.models.add_group_request import AddGroupRequest

    client = SimpleNamespace(
        replace_group=AsyncMock(return_value=({"id": "00g1"}, None))
    )
    monkeypatch.setattr(okta_sdk, "_build_okta_client", lambda **_kwargs: client)

    await okta_sdk.replace_group(
        group_id="00g1",
        group={"profile": {"name": "Engineers", "costCenter": "CC-42"}},
        base_url="https://example.okta.com",
    )

    client.replace_group.assert_awaited_once()
    await_kwargs = client.replace_group.await_args.kwargs
    assert await_kwargs["group_id"] == "00g1"
    sent_group = await_kwargs["group"]
    assert isinstance(sent_group, AddGroupRequest)
    assert sent_group.to_dict()["profile"]["costCenter"] == "CC-42"


@pytest.mark.anyio
async def test_call_method_rejects_private_method() -> None:
    with pytest.raises(ValueError, match="cannot start"):
        await okta_sdk.call_method(method_name="_private")


@pytest.mark.anyio
async def test_call_method_raises_two_tuple_sdk_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(
        delete_user=AsyncMock(return_value=(None, ValueError("bad")))
    )
    monkeypatch.setattr(okta_sdk, "_build_okta_client", lambda **_kwargs: client)

    with pytest.raises(ValueError, match="bad"):
        await okta_sdk.call_method(method_name="delete_user", params={"id": "00u123"})


@pytest.mark.anyio
async def test_call_paginated_method_follows_link_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_response = SimpleNamespace(
        headers={
            "Link": '<https://example.okta.com/api/v1/users?after=abc>; rel="next"'
        }
    )
    second_response = SimpleNamespace(headers={})
    client = SimpleNamespace(
        list_users=AsyncMock(
            side_effect=[
                ([{"id": "00u1"}], first_response, None),
                ([{"id": "00u2"}], second_response, None),
            ]
        )
    )
    monkeypatch.setattr(okta_sdk, "_build_okta_client", lambda **_kwargs: client)

    result = await okta_sdk.call_paginated_method(
        method_name="list_users",
        params={"q": "alice"},
        limit=1,
        base_url="https://example.okta.com",
    )

    assert result == {
        "items": [{"id": "00u1"}, {"id": "00u2"}],
        "pages": 2,
        "next_after": None,
    }
    assert client.list_users.await_args_list[0].kwargs == {
        "q": "alice",
        "limit": 1,
    }
    assert client.list_users.await_args_list[1].kwargs == {
        "q": "alice",
        "limit": 1,
        "after": "abc",
    }
