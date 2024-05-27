"""Core HTTP actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any, Literal, TypedDict

import httpx
from loguru import logger
from pydantic import AnyHttpUrl, Field

from tracecat.experimental.registry import registry

RequestMethods = Literal["GET", "POST", "PUT", "DELETE"]


class HTTPResponse(TypedDict):
    status_code: int
    headers: dict[str, str]
    data: str | dict[str, Any] | list[Any]


@registry.register(
    namespace="core",
    version="0.1.0",
    description="Perform a HTTP request to a given URL.",
)
async def http_request(
    url: Annotated[
        AnyHttpUrl,
        Field(description="The destination of the HTTP request", max_length=100),
    ],
    headers: Annotated[
        dict[str, str],
        Field(description="HTTP request headers"),
    ] = None,
    payload: Annotated[
        dict[str, Any],
        Field(description="HTTP request payload"),
    ] = None,
    params: Annotated[
        dict[str, Any],
        Field(description="URL query parameters"),
    ] = None,
    method: Annotated[
        RequestMethods,
        Field(description="HTTP reqest method"),
    ] = "GET",
) -> HTTPResponse:
    try:
        async with httpx.AsyncClient() as client:
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
        raise e

    # TODO: Better parsing logic
    content_type = response.headers.get("Content-Type")
    if content_type.startswith("application/json"):
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
