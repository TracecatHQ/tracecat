from __future__ import annotations

from tracecat.agent.common.types import MCPHttpServerConfig, MCPStdioServerConfig
from tracecat.agent.factory import _is_http_server


def test_is_http_server_accepts_legacy_untyped_http_config() -> None:
    legacy_http_config: MCPHttpServerConfig = {
        "name": "legacy",
        "url": "http://localhost:8080",
    }

    assert _is_http_server(legacy_http_config) is True


def test_is_http_server_accepts_explicit_http_type() -> None:
    typed_http_config: MCPHttpServerConfig = {
        "type": "http",
        "name": "typed",
        "url": "http://localhost:8080",
    }

    assert _is_http_server(typed_http_config) is True


def test_is_http_server_rejects_stdio_type() -> None:
    stdio_config: MCPStdioServerConfig = {
        "type": "stdio",
        "name": "local-tools",
        "command": "npx",
    }

    assert _is_http_server(stdio_config) is False
