"""Core HTTP actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import tempfile
from collections.abc import Callable
from typing import Annotated, Any, Literal, TypedDict

import httpx
from pydantic import (
    BaseModel,
    TypeAdapter,
    UrlConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from tenacity import (
    retry,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)
from tracecat.expressions.functions import _build_safe_lambda
from tracecat.logger import logger
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets

RequestMethods = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
JSONObjectOrArray = dict[str, Any] | list[Any]

ssl_secret = RegistrySecret(
    name="ssl",
    optional_keys=["SSL_CLIENT_CERT", "SSL_CLIENT_KEY", "SSL_CLIENT_PASSWORD"],
    optional=True,
)
"""HTTP SSL certificate secrets.

By default, the HTTP action uses the CA bundle from Certifi.
This optional secret allows you to specify a custom client-side certificate to use for SSL verification.

- name: `ssl`
- optional keys:
    - `SSL_CLIENT_CERT`
    - `SSL_CLIENT_KEY`
    - `SSL_CLIENT_PASSWORD`

Note: `SSL_CLIENT_CERT` and `SSL_CLIENT_KEY` are text fields that contain the certificate and key respectively.
`SSL_CLIENT_PASSWORD` is optional.

"""


Url = Annotated[
    str,
    Doc("The destination of the HTTP request"),
    UrlConstraints(),
]
Method = Annotated[
    RequestMethods,
    Doc("HTTP request method"),
]
Headers = Annotated[
    dict[str, str] | None,
    Doc("HTTP request headers"),
]
Params = Annotated[
    dict[str, Any] | None,
    Doc("URL query parameters"),
]
Payload = Annotated[
    JSONObjectOrArray | None,
    Doc("JSON serializable data in request body (POST, PUT, and PATCH)"),
]
FormData = Annotated[
    dict[str, Any] | None,
    Doc("Form encoded data in request body (POST, PUT, and PATCH)"),
]
Auth = Annotated[
    dict[str, str] | None,
    Doc("Basic auth credentials with `username` and `password` keys"),
]
Timeout = Annotated[
    float,
    Doc("Timeout in seconds"),
]
FollowRedirects = Annotated[
    bool,
    Doc("Follow HTTP redirects"),
]
MaxRedirects = Annotated[
    int,
    Doc("Maximum number of redirects"),
]
VerifySSL = Annotated[
    bool,
    Doc("Verify SSL certificates. Defaults to True, disable at own risk."),
]


class HTTPResponse(TypedDict):
    status_code: int
    headers: dict[str, str]
    data: str | dict[str, Any] | list[Any] | None


def get_certificate() -> tuple[str, str | None, str | None] | None:
    cert = None
    if secrets.get("SSL_CLIENT_CERT"):
        # Create a temp file for the certificate
        cert_file_path = None
        with tempfile.NamedTemporaryFile(delete=False) as cert_file:
            cert_file.write(secrets.get("SSL_CLIENT_CERT").encode())
            cert_file.flush()
            cert_file_path = cert_file.name

        # Create a temp file for the key (if exists)
        key_file_path = None
        if secrets.get("SSL_CLIENT_KEY"):
            with tempfile.NamedTemporaryFile(delete=False) as key_file:
                key_file.write(secrets.get("SSL_CLIENT_KEY").encode())
                key_file.flush()
                key_file_path = key_file.name

        cert = [
            cert_file_path,
            key_file_path,
            secrets.get("SSL_CLIENT_PASSWORD"),
        ]
        # Drop None values
        cert = tuple(c for c in cert if c is not None)
    return cert


def httpx_to_response(response: httpx.Response) -> HTTPResponse:
    # Handle 204 No Content
    if response.status_code == 204:
        return HTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers.items()),
            data=None,  # No content
        )

    # TODO: Better parsing logic
    content_type = response.headers.get("Content-Type")
    if content_type and content_type.startswith("application/json"):
        return HTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers.items()),
            data=response.json(),
        )
    else:
        return HTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers.items()),
            data=response.text,
        )


@registry.register(
    namespace="core",
    description="Perform a HTTP request to a given URL.",
    default_title="HTTP Request",
    secrets=[ssl_secret],
)
async def http_request(
    url: Url,
    method: Method,
    headers: Headers = None,
    params: Params = None,
    payload: Payload = None,
    form_data: FormData = None,
    auth: Auth = None,
    timeout: Timeout = 10.0,
    follow_redirects: FollowRedirects = False,
    max_redirects: MaxRedirects = 20,
    verify_ssl: VerifySSL = True,
) -> HTTPResponse:
    """Perform a HTTP request to a given URL."""

    basic_auth = httpx.BasicAuth(**auth) if auth else None
    cert = get_certificate()

    try:
        async with httpx.AsyncClient(
            cert=cert,
            auth=basic_auth,
            timeout=httpx.Timeout(timeout),
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            verify=verify_ssl,
        ) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=payload,
                data=form_data,
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP request failed with status {e.response.status_code}.")
        logger.error(e.response.text)
        raise e
    except httpx.ReadTimeout as e:
        logger.error(f"HTTP request timed out after {timeout} seconds.")
        raise e
    return httpx_to_response(response)


class PollingOptions(BaseModel):
    retry_codes: list[int] | None = None
    """List of codes on which we retry."""
    interval: float = 1.0
    """Gap between polling attempts."""
    max_attempts: int = 10
    """Maximum number of polling attempts."""
    predicate: Callable[..., bool] | None = None
    """User defined predicate as a `python_lambda`."""

    @field_validator("retry_codes", mode="before")
    def validate_retry_codes(cls, v: int | list[int] | None) -> list[int] | None:
        if v is None:
            return None
        if isinstance(v, int):
            return [v]
        return v

    @field_validator("predicate", mode="before")
    def validate_predicate(cls, v: str | None) -> Callable[..., bool] | None:
        if v:
            return _build_safe_lambda(v)
        return None

    @model_validator(mode="after")
    def validate_at_least_one_predicate(self):
        if not self.retry_codes and not self.predicate:
            raise ValueError(
                "At least one of retry_codes or predicate must be specified"
            )
        return self


PollingOptionsAdapter = TypeAdapter(PollingOptions)

PollOptions = Annotated[
    dict[str, Any] | None,
    Doc(
        "Polling options:"
        "\nretry_codes: List of codes on which we retry."
        "\ninterval: Gap between polling attempts."
        "\nmax_attempts: Maximum number of polling attempts."
        "\npredicate: User defined predicate as a `python_lambda`."
    ),
]


class PredicateArgs(TypedDict):
    headers: dict[str, Any]
    data: Any
    status_code: int


@registry.register(
    namespace="core",
    description="Perform a HTTP request to a given URL with polling.",
    default_title="HTTP Polling",
    secrets=[ssl_secret],
)
async def http_poll(
    *,
    url: Url,
    method: Method,
    poll_options: PollOptions,
    headers: Headers = None,
    params: Params = None,
    payload: Payload = None,
    form_data: FormData = None,
    auth: Auth = None,
    timeout: Timeout = 10.0,
    follow_redirects: FollowRedirects = False,
    max_redirects: MaxRedirects = 20,
    verify_ssl: VerifySSL = True,
) -> HTTPResponse:
    """Perform a HTTP request to a given URL with optional polling."""

    basic_auth = httpx.BasicAuth(**auth) if auth else None
    cert = get_certificate()

    try:
        options = PollingOptions.model_validate(poll_options)
    except ValidationError as e:
        logger.error(f"Invalid polling options: {e}")
        raise e

    # The default predicate is to retry on the specified status codes
    def retry_status_code(response: httpx.Response) -> bool:
        if not options.retry_codes:
            return False
        status_code = response.status_code
        return status_code in options.retry_codes

    # We also wanna support user defined predicate as a `python_lambda`
    def user_defined_predicate(response: httpx.Response) -> bool:
        if not options.predicate:
            return False
        try:
            data = response.json()
        except Exception:
            data = response.text
        args = PredicateArgs(
            headers=dict(response.headers.items()),
            data=data,
            status_code=response.status_code,
        )
        return options.predicate(args)

    @retry(
        stop=stop_after_attempt(options.max_attempts),
        wait=wait_exponential(min=options.interval),
        retry=(
            retry_if_result(retry_status_code) | retry_if_result(user_defined_predicate)
        ),
    )
    async def call() -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                cert=cert,
                auth=basic_auth,
                timeout=httpx.Timeout(timeout),
                follow_redirects=follow_redirects,
                max_redirects=max_redirects,
                verify=verify_ssl,
            ) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=payload,
                    data=form_data,
                )
            return response
        # Handled by Temporal
        except httpx.ReadTimeout as e:
            logger.error(f"HTTP request timed out after {timeout} seconds.")
            raise e

    result = await call()
    return httpx_to_response(result)
