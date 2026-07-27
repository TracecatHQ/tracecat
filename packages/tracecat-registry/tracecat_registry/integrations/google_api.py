"""Google API authentication and SDK helpers via Google Auth Python library."""

from collections.abc import Callable
from typing import Annotated, Any, Protocol, cast

import orjson
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build
from googleapiclient.http import HttpRequest
from pydantic import Field

from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
    SecretNotFoundError,
    registry,
    secrets,
)

DEFAULT_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

type GoogleAPIResponse = dict[str, Any]
type GoogleAPIResult = Any
type GoogleAPIParams = dict[str, Any]
type GoogleCredentials = OAuthCredentials | service_account.Credentials
type GoogleAPIRequestBuilder = Callable[..., HttpRequest]


class GoogleAPIResource(Protocol):
    def __getattr__(self, name: str) -> GoogleAPIRequestBuilder: ...


google_api_optional_secret = RegistrySecret(
    name="google_api",
    keys=["GOOGLE_API_CREDENTIALS"],
    optional=True,
)
"""Google API service account credentials.

- name: `google_api`
- keys:
    - `GOOGLE_API_CREDENTIALS` (JSON string)

Note: `GOOGLE_API_CREDENTIALS` should be a JSON string of the service account credentials.
"""

google_oauth_secret = RegistryOAuthSecret(
    provider_id="google",
    grant_type="client_credentials",
    optional=True,
)
"""Google service account OAuth credentials.

- name: `google_oauth`
- provider_id: `google`
- token_name: `GOOGLE_SERVICE_TOKEN`
"""


def _load_service_account_info() -> dict[str, Any]:
    creds_json = secrets.get("GOOGLE_API_CREDENTIALS")
    try:
        creds = orjson.loads(creds_json)
    except orjson.JSONDecodeError as e:
        raise ValueError("`GOOGLE_API_CREDENTIALS` is not a valid JSON string.") from e
    if not isinstance(creds, dict):
        raise ValueError("`GOOGLE_API_CREDENTIALS` must be a JSON object.")
    return creds


def _get_service_account_credentials(
    scopes: list[str] | None = None,
    subject: str | None = None,
) -> service_account.Credentials:
    credentials = service_account.Credentials.from_service_account_info(
        _load_service_account_info(),
        scopes=scopes or DEFAULT_SCOPES,
    )
    if subject:
        credentials = credentials.with_subject(subject)
    return credentials


def _get_google_credentials(
    scopes: list[str] | None = None,
    subject: str | None = None,
) -> GoogleCredentials:
    has_service_account_credentials = bool(
        secrets.get_or_default("GOOGLE_API_CREDENTIALS")
    )
    if (scopes is not None or subject is not None) and has_service_account_credentials:
        return _get_service_account_credentials(scopes=scopes, subject=subject)

    if (
        scopes is not None or subject is not None
    ) and not has_service_account_credentials:
        raise SecretNotFoundError(
            "`scopes` and `subject` require `GOOGLE_API_CREDENTIALS` service "
            "account JSON because OAuth service tokens cannot apply per-call "
            "service account overrides."
        )

    if token := secrets.get_or_default(google_oauth_secret.token_name):
        return OAuthCredentials(token=token)

    if has_service_account_credentials:
        return _get_service_account_credentials(scopes=scopes, subject=subject)

    raise SecretNotFoundError(
        "Google API calls require either `GOOGLE_SERVICE_TOKEN` from the `google` "
        "OAuth integration or `GOOGLE_API_CREDENTIALS` service account JSON."
    )


def _resolve_resource(service: Resource, resource: str) -> GoogleAPIResource:
    target = service
    for part in resource.split("."):
        if not part:
            raise ValueError("Resource path cannot contain empty segments.")
        target = getattr(target, part)()
    return cast(GoogleAPIResource, target)


def _get_value_by_path(data: dict[str, Any], path: str) -> Any | None:
    current: Any = data
    for part in path.split("."):
        if not part:
            raise ValueError(
                "Next page token response path cannot contain empty segments."
            )
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class _DiscoveryCache:
    """Process-local cache for runtime-fetched discovery documents.

    Only used when `static_discovery` is disabled, i.e. when the discovery
    document is fetched over the network instead of read from the copy bundled
    with the client library. Those documents are large (the Security Command
    Center v2 document is ~420 KB) and would otherwise be re-downloaded on every
    single call. Executor workers are reused across actions, so caching per
    process means each worker fetches a given document at most once.

    Implements the `get`/`set` interface `googleapiclient.discovery.build`
    expects from a cache object.
    """

    def __init__(self) -> None:
        self._documents: dict[str, str] = {}

    def get(self, url: str) -> str | None:
        return self._documents.get(url)

    def set(self, url: str, content: str) -> None:
        self._documents[url] = content


_discovery_cache = _DiscoveryCache()


def _build_google_service(
    service_name: str,
    version: str,
    scopes: list[str] | None = None,
    subject: str | None = None,
    static_discovery: bool | None = None,
) -> Resource:
    credentials = _get_google_credentials(scopes=scopes, subject=subject)
    # `googleapiclient` consults the cache *before* it decides whether to use a
    # bundled document, so a cached runtime-fetched document would otherwise
    # shadow the bundled one for every later call in this worker. Only enable
    # the cache on the path that actually fetches over the network.
    fetches_discovery_doc = static_discovery is False
    return cast(
        Resource,
        build(
            service_name,
            version,
            credentials=credentials,
            cache_discovery=fetches_discovery_doc,
            cache=_discovery_cache if fetches_discovery_doc else None,
            static_discovery=static_discovery,
        ),
    )


@registry.register(
    default_title="Call API",
    description="Instantiate a Google API client and call a Google API method.",
    display_group="Google API",
    doc_url="https://googleapis.github.io/google-api-python-client/docs/dyn/",
    namespace="tools.google_api",
    secrets=[google_oauth_secret, google_api_optional_secret],
)
def call_api(
    service_name: Annotated[
        str,
        Field(..., description="Google API service name, e.g. `drive` or `sheets`."),
    ],
    version: Annotated[
        str,
        Field(..., description="Google API version, e.g. `v3` or `v4`."),
    ],
    resource: Annotated[
        str,
        Field(
            ...,
            description="Resource path, e.g. `files` or `spreadsheets.values`.",
        ),
    ],
    method_name: Annotated[
        str,
        Field(..., description="Google API method name, e.g. `list` or `get`."),
    ],
    params: Annotated[
        GoogleAPIParams | None,
        Field(..., description="Parameters for the Google API method."),
    ] = None,
    scopes: Annotated[
        list[str] | None,
        Field(
            ...,
            description='Service account scopes. Defaults to ["https://www.googleapis.com/auth/cloud-platform"].',
        ),
    ] = None,
    subject: Annotated[
        str | None,
        Field(
            ..., description="Optional service account domain-wide delegation subject."
        ),
    ] = None,
    static_discovery: Annotated[
        bool | None,
        Field(
            ...,
            description=(
                "Whether to use the discovery document bundled with the client "
                "library. Defaults to the library behaviour (bundled). Set to "
                "false to fetch the discovery document at runtime, which is "
                "required for API versions that are not bundled (e.g. "
                "`securitycenter` `v2`)."
            ),
        ),
    ] = None,
) -> GoogleAPIResult:
    params = params or {}
    service = _build_google_service(
        service_name=service_name,
        version=version,
        scopes=scopes,
        subject=subject,
        static_discovery=static_discovery,
    )
    request = getattr(_resolve_resource(service, resource), method_name)(**params)
    return request.execute()


@registry.register(
    default_title="Call paginated API",
    description="Instantiate a Google API client and call a paginated Google API method.",
    display_group="Google API",
    doc_url="https://googleapis.github.io/google-api-python-client/docs/dyn/",
    namespace="tools.google_api",
    secrets=[google_oauth_secret, google_api_optional_secret],
)
def call_paginated_api(
    service_name: Annotated[
        str,
        Field(..., description="Google API service name, e.g. `drive` or `sheets`."),
    ],
    version: Annotated[
        str,
        Field(..., description="Google API version, e.g. `v3` or `v4`."),
    ],
    resource: Annotated[
        str,
        Field(
            ...,
            description="Resource path, e.g. `files` or `spreadsheets.values`.",
        ),
    ],
    method_name: Annotated[
        str,
        Field(..., description="Google API method name, e.g. `list` or `get`."),
    ],
    params: Annotated[
        GoogleAPIParams | None,
        Field(..., description="Parameters for the Google API method."),
    ] = None,
    scopes: Annotated[
        list[str] | None,
        Field(
            ...,
            description='Service account scopes. Defaults to ["https://www.googleapis.com/auth/cloud-platform"].',
        ),
    ] = None,
    subject: Annotated[
        str | None,
        Field(
            ..., description="Optional service account domain-wide delegation subject."
        ),
    ] = None,
    page_token_param: Annotated[
        str,
        Field(
            ...,
            description='Request parameter name for the next page token. Defaults to "pageToken".',
        ),
    ] = "pageToken",
    next_page_token_path: Annotated[
        str,
        Field(
            ...,
            description='Dot-separated response path for the next page token. Defaults to "nextPageToken".',
        ),
    ] = "nextPageToken",
    max_pages: Annotated[
        int | None,
        Field(
            ...,
            ge=1,
            description="Maximum number of pages to fetch. If null, fetches every page.",
        ),
    ] = None,
    static_discovery: Annotated[
        bool | None,
        Field(
            ...,
            description=(
                "Whether to use the discovery document bundled with the client "
                "library. Defaults to the library behaviour (bundled). Set to "
                "false to fetch the discovery document at runtime, which is "
                "required for API versions that are not bundled (e.g. "
                "`securitycenter` `v2`)."
            ),
        ),
    ] = None,
) -> list[GoogleAPIResponse]:
    if not page_token_param:
        raise ValueError("Page token request parameter cannot be empty.")
    if not next_page_token_path:
        raise ValueError("Next page token response path cannot be empty.")

    request_params = dict(params or {})
    pages: list[GoogleAPIResponse] = []
    service = _build_google_service(
        service_name=service_name,
        version=version,
        scopes=scopes,
        subject=subject,
        static_discovery=static_discovery,
    )
    target_resource = _resolve_resource(service, resource)

    while True:
        response = getattr(target_resource, method_name)(**request_params).execute()
        if not isinstance(response, dict):
            raise ValueError(
                "Expected Google API response to be a dict, "
                f"got {type(response).__name__}."
            )
        page = cast(GoogleAPIResponse, response)
        pages.append(page)
        if max_pages is not None and len(pages) >= max_pages:
            break
        next_page_token = _get_value_by_path(page, next_page_token_path)
        if not next_page_token:
            break
        request_params[page_token_param] = next_page_token
    return pages
