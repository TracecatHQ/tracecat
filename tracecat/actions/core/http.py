"""Core HTTP actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any, Literal, TypedDict

import httpx
from loguru import logger
from pydantic import Field, UrlConstraints

from tracecat.registry import registry

RequestMethods = Literal["GET", "POST", "PUT", "DELETE"]
JSONObjectOrArray = dict[str, Any] | list[Any]


class HTTPResponse(TypedDict):
    status_code: int
    headers: dict[str, str]
    data: str | dict[str, Any] | list[Any] | None


@registry.register(
    namespace="core",
    version="0.1.0",
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
) -> HTTPResponse:
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
