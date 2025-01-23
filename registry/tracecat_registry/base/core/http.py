"""Core HTTP actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import tempfile
from typing import Annotated, Any, Literal, TypedDict

import httpx
from pydantic import Field, UrlConstraints

from tracecat_registry import RegistrySecret, logger, registry, secrets

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


class HTTPResponse(TypedDict):
    status_code: int
    headers: dict[str, str]
    data: str | dict[str, Any] | list[Any] | None


@registry.register(
    namespace="core",
    description="Perform a HTTP request to a given URL.",
    default_title="HTTP Request",
    secrets=[ssl_secret],
)
async def http_request(
    url: Annotated[
        str,
        Field(description="The destination of the HTTP request"),
        UrlConstraints(),
    ],
    method: Annotated[
        RequestMethods,
        Field(description="HTTP request method"),
    ],
    headers: Annotated[
        dict[str, str],
        Field(description="HTTP request headers"),
    ] = None,
    params: Annotated[
        dict[str, Any],
        Field(description="URL query parameters"),
    ] = None,
    payload: Annotated[
        JSONObjectOrArray,
        Field(
            description="JSON serializable data in request body (POST, PUT, and PATCH)"
        ),
    ] = None,
    form_data: Annotated[
        dict[str, Any],
        Field(description="Form encoded data in request body (POST, PUT, and PATCH)"),
    ] = None,
    auth: Annotated[
        dict[str, str],
        Field(description="Basic auth credentials with `username` and `password` keys"),
    ] = None,
    timeout: Annotated[
        float,
        Field(description="Timeout in seconds"),
    ] = 10.0,
    follow_redirects: Annotated[
        bool,
        Field(description="Follow HTTP redirects"),
    ] = False,
    max_redirects: Annotated[
        int,
        Field(description="Maximum number of redirects"),
    ] = 20,
    verify_ssl: Annotated[
        bool,
        Field(
            description="Verify SSL certificates. Defaults to True, disable at own risk."
        ),
    ] = True,
) -> HTTPResponse:
    """Perform a HTTP request to a given URL."""

    basic_auth = None
    if auth:
        basic_auth = httpx.BasicAuth(**auth)

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
