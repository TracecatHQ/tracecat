"""Google API authentication and SDK helpers via Google Auth Python library."""

import base64
import io
from collections.abc import Callable
from typing import Annotated, Any, Protocol, TypedDict, cast

import orjson
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build
from googleapiclient.http import HttpRequest, MediaIoBaseUpload
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


class GoogleMediaUpload(TypedDict):
    """File payload for Google API upload methods."""

    content_base64: str
    """Base64-encoded file content; standard or URL-safe alphabet, padding optional."""

    mime_type: str
    """MIME type of the content."""


google_api_optional_secret = RegistrySecret(
    name="google_api",
    keys=["GOOGLE_API_CREDENTIALS"],
    optional_keys=["GOOGLE_API_SUBJECT"],
    optional=True,
)
"""Google API service account credentials.

- name: `google_api`
- keys:
    - `GOOGLE_API_CREDENTIALS` (JSON string)
- optional_keys:
    - `GOOGLE_API_SUBJECT` (user email)

Note: `GOOGLE_API_CREDENTIALS` should be a JSON string of the service account credentials.
`GOOGLE_API_SUBJECT` is the optional domain-wide delegation subject (a user email) applied
when minting a token from that JSON.
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


def _pop_embedded_subject(info: dict[str, Any]) -> str | None:
    """Remove the Tracecat-specific `subject` key from service account JSON.

    Mirrors `GoogleServiceAccountOAuthProvider._extract_subject`: `subject` is not
    part of Google's key schema, so it is always popped before the info dict
    reaches the SDK.
    """
    subject = info.pop("subject", None)
    if subject is None:
        return None
    return str(subject).strip() or None


def _get_service_account_credentials(
    scopes: list[str] | None = None,
    subject: str | None = None,
) -> service_account.Credentials:
    info = _load_service_account_info()
    embedded_subject = _pop_embedded_subject(info)
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=scopes or DEFAULT_SCOPES,
    )
    delegated_subject = (
        subject or secrets.get_or_default("GOOGLE_API_SUBJECT") or embedded_subject
    )
    if delegated_subject:
        credentials = credentials.with_subject(delegated_subject)
    return credentials


def _get_google_credentials(
    scopes: list[str] | None = None,
    subject: str | None = None,
    *,
    access_token: str | None = None,
) -> GoogleCredentials:
    """Resolve Google credentials from the first configured source.

    Precedence:
    1. `access_token` from a Tracecat OAuth integration, used as-is.
    2. `GOOGLE_API_CREDENTIALS` service account JSON, when `scopes` or `subject`
       are given.
    3. `GOOGLE_SERVICE_TOKEN` from the `google` service account integration.
       `scopes` are advisory here: a minted token already carries the scopes
       its integration was configured with. A `subject` is a per-call
       delegation override that a minted token cannot apply, so it still
       requires the JSON.
    4. `GOOGLE_API_CREDENTIALS` service account JSON.

    Every Google template passes `scopes`, which is what places the JSON key
    (step 2) ahead of `GOOGLE_SERVICE_TOKEN` (step 3) for them; a call without
    `scopes` or `subject` takes the token first (step 3, then 4).
    """
    if access_token:
        return OAuthCredentials(token=access_token)

    has_service_account_credentials = bool(
        secrets.get_or_default("GOOGLE_API_CREDENTIALS")
    )
    # A configured `GOOGLE_API_SUBJECT` is a delegation request just like an
    # explicit `subject`; without this the generic service token would win and
    # the call would silently run as the service account instead.
    has_overrides = (
        scopes is not None
        or subject is not None
        or bool(secrets.get_or_default("GOOGLE_API_SUBJECT"))
    )

    if has_overrides and has_service_account_credentials:
        return _get_service_account_credentials(scopes=scopes, subject=subject)

    if subject is not None:
        raise SecretNotFoundError(
            "`subject` requires `GOOGLE_API_CREDENTIALS` service account JSON "
            "because OAuth service tokens cannot apply a per-call delegation "
            "subject."
        )

    if token := secrets.get_or_default(google_oauth_secret.token_name):
        return OAuthCredentials(token=token)

    if has_service_account_credentials:
        return _get_service_account_credentials(scopes=scopes, subject=subject)

    raise SecretNotFoundError(
        "Google API calls require an `access_token` from a Google OAuth "
        "integration, `GOOGLE_API_CREDENTIALS` service account JSON, or "
        "`GOOGLE_SERVICE_TOKEN` from the `google` service account integration."
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


def _prune_none_params(params: GoogleAPIParams | None) -> GoogleAPIParams:
    """Drop top-level `None` values from a Google API method's parameters.

    `googleapiclient` discards `None` kwargs itself before building the request;
    pruning here keeps that behaviour independent of the library version and
    makes the contract explicit for templates. Values nested inside `body` are
    left untouched: a JSON `null` in a request body is meaningful to the API.
    """
    return {key: value for key, value in (params or {}).items() if value is not None}


_URL_SAFE_TO_STANDARD = str.maketrans("-_", "+/")


def _build_media_upload(media: GoogleMediaUpload) -> MediaIoBaseUpload:
    # Accept the standard and URL-safe alphabets (Gmail attachment `data` is
    # URL-safe and unpadded), with or without line wrapping, then reject anything
    # else instead of silently uploading corrupted content.
    encoded = "".join(media["content_base64"].split()).translate(_URL_SAFE_TO_STANDARD)
    encoded += "=" * (-len(encoded) % 4)
    return MediaIoBaseUpload(
        io.BytesIO(base64.b64decode(encoded, validate=True)),
        mimetype=media["mime_type"],
        resumable=False,
    )


class _DiscoveryCache:
    """Process-local cache for runtime-fetched discovery documents.

    Only used when `static_discovery` is disabled, i.e. when the discovery
    document is fetched over the network instead of read from the copy bundled
    with the client library. Those documents are large (the Security Command
    Center v2 document is ~420 KB) and would otherwise be re-downloaded on every
    single call. The default executor backend runs each action in a fresh
    subprocess, so this only saves a fetch on backends that reuse a process
    across actions.

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
    access_token: str | None = None,
) -> Resource:
    credentials = _get_google_credentials(
        scopes=scopes, subject=subject, access_token=access_token
    )
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


ServiceName = Annotated[
    str,
    Field(..., description="Google API service name, e.g. `drive` or `sheets`."),
]
Version = Annotated[
    str,
    Field(..., description="Google API version, e.g. `v3` or `v4`."),
]
ResourcePath = Annotated[
    str,
    Field(..., description="Resource path, e.g. `files` or `spreadsheets.values`."),
]
MethodName = Annotated[
    str,
    Field(..., description="Google API method name, e.g. `list` or `get`."),
]
MethodParams = Annotated[
    GoogleAPIParams | None,
    Field(..., description="Parameters for the Google API method."),
]
AccessToken = Annotated[
    str | None,
    Field(
        ...,
        description=(
            "OAuth access token from a Tracecat OAuth integration, e.g. "
            "`${{ SECRETS.google_drive_oauth.GOOGLE_DRIVE_USER_TOKEN || "
            "SECRETS.google_drive_oauth.GOOGLE_DRIVE_SERVICE_TOKEN }}`. When "
            "set it is used as-is and `scopes`/`subject` are ignored."
        ),
    ),
]
Scopes = Annotated[
    list[str] | None,
    Field(
        ...,
        description=(
            "Scopes used when minting a token from `GOOGLE_API_CREDENTIALS`. "
            'Defaults to ["https://www.googleapis.com/auth/cloud-platform"].'
        ),
    ),
]
Subject = Annotated[
    str | None,
    Field(
        ...,
        description=(
            "Optional domain-wide delegation subject (user email) applied "
            "when minting from `GOOGLE_API_CREDENTIALS`. Defaults to the "
            "`GOOGLE_API_SUBJECT` secret key, then the `subject` key inside "
            "the JSON."
        ),
    ),
]
StaticDiscovery = Annotated[
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
]


def _bound_method(
    *,
    service_name: str,
    version: str,
    resource: str,
    method_name: str,
    scopes: list[str] | None,
    subject: str | None,
    static_discovery: bool | None,
    access_token: str | None,
) -> GoogleAPIRequestBuilder:
    """Build the client and return the callable for one API method."""
    service = _build_google_service(
        service_name=service_name,
        version=version,
        scopes=scopes,
        subject=subject,
        static_discovery=static_discovery,
        access_token=access_token,
    )
    return getattr(_resolve_resource(service, resource), method_name)


@registry.register(
    default_title="Call API",
    description="Instantiate a Google API client and call a Google API method.",
    display_group="Google API",
    doc_url="https://googleapis.github.io/google-api-python-client/docs/dyn/",
    namespace="tools.google_api",
    secrets=[google_oauth_secret, google_api_optional_secret],
)
def call_api(
    service_name: ServiceName,
    version: Version,
    resource: ResourcePath,
    method_name: MethodName,
    params: MethodParams = None,
    access_token: AccessToken = None,
    scopes: Scopes = None,
    subject: Subject = None,
    media: Annotated[
        GoogleMediaUpload | None,
        Field(
            ...,
            description=(
                "File content for upload methods (e.g. Drive `files.create`). "
                "Sent as `media_body`."
            ),
        ),
    ] = None,
    static_discovery: StaticDiscovery = None,
) -> GoogleAPIResult:
    """Call a Google API method.

    Top-level `None` values in `params` are dropped; values nested inside `body`
    are sent as-is.
    """
    method_params = _prune_none_params(params)
    if media is not None:
        method_params["media_body"] = _build_media_upload(media)
    method = _bound_method(
        service_name=service_name,
        version=version,
        resource=resource,
        method_name=method_name,
        scopes=scopes,
        subject=subject,
        static_discovery=static_discovery,
        access_token=access_token,
    )
    return method(**method_params).execute()


@registry.register(
    default_title="Call paginated API",
    description="Instantiate a Google API client and call a paginated Google API method.",
    display_group="Google API",
    doc_url="https://googleapis.github.io/google-api-python-client/docs/dyn/",
    namespace="tools.google_api",
    secrets=[google_oauth_secret, google_api_optional_secret],
)
def call_paginated_api(
    service_name: ServiceName,
    version: Version,
    resource: ResourcePath,
    method_name: MethodName,
    params: MethodParams = None,
    access_token: AccessToken = None,
    scopes: Scopes = None,
    subject: Subject = None,
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
    static_discovery: StaticDiscovery = None,
) -> list[GoogleAPIResponse]:
    """Call a paginated Google API method and return every page fetched.

    Top-level `None` values in `params` are dropped; values nested inside `body`
    are sent as-is.
    """
    if not page_token_param:
        raise ValueError("Page token request parameter cannot be empty.")
    if not next_page_token_path:
        raise ValueError("Next page token response path cannot be empty.")

    request_params = _prune_none_params(params)
    pages: list[GoogleAPIResponse] = []
    method = _bound_method(
        service_name=service_name,
        version=version,
        resource=resource,
        method_name=method_name,
        scopes=scopes,
        subject=subject,
        static_discovery=static_discovery,
        access_token=access_token,
    )

    while True:
        response = method(**request_params).execute()
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
