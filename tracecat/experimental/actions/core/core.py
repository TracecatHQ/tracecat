"""UDF Definitions of core Tracecat actions."""

# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from typing import Annotated, Any, Literal, TypedDict

import httpx
from loguru import logger
from typing_extensions import Doc

from tracecat.db import CaseContext
from tracecat.experimental.actions._registry import registry
from tracecat.types.api import Suppression, Tag

RequestMethods = Literal["GET", "POST", "PUT", "DELETE"]


class HTTPResponse(TypedDict):
    status_code: int
    headers: dict[str, str]
    data: str | bytes | dict[str, Any]


@registry.register(
    namespace="core",
    version="0.1.0",
    description="Perform a HTTP request to a given URL.",
)
async def http_request(
    url: Annotated[str, Doc("The destination URL address")],
    headers: Annotated[dict[str, str], Doc("HTTP request headers")] = None,
    payload: Annotated[dict[str, Any], Doc("HTTP request payload")] = None,
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


@registry.register(
    namespace="core",
    version="0.1.0",
    description="Open a new case in the case management system.",
)
def open_case(
    # Action Inputs
    case_title: str,
    payload: dict[str, Any],
    malice: Literal["malicious", "benign"],
    status: Literal["open", "closed", "in_progress", "reported", "escalated"],
    priority: Literal["low", "medium", "high", "critical"],
    action: Literal[
        "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
    ],
    context: list[CaseContext] | None = None,
    suppression: list[Suppression] | None = None,
    tags: list[Tag] | None = None,
) -> Any:
    return NotImplemented


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
