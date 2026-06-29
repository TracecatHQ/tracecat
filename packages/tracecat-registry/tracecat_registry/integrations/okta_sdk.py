"""Generic interface and focused helpers for Okta Python SDK."""

from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict, cast
from urllib.parse import parse_qs, urlparse
from okta.models import UpdateUserRequest
from pydantic import BaseModel, Field

from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
    SecretNotFoundError,
    registry,
    secrets,
)

OKTA_SDK_DOC_URL = "https://github.com/okta/okta-sdk-python"
DEFAULT_RATE_LIMIT_MAX_RETRIES = 2

type OktaAuthMode = Literal["auto", "ssws", "oauth", "bearer", "private_key"]

BaseUrlParam = Annotated[
    str | None, Field(description="Okta org URL. Defaults to `OKTA_BASE_URL`.")
]
AuthModeParam = Annotated[OktaAuthMode, Field(description="Auth mode.")]
ScopesParam = Annotated[
    list[str] | None, Field(description="OAuth scopes for private-key auth.")
]


class OktaPaginatedResult(TypedDict):
    items: list[Any]
    pages: int
    next_after: str | None


okta_secret = RegistrySecret(
    name="okta",
    keys=None,
    optional_keys=[
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
    ],
    optional=True,
)
"""Okta SDK credentials.

- name: `okta`
- optional keys:
    - `OKTA_BASE_URL`
    - `OKTA_API_TOKEN`
    - `OKTA_ACCESS_TOKEN`
    - `OKTA_SERVICE_TOKEN`
    - `OKTA_CLIENT_ID`
    - `OKTA_PRIVATE_KEY`
    - `OKTA_SCOPES`
    - `OKTA_KID`
    - `OKTA_DPOP_ENABLED`
    - `OKTA_DPOP_KEY_ROTATION_INTERVAL`
"""

okta_oauth_secret = RegistryOAuthSecret(
    provider_id="okta",
    grant_type="client_credentials",
    optional=True,
)
"""Okta OAuth client credentials service token.

- name: `okta_oauth`
- provider_id: `okta`
- token_name: `OKTA_SERVICE_TOKEN`
"""

OKTA_SDK_SECRETS = [okta_secret, okta_oauth_secret]


def _validate_public_name(name: str, field: str) -> None:
    if not name:
        raise ValueError(f"{field} cannot be empty.")
    if name.startswith("_"):
        raise ValueError(f"{field} cannot start with `_`.")


def _drop_none(params: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value is not None}


def _split_scopes(scopes: list[str] | str | None) -> list[str] | None:
    if scopes is None:
        return None
    if isinstance(scopes, str):
        values = scopes.replace(",", " ").split()
    else:
        values = scopes
    return [scope.strip() for scope in values if scope.strip()]


def _get_bool_secret(name: str) -> bool:
    value = secrets.get_or_default(name)
    if not value:
        return False
    match value.strip().lower():
        case "true" | "1" | "yes" | "y" | "on":
            return True
        case "false" | "0" | "no" | "n" | "off":
            return False
    raise ValueError(f"`{name}` must be true or false.")


def _get_int_secret(name: str) -> int | None:
    value = secrets.get_or_default(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"`{name}` must be an integer.") from exc


def _resolve_base_url(base_url: str | None) -> str:
    resolved = base_url or secrets.get_or_default("OKTA_BASE_URL")
    if not resolved:
        raise SecretNotFoundError(
            "Okta SDK calls require `base_url` or `OKTA_BASE_URL`, e.g. "
            "`https://dev-123456.okta.com`."
        )
    return resolved.rstrip("/")


def _has_secret(name: str) -> bool:
    return bool(secrets.get_or_default(name))


def _resolve_auth_mode(
    auth_mode: OktaAuthMode,
) -> Literal["ssws", "bearer", "private_key"]:
    if auth_mode == "oauth":
        return "bearer"
    if auth_mode != "auto":
        return auth_mode
    if _has_secret(okta_oauth_secret.token_name) or _has_secret("OKTA_ACCESS_TOKEN"):
        return "bearer"
    if _has_secret("OKTA_CLIENT_ID") and _has_secret("OKTA_PRIVATE_KEY"):
        return "private_key"
    if _has_secret("OKTA_API_TOKEN"):
        return "ssws"
    raise SecretNotFoundError(
        "Okta SDK calls require one auth source: `OKTA_SERVICE_TOKEN` OAuth token, "
        "`OKTA_ACCESS_TOKEN`, `OKTA_CLIENT_ID` + `OKTA_PRIVATE_KEY`, or "
        "`OKTA_API_TOKEN`."
    )


def _build_okta_config(
    *,
    base_url: str | None = None,
    auth_mode: OktaAuthMode = "auto",
    scopes: list[str] | str | None = None,
    rate_limit_max_retries: int = DEFAULT_RATE_LIMIT_MAX_RETRIES,
) -> dict[str, Any]:
    resolved_auth_mode = _resolve_auth_mode(auth_mode)
    config: dict[str, Any] = {
        "orgUrl": _resolve_base_url(base_url),
        "raiseException": True,
        "rateLimit": {"maxRetries": rate_limit_max_retries},
        "cache": {"enabled": False},
        "logging": {"enabled": False},
    }

    match resolved_auth_mode:
        case "ssws":
            config["authorizationMode"] = "SSWS"
            config["token"] = secrets.get("OKTA_API_TOKEN")
        case "bearer":
            token = secrets.get_or_default(okta_oauth_secret.token_name) or secrets.get(
                "OKTA_ACCESS_TOKEN"
            )
            config["authorizationMode"] = "Bearer"
            config["token"] = token
        case "private_key":
            resolved_scopes = _split_scopes(scopes) or _split_scopes(
                secrets.get_or_default("OKTA_SCOPES")
            )
            if not resolved_scopes:
                raise SecretNotFoundError(
                    "Okta private-key auth requires `scopes` or `OKTA_SCOPES`."
                )
            config.update(
                {
                    "authorizationMode": "PrivateKey",
                    "clientId": secrets.get("OKTA_CLIENT_ID"),
                    "privateKey": secrets.get("OKTA_PRIVATE_KEY"),
                    "scopes": resolved_scopes,
                    "oauthTokenRenewalOffset": 5,
                }
            )
            if kid := secrets.get_or_default("OKTA_KID"):
                config["kid"] = kid
            if _get_bool_secret("OKTA_DPOP_ENABLED"):
                config["dpopEnabled"] = True
                if rotation_interval := _get_int_secret(
                    "OKTA_DPOP_KEY_ROTATION_INTERVAL"
                ):
                    config["dpopKeyRotationInterval"] = rotation_interval
    return config


def _get_okta_client_class() -> Any:
    from okta.client import Client as OktaClient

    return OktaClient


def _build_okta_client(
    *,
    base_url: str | None = None,
    auth_mode: OktaAuthMode = "auto",
    scopes: list[str] | str | None = None,
    rate_limit_max_retries: int = DEFAULT_RATE_LIMIT_MAX_RETRIES,
) -> Any:
    client_cls = _get_okta_client_class()
    config = _build_okta_config(
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
        rate_limit_max_retries=rate_limit_max_retries,
    )
    return client_cls(config)


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return cast(
            dict[str, Any],
            value.model_dump(by_alias=True, mode="json", exclude_none=True),
        )
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return cast(
            dict[str, Any],
            value.model_dump(by_alias=True, mode="json", exclude_none=True),
        )
    if hasattr(value, "__dict__"):
        return _jsonable(
            {key: item for key, item in vars(value).items() if not key.startswith("_")}
        )
    return value


def _raise_if_error(error: Any) -> None:
    if error is None:
        return
    if isinstance(error, BaseException):
        raise error
    raise RuntimeError(str(_jsonable(error)))


def _unwrap_sdk_result(result: Any) -> Any:
    if isinstance(result, tuple):
        if len(result) >= 3:
            _raise_if_error(result[2])
            return _jsonable(result[0])
        if len(result) == 2:
            _raise_if_error(result[1])
            return _jsonable(result[0])
    return _jsonable(result)


async def _call_okta_method(
    *,
    method_name: str,
    params: dict[str, Any] | None = None,
    base_url: str | None = None,
    auth_mode: OktaAuthMode = "auto",
    scopes: list[str] | str | None = None,
    rate_limit_max_retries: int = DEFAULT_RATE_LIMIT_MAX_RETRIES,
) -> Any:
    _validate_public_name(method_name, "Method name")
    client = _build_okta_client(
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
        rate_limit_max_retries=rate_limit_max_retries,
    )
    method = getattr(client, method_name)
    result = await method(**(params or {}))
    return _unwrap_sdk_result(result)


def _extract_next_after(response: Any) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None and isinstance(response, Mapping):
        headers = response.get("headers")
    if not headers:
        return None

    link_header = None
    if isinstance(headers, Mapping):
        link_header = headers.get("Link") or headers.get("link")
    if not isinstance(link_header, str):
        return None

    for part in link_header.split(","):
        if 'rel="next"' not in part and "rel=next" not in part:
            continue
        start = part.find("<")
        end = part.find(">", start + 1)
        if start == -1 or end == -1:
            continue
        query = parse_qs(urlparse(part[start + 1 : end]).query)
        after_values = query.get("after")
        if after_values:
            return after_values[0]
    return None


async def _call_okta_paginated_method(
    *,
    method_name: str,
    params: dict[str, Any] | None = None,
    base_url: str | None = None,
    auth_mode: OktaAuthMode = "auto",
    scopes: list[str] | str | None = None,
    limit: int | None = None,
    max_pages: int | None = None,
    rate_limit_max_retries: int = DEFAULT_RATE_LIMIT_MAX_RETRIES,
) -> OktaPaginatedResult:
    _validate_public_name(method_name, "Method name")
    client = _build_okta_client(
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
        rate_limit_max_retries=rate_limit_max_retries,
    )
    method = getattr(client, method_name)
    request_params = dict(params or {})
    if limit is not None and "limit" not in request_params:
        request_params["limit"] = limit

    items: list[Any] = []
    pages = 0
    next_after: str | None = cast(str | None, request_params.get("after"))

    while True:
        if next_after:
            request_params["after"] = next_after
        result = await method(**request_params)
        if isinstance(result, tuple) and len(result) == 2:
            _raise_if_error(result[1])
        if not isinstance(result, tuple) or len(result) < 3:
            raise ValueError(
                "Okta paginated SDK method must return `(data, response, error)`."
            )
        _raise_if_error(result[2])
        data = _jsonable(result[0])
        if isinstance(data, list):
            items.extend(data)
        else:
            items.append(data)
        pages += 1

        next_after = _extract_next_after(result[1])
        if not next_after or (max_pages is not None and pages >= max_pages):
            break

    return {"items": items, "pages": pages, "next_after": next_after}


@registry.register(
    default_title="Call method",
    description="Instantiate an Okta SDK client and call an async Okta SDK method.",
    display_group="Okta SDK",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def call_method(
    method_name: Annotated[
        str,
        Field(..., description="Okta SDK client method name, e.g. `list_users`."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the Okta SDK method."),
    ] = None,
    base_url: Annotated[
        str | None,
        Field(
            ...,
            description="Okta org URL. Defaults to `OKTA_BASE_URL`.",
        ),
    ] = None,
    auth_mode: Annotated[
        OktaAuthMode,
        Field(
            ...,
            description="Auth mode. Auto prefers OAuth/bearer, then private key, then SSWS.",
        ),
    ] = "auto",
    scopes: Annotated[
        list[str] | None,
        Field(..., description="OAuth scopes for private-key auth."),
    ] = None,
    rate_limit_max_retries: Annotated[
        int,
        Field(..., ge=0, le=10, description="Okta SDK rate-limit retry count."),
    ] = DEFAULT_RATE_LIMIT_MAX_RETRIES,
) -> Any:
    return await _call_okta_method(
        method_name=method_name,
        params=params,
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
        rate_limit_max_retries=rate_limit_max_retries,
    )


@registry.register(
    default_title="Call paginated method",
    description="Call an Okta SDK list method and follow Okta `Link` pagination.",
    display_group="Okta SDK",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def call_paginated_method(
    method_name: Annotated[
        str,
        Field(..., description="Okta SDK method name, e.g. `list_users`."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the Okta SDK method."),
    ] = None,
    base_url: Annotated[
        str | None,
        Field(..., description="Okta org URL. Defaults to `OKTA_BASE_URL`."),
    ] = None,
    auth_mode: Annotated[
        OktaAuthMode,
        Field(..., description="Auth mode."),
    ] = "auto",
    scopes: Annotated[
        list[str] | None,
        Field(..., description="OAuth scopes for private-key auth."),
    ] = None,
    limit: Annotated[
        int | None,
        Field(..., ge=1, le=1000, description="Per-page item limit."),
    ] = None,
    max_pages: Annotated[
        int | None,
        Field(..., ge=1, description="Maximum pages to fetch."),
    ] = None,
    rate_limit_max_retries: Annotated[
        int,
        Field(..., ge=0, le=10, description="Okta SDK rate-limit retry count."),
    ] = DEFAULT_RATE_LIMIT_MAX_RETRIES,
) -> OktaPaginatedResult:
    return await _call_okta_paginated_method(
        method_name=method_name,
        params=params,
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
        limit=limit,
        max_pages=max_pages,
        rate_limit_max_retries=rate_limit_max_retries,
    )


@registry.register(
    default_title="Update user",
    description="Partially update an Okta user profile and credentials.",
    display_group="Okta SDK Users",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def update_user(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    user: Annotated[dict[str, Any], Field(..., description="Okta user request body.")],
    strict: Annotated[
        bool | None,
        Field(..., description="Apply Okta strict validation."),
    ] = None,
    if_match: Annotated[
        str | None,
        Field(..., description="ETag value for conditional update."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="update_user",
        params=_drop_none(
            {
                "id": user_id,
                # from_dict preserves custom profile attributes (model_validate
                # drops unknown keys before the request is sent).
                "user": UpdateUserRequest.from_dict(user),
                "strict": strict,
                "if_match": if_match,
            }
        ),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Replace user",
    description="Replace an Okta user profile and credentials.",
    display_group="Okta SDK Users",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def replace_user(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    user: Annotated[dict[str, Any], Field(..., description="Okta user request body.")],
    strict: Annotated[
        bool | None,
        Field(..., description="Apply Okta strict validation."),
    ] = None,
    if_match: Annotated[
        str | None,
        Field(..., description="ETag value for conditional replace."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="replace_user",
        params=_drop_none(
            {
                "id": user_id,
                # from_dict preserves custom profile attributes (model_validate
                # drops unknown keys before the request is sent).
                "user": UpdateUserRequest.from_dict(user),
                "strict": strict,
                "if_match": if_match,
            }
        ),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Delete user",
    description="Delete or clear an Okta user, depending on current user status.",
    display_group="Okta SDK Users",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def delete_user(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    send_email: Annotated[
        bool | None,
        Field(..., description="Send Okta lifecycle email."),
    ] = None,
    prefer: Annotated[
        str | None,
        Field(..., description="Okta Prefer header value."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> None:
    return await _call_okta_method(
        method_name="delete_user",
        params=_drop_none({"id": user_id, "send_email": send_email, "prefer": prefer}),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Deactivate user",
    description="Deactivate an Okta user.",
    display_group="Okta SDK User Lifecycle",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def deactivate_user(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    send_email: Annotated[
        bool | None,
        Field(..., description="Send Okta lifecycle email."),
    ] = None,
    prefer: Annotated[
        str | None,
        Field(..., description="Okta Prefer header value."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any] | None:
    return await _call_okta_method(
        method_name="deactivate_user",
        params=_drop_none({"id": user_id, "send_email": send_email, "prefer": prefer}),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Reactivate user",
    description="Reactivate an Okta user.",
    display_group="Okta SDK User Lifecycle",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def reactivate_user(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    send_email: Annotated[
        bool | None,
        Field(..., description="Send Okta lifecycle email."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="reactivate_user",
        params=_drop_none({"id": user_id, "send_email": send_email}),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Unlock user",
    description="Unlock an Okta user account.",
    display_group="Okta SDK User Lifecycle",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def unlock_user(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any] | None:
    return await _call_okta_method(
        method_name="unlock_user",
        params={"id": user_id},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Reset user factors",
    description="Reset all enrolled factors for an Okta user.",
    display_group="Okta SDK User Lifecycle",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def reset_factors(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any] | None:
    return await _call_okta_method(
        method_name="reset_factors",
        params={"id": user_id},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Revoke user sessions",
    description="Revoke active Okta sessions for a user.",
    display_group="Okta SDK User Sessions",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def revoke_user_sessions(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    oauth_tokens: Annotated[
        bool | None,
        Field(..., description="Revoke OAuth tokens too."),
    ] = None,
    forget_devices: Annotated[
        bool | None,
        Field(..., description="Forget remembered devices."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> None:
    return await _call_okta_method(
        method_name="revoke_user_sessions",
        params=_drop_none(
            {
                "user_id": user_id,
                "oauth_tokens": oauth_tokens,
                "forget_devices": forget_devices,
            }
        ),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Change user password",
    description="Change an Okta user's password.",
    display_group="Okta SDK User Credentials",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def change_password(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    change_password_request: Annotated[
        dict[str, Any],
        Field(..., description="Okta change password request body."),
    ],
    strict: Annotated[
        bool | None,
        Field(..., description="Apply Okta strict validation."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="change_password",
        params=_drop_none(
            {
                "user_id": user_id,
                "change_password_request": change_password_request,
                "strict": strict,
            }
        ),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Forgot password",
    description="Start Okta forgot-password flow for a user.",
    display_group="Okta SDK User Credentials",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def forgot_password(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    send_email: Annotated[
        bool | None,
        Field(..., description="Send Okta lifecycle email."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="forgot_password",
        params=_drop_none({"user_id": user_id, "send_email": send_email}),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Create group",
    description="Create an Okta group.",
    display_group="Okta SDK Groups",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def add_group(
    group: Annotated[dict[str, Any], Field(..., description="Okta group body.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="add_group",
        params={"group": group},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Get group",
    description="Get an Okta group.",
    display_group="Okta SDK Groups",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def get_group(
    group_id: Annotated[str, Field(..., description="Okta group ID.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="get_group",
        params={"group_id": group_id},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Replace group",
    description="Replace an Okta group.",
    display_group="Okta SDK Groups",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def replace_group(
    group_id: Annotated[str, Field(..., description="Okta group ID.")],
    group: Annotated[dict[str, Any], Field(..., description="Okta group body.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="replace_group",
        params={"group_id": group_id, "group": group},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Delete group",
    description="Delete an Okta group.",
    display_group="Okta SDK Groups",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def delete_group(
    group_id: Annotated[str, Field(..., description="Okta group ID.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> None:
    return await _call_okta_method(
        method_name="delete_group",
        params={"group_id": group_id},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="List group applications",
    description="List applications assigned to an Okta group.",
    display_group="Okta SDK Groups",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def list_assigned_applications_for_group(
    group_id: Annotated[str, Field(..., description="Okta group ID.")],
    after: Annotated[
        str | None,
        Field(..., description="Okta pagination cursor."),
    ] = None,
    limit: Annotated[
        int | None,
        Field(..., ge=1, le=1000, description="Per-page item limit."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> list[dict[str, Any]]:
    return await _call_okta_method(
        method_name="list_assigned_applications_for_group",
        params=_drop_none({"group_id": group_id, "after": after, "limit": limit}),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="List applications",
    description="List Okta applications.",
    display_group="Okta SDK Applications",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def list_applications(
    q: Annotated[str | None, Field(..., description="Search query.")] = None,
    after: Annotated[
        str | None,
        Field(..., description="Okta pagination cursor."),
    ] = None,
    limit: Annotated[
        int | None,
        Field(..., ge=1, le=1000, description="Per-page item limit."),
    ] = None,
    filter: Annotated[
        str | None, Field(..., description="Okta filter expression.")
    ] = None,
    expand: Annotated[
        str | None, Field(..., description="Okta expand expression.")
    ] = None,
    include_non_deleted: Annotated[
        bool | None,
        Field(..., description="Include non-deleted applications."),
    ] = None,
    use_optimization: Annotated[
        bool | None,
        Field(..., description="Use Okta application-list optimization."),
    ] = None,
    always_include_vpn_settings: Annotated[
        bool | None,
        Field(..., description="Include VPN settings."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> list[dict[str, Any]]:
    return await _call_okta_method(
        method_name="list_applications",
        params=_drop_none(
            {
                "q": q,
                "after": after,
                "limit": limit,
                "filter": filter,
                "expand": expand,
                "include_non_deleted": include_non_deleted,
                "use_optimization": use_optimization,
                "always_include_vpn_settings": always_include_vpn_settings,
            }
        ),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Get application",
    description="Get an Okta application.",
    display_group="Okta SDK Applications",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def get_application(
    app_id: Annotated[str, Field(..., description="Okta application ID.")],
    expand: Annotated[
        str | None, Field(..., description="Okta expand expression.")
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="get_application",
        params=_drop_none({"app_id": app_id, "expand": expand}),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Activate application",
    description="Activate an Okta application.",
    display_group="Okta SDK Applications",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def activate_application(
    app_id: Annotated[str, Field(..., description="Okta application ID.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any] | None:
    return await _call_okta_method(
        method_name="activate_application",
        params={"app_id": app_id},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Deactivate application",
    description="Deactivate an Okta application.",
    display_group="Okta SDK Applications",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def deactivate_application(
    app_id: Annotated[str, Field(..., description="Okta application ID.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any] | None:
    return await _call_okta_method(
        method_name="deactivate_application",
        params={"app_id": app_id},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="List factors",
    description="List enrolled factors for an Okta user.",
    display_group="Okta SDK Factors",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def list_factors(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> list[dict[str, Any]]:
    return await _call_okta_method(
        method_name="list_factors",
        params={"user_id": user_id},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Get factor",
    description="Get an Okta user factor.",
    display_group="Okta SDK Factors",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def get_factor(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    factor_id: Annotated[str, Field(..., description="Okta factor ID.")],
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="get_factor",
        params={"user_id": user_id, "factor_id": factor_id},
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Unenroll factor",
    description="Unenroll an Okta user factor.",
    display_group="Okta SDK Factors",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def unenroll_factor(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    factor_id: Annotated[str, Field(..., description="Okta factor ID.")],
    remove_recovery_enrollment: Annotated[
        bool | None,
        Field(..., description="Remove recovery enrollment."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> None:
    return await _call_okta_method(
        method_name="unenroll_factor",
        params=_drop_none(
            {
                "user_id": user_id,
                "factor_id": factor_id,
                "remove_recovery_enrollment": remove_recovery_enrollment,
            }
        ),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="Verify factor",
    description="Verify an Okta user factor.",
    display_group="Okta SDK Factors",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def verify_factor(
    user_id: Annotated[str, Field(..., description="Okta user ID or login.")],
    factor_id: Annotated[str, Field(..., description="Okta factor ID.")],
    body: Annotated[
        dict[str, Any] | None,
        Field(..., description="Okta verify factor request body."),
    ] = None,
    template_id: Annotated[
        str | None, Field(..., description="Okta template ID.")
    ] = None,
    token_lifetime_seconds: Annotated[
        int | None,
        Field(..., ge=1, description="Token lifetime in seconds."),
    ] = None,
    x_forwarded_for: Annotated[
        str | None,
        Field(..., description="X-Forwarded-For header value."),
    ] = None,
    user_agent: Annotated[
        str | None,
        Field(..., description="User-Agent header value."),
    ] = None,
    accept_language: Annotated[
        str | None,
        Field(..., description="Accept-Language header value."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> dict[str, Any]:
    return await _call_okta_method(
        method_name="verify_factor",
        params=_drop_none(
            {
                "user_id": user_id,
                "factor_id": factor_id,
                "body": body,
                "template_id": template_id,
                "token_lifetime_seconds": token_lifetime_seconds,
                "x_forwarded_for": x_forwarded_for,
                "user_agent": user_agent,
                "accept_language": accept_language,
            }
        ),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )


@registry.register(
    default_title="List log events",
    description="List Okta System Log events.",
    display_group="Okta SDK System Log",
    doc_url=OKTA_SDK_DOC_URL,
    namespace="tools.okta_sdk",
    secrets=OKTA_SDK_SECRETS,
)
async def list_log_events(
    since: Annotated[
        str | None,
        Field(..., description="Lower time bound for log events."),
    ] = None,
    until: Annotated[
        str | None,
        Field(..., description="Upper time bound for log events."),
    ] = None,
    after: Annotated[
        str | None,
        Field(..., description="Okta pagination cursor."),
    ] = None,
    filter: Annotated[
        str | None, Field(..., description="Okta filter expression.")
    ] = None,
    q: Annotated[str | None, Field(..., description="Search query.")] = None,
    limit: Annotated[
        int | None,
        Field(..., ge=1, le=1000, description="Per-page item limit."),
    ] = None,
    sort_order: Annotated[
        str | None,
        Field(..., description="Sort order, e.g. `ASCENDING` or `DESCENDING`."),
    ] = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "auto",
    scopes: ScopesParam = None,
) -> list[dict[str, Any]]:
    return await _call_okta_method(
        method_name="list_log_events",
        params=_drop_none(
            {
                "since": since,
                "until": until,
                "after": after,
                "filter": filter,
                "q": q,
                "limit": limit,
                "sort_order": sort_order,
            }
        ),
        base_url=base_url,
        auth_mode=auth_mode,
        scopes=scopes,
    )
