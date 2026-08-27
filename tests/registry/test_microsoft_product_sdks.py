"""Credential-isolation tests for the product-scoped Microsoft Graph SDKs.

`tools.microsoft_graph_security_sdk` and `tools.microsoft_outlook_sdk` share one
transport with `tools.microsoft_graph_sdk`, so the request pipeline itself is
covered once in `test_microsoft_graph_sdk.py`. What is product-specific, and what
is tested here, is which OAuth token each namespace is allowed to pick up: its own
first, the generic Microsoft Graph token as a fallback, and never the other
product's token.
"""

import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecretType,
    SecretNotFoundError,
)
from tracecat_registry.integrations import microsoft_graph_security_sdk as security
from tracecat_registry.integrations import microsoft_outlook_sdk as outlook
from tracecat_registry.integrations._microsoft_graph_transport import (
    GraphProduct,
    MicrosoftGraphAuthMode,
)

# Mirrors of the `tests/registry/conftest.py` fixture types, declared locally so
# the module does not import from a conftest.
type GraphHandler = Callable[[httpx.Request], httpx.Response]
type InstallGraphTransport = Callable[[GraphHandler], list[httpx.Request]]
type GraphSecrets = Callable[..., AbstractContextManager[None]]

GRAPH_SERVICE = "MICROSOFT_GRAPH_SERVICE_TOKEN"
GRAPH_USER = "MICROSOFT_GRAPH_USER_TOKEN"
SECURITY_SERVICE = "MICROSOFT_GRAPH_SECURITY_SERVICE_TOKEN"
SECURITY_USER = "MICROSOFT_GRAPH_SECURITY_USER_TOKEN"
OUTLOOK_SERVICE = "MICROSOFT_OUTLOOK_SERVICE_TOKEN"
OUTLOOK_USER = "MICROSOFT_OUTLOOK_USER_TOKEN"

ALL_TOKEN_NAMES = [
    GRAPH_SERVICE,
    GRAPH_USER,
    SECURITY_SERVICE,
    SECURITY_USER,
    OUTLOOK_SERVICE,
    OUTLOOK_USER,
]


def token_value(token_name: str) -> str:
    return f"{token_name.lower().replace('_', '-')}-placeholder"


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        content=json.dumps({"value": []}).encode(),
    )


SECURITY_PATH = "/security/alerts_v2"
OUTLOOK_PATH = "/users/user-1/messages"

type GraphSdkCall = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ProductCase:
    path: str
    call_method: GraphSdkCall
    call_paginated_method: GraphSdkCall
    call_continuation_method: GraphSdkCall
    secrets: list[RegistrySecretType]


PRODUCTS: dict[str, ProductCase] = {
    "security": ProductCase(
        path=SECURITY_PATH,
        call_method=security.call_method,
        call_paginated_method=security.call_paginated_method,
        call_continuation_method=security.call_continuation_method,
        secrets=security.MICROSOFT_GRAPH_SECURITY_SDK_SECRETS,
    ),
    "outlook": ProductCase(
        path=OUTLOOK_PATH,
        call_method=outlook.call_method,
        call_paginated_method=outlook.call_paginated_method,
        call_continuation_method=outlook.call_continuation_method,
        secrets=outlook.MICROSOFT_OUTLOOK_SDK_SECRETS,
    ),
}


# --- Declared token chains ------------------------------------------------


@pytest.mark.parametrize(
    ("product", "expected_service", "expected_user"),
    [
        (
            security.MICROSOFT_GRAPH_SECURITY,
            (SECURITY_SERVICE, GRAPH_SERVICE),
            (SECURITY_USER, GRAPH_USER),
        ),
        (
            outlook.MICROSOFT_OUTLOOK,
            (OUTLOOK_SERVICE, GRAPH_SERVICE),
            (OUTLOOK_USER, GRAPH_USER),
        ),
    ],
    ids=["security", "outlook"],
)
def test_product_token_chains_put_the_product_first(
    product: GraphProduct,
    expected_service: tuple[str, ...],
    expected_user: tuple[str, ...],
) -> None:
    assert product.token_names("application") == expected_service
    assert product.token_names("delegated") == expected_user
    assert product.token_names("auto") == expected_service + expected_user


@pytest.mark.parametrize(
    ("product_key", "provider_id", "service_token", "user_token"),
    [
        ("security", "microsoft_graph_security", SECURITY_SERVICE, SECURITY_USER),
        ("outlook", "microsoft_outlook", OUTLOOK_SERVICE, OUTLOOK_USER),
    ],
    ids=["security", "outlook"],
)
def test_product_sdk_declares_product_and_fallback_secrets(
    product_key: str, provider_id: str, service_token: str, user_token: str
) -> None:
    declared = PRODUCTS[product_key].secrets
    oauth_secrets = [
        secret for secret in declared if isinstance(secret, RegistryOAuthSecret)
    ]
    assert len(oauth_secrets) == len(declared)
    assert [(secret.provider_id, secret.token_name) for secret in oauth_secrets] == [
        (provider_id, service_token),
        (provider_id, user_token),
        ("microsoft_graph", GRAPH_SERVICE),
        ("microsoft_graph", GRAPH_USER),
    ]
    assert all(secret.optional for secret in oauth_secrets)


# --- Token precedence -----------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("product_key", "auth_mode", "available", "expected"),
    [
        # Graph Security: product token wins, generic Graph token is the fallback.
        (
            "security",
            "application",
            [SECURITY_SERVICE, GRAPH_SERVICE],
            SECURITY_SERVICE,
        ),
        ("security", "application", [GRAPH_SERVICE], GRAPH_SERVICE),
        ("security", "delegated", [SECURITY_USER, GRAPH_USER], SECURITY_USER),
        ("security", "delegated", [GRAPH_USER], GRAPH_USER),
        ("security", "auto", ALL_TOKEN_NAMES, SECURITY_SERVICE),
        ("security", "auto", [GRAPH_SERVICE, SECURITY_USER, GRAPH_USER], GRAPH_SERVICE),
        ("security", "auto", [SECURITY_USER, GRAPH_USER], SECURITY_USER),
        ("security", "auto", [GRAPH_USER], GRAPH_USER),
        # Outlook: identical ordering with Outlook in place of Security.
        ("outlook", "application", [OUTLOOK_SERVICE, GRAPH_SERVICE], OUTLOOK_SERVICE),
        ("outlook", "application", [GRAPH_SERVICE], GRAPH_SERVICE),
        ("outlook", "delegated", [OUTLOOK_USER, GRAPH_USER], OUTLOOK_USER),
        ("outlook", "delegated", [GRAPH_USER], GRAPH_USER),
        ("outlook", "auto", ALL_TOKEN_NAMES, OUTLOOK_SERVICE),
        ("outlook", "auto", [GRAPH_SERVICE, OUTLOOK_USER, GRAPH_USER], GRAPH_SERVICE),
        ("outlook", "auto", [OUTLOOK_USER, GRAPH_USER], OUTLOOK_USER),
        ("outlook", "auto", [GRAPH_USER], GRAPH_USER),
    ],
)
async def test_product_token_precedence(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
    product_key: str,
    auth_mode: MicrosoftGraphAuthMode,
    available: list[str],
    expected: str,
) -> None:
    product = PRODUCTS[product_key]
    requests = install_graph_transport(ok_handler)
    with graph_secrets(**{name: token_value(name) for name in available}):
        await product.call_method(path=product.path, method="GET", auth_mode=auth_mode)
    assert requests[0].headers.get_list("authorization") == [
        f"Bearer {token_value(expected)}"
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("auth_mode", ["application", "delegated", "auto"])
@pytest.mark.parametrize(
    ("product_key", "foreign_tokens"),
    [
        ("security", [OUTLOOK_SERVICE, OUTLOOK_USER]),
        ("outlook", [SECURITY_SERVICE, SECURITY_USER]),
    ],
    ids=["security-ignores-outlook", "outlook-ignores-security"],
)
async def test_products_never_borrow_each_others_tokens(
    graph_secrets: GraphSecrets,
    auth_mode: MicrosoftGraphAuthMode,
    product_key: str,
    foreign_tokens: list[str],
) -> None:
    product = PRODUCTS[product_key]
    present = {name: token_value(name) for name in foreign_tokens}
    with graph_secrets(**present):
        with pytest.raises(SecretNotFoundError) as exc_info:
            await product.call_method(
                path=product.path, method="GET", auth_mode=auth_mode
            )
    message = str(exc_info.value)
    for name, value in present.items():
        assert name not in message
        assert value not in message


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("product_key", "auth_mode", "expected_names"),
    [
        ("security", "application", [SECURITY_SERVICE, GRAPH_SERVICE]),
        ("security", "delegated", [SECURITY_USER, GRAPH_USER]),
        (
            "security",
            "auto",
            [SECURITY_SERVICE, GRAPH_SERVICE, SECURITY_USER, GRAPH_USER],
        ),
        ("outlook", "application", [OUTLOOK_SERVICE, GRAPH_SERVICE]),
        ("outlook", "delegated", [OUTLOOK_USER, GRAPH_USER]),
        ("outlook", "auto", [OUTLOOK_SERVICE, GRAPH_SERVICE, OUTLOOK_USER, GRAPH_USER]),
    ],
)
async def test_missing_token_errors_name_the_variables_only(
    graph_secrets: GraphSecrets,
    product_key: str,
    auth_mode: MicrosoftGraphAuthMode,
    expected_names: list[str],
) -> None:
    product = PRODUCTS[product_key]
    with graph_secrets():
        with pytest.raises(SecretNotFoundError) as exc_info:
            await product.call_method(
                path=product.path, method="GET", auth_mode=auth_mode
            )
    message = str(exc_info.value)
    for name in expected_names:
        assert name in message
    for name in ALL_TOKEN_NAMES:
        assert token_value(name) not in message


# --- Shared transport protections reach the product wrappers ---------------


@pytest.mark.anyio
@pytest.mark.parametrize("product_key", ["security", "outlook"])
async def test_product_wrappers_reject_unapproved_roots(
    graph_secrets: GraphSecrets, product_key: str
) -> None:
    product = PRODUCTS[product_key]
    with graph_secrets(**{GRAPH_SERVICE: token_value(GRAPH_SERVICE)}):
        with pytest.raises(ValueError, match="approved Microsoft Graph v1.0 API root"):
            await product.call_method(
                path=product.path,
                method="GET",
                base_url="https://attacker.example/v1.0",
            )


@pytest.mark.anyio
@pytest.mark.parametrize("product_key", ["security", "outlook"])
async def test_product_wrappers_reject_sdk_owned_headers(
    graph_secrets: GraphSecrets, product_key: str
) -> None:
    product = PRODUCTS[product_key]
    with graph_secrets(**{GRAPH_SERVICE: token_value(GRAPH_SERVICE)}):
        with pytest.raises(ValueError, match="cannot be supplied by the caller"):
            await product.call_method(
                path=product.path,
                method="GET",
                headers={"Authorization": "Bearer attacker-token"},
            )


@pytest.mark.anyio
@pytest.mark.parametrize("product_key", ["security", "outlook"])
async def test_product_wrappers_reject_traversal_paths(
    graph_secrets: GraphSecrets, product_key: str
) -> None:
    product = PRODUCTS[product_key]
    with graph_secrets(**{GRAPH_SERVICE: token_value(GRAPH_SERVICE)}):
        with pytest.raises(ValueError, match="Invalid Microsoft Graph path"):
            await product.call_method(path="../beta/users", method="GET")


@pytest.mark.anyio
@pytest.mark.parametrize("product_key", ["security", "outlook"])
async def test_product_wrappers_refuse_hostile_next_links(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
    product_key: str,
) -> None:
    product = PRODUCTS[product_key]
    hostile = "https://graph.microsoft.com/beta/users?$skiptoken=2"
    install_graph_transport(
        lambda request: httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(
                {"value": [{"id": "1"}], "@odata.nextLink": hostile}
            ).encode(),
        )
    )
    with graph_secrets(**{GRAPH_SERVICE: token_value(GRAPH_SERVICE)}):
        with pytest.raises(ValueError, match="must stay on"):
            await product.call_paginated_method(path=product.path)


@pytest.mark.anyio
@pytest.mark.parametrize("product_key", ["security", "outlook"])
async def test_product_continuation_actions_use_the_generic_graph_fallback(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
    product_key: str,
) -> None:
    product = PRODUCTS[product_key]
    continuation_url = (
        f"https://graph.microsoft.com/v1.0{product.path}?$skiptoken=opaque"
    )
    requests = install_graph_transport(ok_handler)

    with graph_secrets(**{GRAPH_SERVICE: token_value(GRAPH_SERVICE)}):
        await product.call_continuation_method(
            continuation_url=continuation_url,
            auth_mode="application",
        )

    assert requests[0].headers.get_list("authorization") == [
        f"Bearer {token_value(GRAPH_SERVICE)}"
    ]
