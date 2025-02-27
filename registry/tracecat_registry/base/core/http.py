"""Core HTTP actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from collections.abc import Callable
import tempfile
from json import JSONDecodeError
from typing import Annotated, Any, Literal, TypedDict

import httpx
from pydantic import UrlConstraints
from tenacity import (
    retry,
    retry_if_result,
    stop_after_attempt,
    stop_never,
    wait_exponential,
    wait_fixed,
)
import yaml
from tracecat.expressions.common import build_safe_lambda
from tracecat.logger import logger
from typing_extensions import Doc

from tracecat.types.exceptions import TracecatException
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


def _try_parse_response_data(response: httpx.Response) -> str | dict[str, Any]:
    try:
        return response.json()
    except JSONDecodeError:
        return response.text


def _format_response_data(response: httpx.Response) -> str:
    data = _try_parse_response_data(response)
    if isinstance(data, dict):
        return yaml.dump(data)
    return str(data)


STATUS_HANDLERS: dict[int, Callable[[httpx.HTTPStatusError], str]] = {
    400: lambda e: (
        f"400 Bad request for '{e.response.url}':"
        f"\n\n{_format_response_data(e.response)}"
        "\n\nThis error occurs when the server cannot understand the request. Please ensure that:"
        "\n- The request URL is properly formatted"
        "\n- Query parameters are valid"
        "\n- Headers are correctly specified"
        "\n- Request body matches the API requirements"
    ),
    404: lambda e: (
        f"404 Not found for '{e.response.url}':"
        f"\n\nPlease check that the URL is correct and the resource exists:\n"
        f"\nHost: {e.response.url.host}"
        f"\nPort: {e.response.url.port or '-'}"
        f"\nPath: {e.response.url.path}"
    ),
    422: lambda e: (
        f"422 Unprocessable entity for '{e.response.url}':"
        f"\n\n{_format_response_data(e.response)}"
        "\n\nThis error occurs when the server cannot process the request payload. Please ensure that:"
        "\n- The request body matches the expected format (e.g. valid JSON)"
        "\n- All required fields are included"
        "\n- Field values match the expected types and constraints"
    ),
    500: lambda e: (
        f"500 Internal server error for '{e.response.url}':"
        f"\n\n{_format_response_data(e.response)}"
        "\n\nThis error occurs when the server encounters an unexpected error while processing the request. Please:"
        "\n- Check if the server is running and accessible"
        "\n- Review server logs for error details"
        "\n- Contact the server administrator if the issue persists"
        "\n- Try the request again after a few minutes"
    ),
    # Add more status codes as needed...
}


def _http_status_error_to_message(e: httpx.HTTPStatusError) -> str:
    if handler := STATUS_HANDLERS.get(e.response.status_code):
        return handler(e)
    else:
        details = _format_response_data(e.response)
        return f"HTTP request failed with status {e.response.status_code}. Details:\n\n```\n{details}\n```"


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
        error_message = _http_status_error_to_message(e)
        logger.error(
            "HTTP request failed",
            status_code=e.response.status_code,
            error_message=error_message,
        )
        raise TracecatException(error_message)
        # raise e
    except httpx.ReadTimeout as e:
        logger.error(f"HTTP request timed out after {timeout} seconds.")
        raise e
    return httpx_to_response(response)


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
    # Common
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
    # Polling
    poll_retry_codes: Annotated[
        int | list[int] | None,
        Doc(
            "Status codes on which the action will retry. If not specified, `poll_condition` must be provided."
        ),
    ] = None,
    poll_interval: Annotated[
        float | None,
        Doc(
            "Interval in seconds between polling attempts. If not specified, defaults to polling with exponential wait."
        ),
    ] = None,
    poll_max_attempts: Annotated[
        int,
        Doc(
            "Maximum number of polling attempts. If set to 0, the action will poll indefinitely (until timeout)."
        ),
    ] = 10,
    poll_condition: Annotated[
        str | None,
        Doc(
            "User defined condition that determines whether to retry. The condition is a Python lambda function string. If not specified, `poll_retry_codes` must be provided."
        ),
    ] = None,
) -> HTTPResponse:
    """Perform a HTTP request to a given URL with optional polling."""

    basic_auth = httpx.BasicAuth(**auth) if auth else None
    cert = get_certificate()

    retry_codes = poll_retry_codes
    if isinstance(retry_codes, int):
        retry_codes = [retry_codes]

    predicate = build_safe_lambda(poll_condition) if poll_condition else None

    if not retry_codes and not predicate:
        raise ValueError("At least one of retry_codes or predicate must be specified")

    # The default predicate is to retry on the specified status codes
    def retry_status_code(response: httpx.Response) -> bool:
        if not retry_codes:
            return False
        return response.status_code in retry_codes

    # We also wanna support user defined predicate as a `python_lambda`
    def user_defined_predicate(response: httpx.Response) -> bool:
        if not predicate:
            return False
        data = _try_parse_response_data(response)
        args = PredicateArgs(
            headers=dict(response.headers.items()),
            data=data,
            status_code=response.status_code,
        )
        return predicate(args)

    @retry(
        stop=stop_after_attempt(poll_max_attempts)
        if poll_max_attempts > 0
        else stop_never,
        wait=wait_fixed(poll_interval) if poll_interval else wait_exponential(),
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
