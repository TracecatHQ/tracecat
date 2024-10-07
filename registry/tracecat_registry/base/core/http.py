"""Core HTTP actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any, Literal, TypedDict

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from pydantic import Field, UrlConstraints

from tracecat_registry import logger, registry

RequestMethods = Literal["GET", "POST", "PUT", "DELETE"]
JSONObjectOrArray = dict[str, Any] | list[Any]


class HTTPResponse(TypedDict):
    status_code: int
    headers: dict[str, str]
    data: str | dict[str, Any] | list[Any] | None


async def get_jwt_token(
    url: str,
    token_response_key: str,
    json: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, json=json, headers=headers)
            response.raise_for_status()
            token = response.json()[token_response_key]
        except KeyError:
            msg = f"Tried to get JWT token. `{token_response_key}` key not found in response JSON."
            return HTTPResponse(
                status_code=500, headers=dict(response.headers.items()), data=msg
            )
    return token


async def get_oauth2_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    grant_type: str,
    token_response_key: str,
    scope: str | None = None,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
):
    payload = payload or {}
    async with AsyncOAuth2Client(
        client_id=client_id, client_secret=client_secret, scope=scope
    ) as client:
        token = await client.fetch_token(
            token_url=token_url,
            headers=headers,
            grant_type=grant_type,
            **payload,
        )
        try:
            token = token[token_response_key]
        except KeyError:
            msg = f"Tried to get OAuth2 token. `{token_response_key}` key not found in response JSON."
            return HTTPResponse(status_code=500, headers={}, data=msg)
    return token


@registry.register(
    namespace="core",
    description="Perform a HTTP request to a given URL.",
    default_title="HTTP Request",
)
async def http_request(
    url: Annotated[
        str,
        Field(description="The destination of the HTTP request"),
        UrlConstraints(),
    ],
    headers: Annotated[
        dict[str, str],
        Field(description="HTTP request headers"),
    ] = None,
    payload: Annotated[
        JSONObjectOrArray,
        Field(description="HTTP request payload"),
    ] = None,
    params: Annotated[
        dict[str, Any],
        Field(description="URL query parameters"),
    ] = None,
    method: Annotated[
        RequestMethods,
        Field(description="HTTP request method"),
    ] = "GET",
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
    jwt_url: Annotated[
        str,
        Field(description="URL to obtain a JWT token"),
    ] = None,
    jwt_payload: Annotated[
        dict[str, str],
        Field(description="Payload to obtain a JWT token"),
    ] = None,
    oauth2_url: Annotated[
        str,
        Field(description="URL to obtain an OAuth2 token"),
    ] = None,
    oauth2_client_id: Annotated[
        str,
        Field(description="OAuth2 client ID"),
    ] = None,
    oauth2_client_secret: Annotated[
        str,
        Field(description="OAuth2 client secret"),
    ] = None,
    oauth2_scope: Annotated[
        str,
        Field(description="OAuth2 scope"),
    ] = None,
    oauth2_grant_type: Annotated[
        str,
        Field(
            description="OAuth2 grant type. Must be either 'client_credentials' or 'authorization_code'."
        ),
    ] = "client_credentials",
    oauth2_payload: Annotated[
        dict[str, str],
        Field(description="Additional payload to obtain an OAuth2 token"),
    ] = None,
    token_request_headers: Annotated[
        dict[str, str],
        Field(description="Headers to obtain a JWT / OAuth2 token"),
    ] = None,
    token_response_key: Annotated[
        str,
        Field(description="Key to access the token in the JWT / OAuth2 response JSON"),
    ] = "access_token",
    auth_header_key: Annotated[
        str,
        Field(
            description="Authorization header key to pass into HTTP headers. If None, defaults to 'Authorization'}"
        ),
    ] = "Authorization",
    auth_header_value: Annotated[
        str,
        Field(
            description="Authorization header value (must contain `{token}` in the string) to pass into HTTP headers. If None, defaults to 'Bearer {token}'}"
        ),
    ] = "Bearer {token}",
) -> HTTPResponse:
    access_token = None
    if jwt_url is not None:
        access_token = get_jwt_token(
            url=jwt_url,
            token_response_key=token_response_key,
            json=jwt_payload,
            headers=token_request_headers,
        )

    if oauth2_url is not None:
        access_token = get_oauth2_token(
            token_url=oauth2_url,
            client_id=oauth2_client_id,
            client_secret=oauth2_client_secret,
            grant_type=oauth2_grant_type,
            token_response_key=token_response_key,
            scope=oauth2_scope,
            headers=token_request_headers,
            payload=oauth2_payload,
        )

    if access_token is not None:
        headers[auth_header_key] = auth_header_value.format(token=access_token)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
        ) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=payload,
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
