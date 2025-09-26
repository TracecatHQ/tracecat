from typing import Literal, NotRequired, TypedDict


class StdioMcpServer(TypedDict):
    command: str
    args: list[str]
    env: NotRequired[dict[str, str]]


class RemoteMcpServer(TypedDict):
    type: Literal["sse", "http"]
    url: str
    headers: NotRequired[dict[str, str]]


type McpServer = StdioMcpServer | RemoteMcpServer
