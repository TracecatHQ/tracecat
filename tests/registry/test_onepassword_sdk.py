"""Regression tests for the generic 1Password SDK wrapper boundaries."""

import pytest
from onepassword import Client
from onepassword.core import InnerClient, UniffiCore
from onepassword.items import Items
from onepassword.types import (
    ItemListFilterByState,
    VaultCreateParams,
)
from onepassword.vaults import Vaults
from tracecat_registry.integrations import onepassword_sdk
from tracecat_registry.integrations.onepassword_sdk import (
    _get_sdk_method,
    _prepare_call,
    _serialize,
)


@pytest.mark.anyio
async def test_onepassword_client_uses_explicit_service_account_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    captured: dict[str, str] = {}

    class FakeClient:
        @classmethod
        async def authenticate(
            cls,
            *,
            auth: str,
            integration_name: str,
            integration_version: str,
        ) -> object:
            captured.update(
                auth=auth,
                integration_name=integration_name,
                integration_version=integration_version,
            )
            return sentinel

    monkeypatch.setattr(onepassword_sdk, "Client", FakeClient)
    monkeypatch.setattr(
        onepassword_sdk.secrets,
        "get",
        lambda name: "service-account-token",
    )

    assert await onepassword_sdk._get_client() is sentinel
    assert captured == {
        "auth": "service-account-token",
        "integration_name": "Tracecat",
        "integration_version": onepassword_sdk.__pep440_version__,
    }


@pytest.mark.parametrize(
    ("service", "method_name"),
    [
        ("_finalizer", "peek"),
        ("items._inner_client", "invoke"),
        ("items", "_private"),
    ],
)
def test_onepassword_dispatch_rejects_private_attributes(
    service: str,
    method_name: str,
) -> None:
    with pytest.raises(AttributeError, match="Unknown 1Password SDK method"):
        _get_sdk_method(Client(), service, method_name)


def test_onepassword_dispatch_resolves_public_sdk_methods() -> None:
    client = Client()
    client.items = Items(object.__new__(InnerClient))

    assert _get_sdk_method(client, "items", "list").__name__ == "list"
    assert _get_sdk_method(client, "items.files", "read").__name__ == "read"


def test_onepassword_dispatch_rejects_internal_module_traversal() -> None:
    inner_client = object.__new__(InnerClient)
    inner_client.core = UniffiCore()
    client = Client()
    client.items = Items(inner_client)

    with pytest.raises(AttributeError, match="Unknown 1Password SDK method"):
        _get_sdk_method(
            client,
            "items.inner_client.core.core.os",
            "system",
        )


def test_onepassword_prepares_generated_pydantic_parameters() -> None:
    method = object.__new__(Vaults).create

    args, kwargs = _prepare_call(
        method,
        {"params": {"title": "Engineering", "allowAdminsAccess": True}},
    )

    assert args == []
    assert kwargs == {
        "params": VaultCreateParams(
            title="Engineering",
            allowAdminsAccess=True,
        )
    }


def test_onepassword_preserves_primitive_and_null_parameters() -> None:
    method = object.__new__(Items).get

    args, kwargs = _prepare_call(
        method,
        {"vault_id": None, "item_id": "item-123"},
    )

    assert args == []
    assert kwargs == {"vault_id": None, "item_id": "item-123"}


def test_onepassword_prepares_variadic_sdk_filters() -> None:
    method = object.__new__(Items).list

    args, kwargs = _prepare_call(
        method,
        {
            "vault_id": "vault-123",
            "filters": [
                {
                    "content": {
                        "active": True,
                        "archived": False,
                    }
                }
            ],
        },
    )

    assert args[0] == "vault-123"
    assert isinstance(args[1], ItemListFilterByState)
    assert args[1].content.active is True
    assert args[1].content.archived is False
    assert kwargs == {}


def test_onepassword_serializes_sdk_models_with_wire_aliases() -> None:
    value = VaultCreateParams(
        title="Engineering",
        allowAdminsAccess=True,
    )

    assert _serialize(value) == {
        "title": "Engineering",
        "description": None,
        "allowAdminsAccess": True,
    }


def test_onepassword_serializes_binary_responses() -> None:
    assert _serialize(b"\x89PNG\r\n") == {
        "content_base64": "iVBORw0K",
    }


def test_onepassword_bounds_binary_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onepassword_sdk, "TRACECAT__MAX_FILE_SIZE_BYTES", 4)

    with pytest.raises(ValueError, match="binary response exceeds maximum size"):
        _serialize(b"12345")


def test_onepassword_direct_dispatch_rejects_iterators() -> None:
    with pytest.raises(TypeError, match="does not expose paginated iterator methods"):
        _serialize(iter([{"id": "item-123"}]))
