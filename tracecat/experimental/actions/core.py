"""Core Tracecat actions."""

# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import logging
from typing import Annotated, Any, Literal, TypedDict

import httpx
from typing_extensions import Doc

from tracecat.experimental.actions._registry import registry

logger = logging.getLogger(__name__)


RequestMethods = Literal["GET", "POST", "PUT", "DELETE"]


class HTTPResponse(TypedDict):
    status_code: int
    headers: dict[str, str]
    data: str | bytes | dict[str, Any]


@registry.register(
    namespace="core",
    version="0.1.0",
    description="This is a test function",
)
async def http_request(
    url: Annotated[str, Doc("The destination URL address")],
    headers: Annotated[dict[str, str] | None, Doc("HTTP request headers")] = None,
    payload: Annotated[
        dict[str, str | bytes] | None, Doc("HTTP request payload")
    ] = None,
    method: Annotated[RequestMethods, Doc("HTTP reqest method")] = "GET",
) -> HTTPResponse:
    try:
        kwargs: dict[str, Any] = {}
        if headers is None:
            kwargs["headers"] = headers
        if payload is None:
            kwargs["json"] = payload
        async with httpx.AsyncClient() as client:
            response = await client.request(method=method, url=url, **kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("HTTP request failed with status {}.", e.response.status_code)
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


if __name__ == "__main__":
    import asyncio
    import json

    async def main():
        response = await http_request(
            url="http://localhost:8000/items/1",
            method="GET",
        )
        print(response)

    asyncio.run(main())
    print(json.dumps(registry.get_schemas(), indent=2))
    print(registry[http_request.__tracecat_udf_key].args_docs)
