from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from tracecat.agent.models import AgentTLSConfig
from tracecat.logger import logger


@dataclass(slots=True)
class TLSHttpClients:
    """Container for TLS-enabled HTTP clients used by the agent runtime."""

    provider: httpx.AsyncClient | None
    mcp: httpx.AsyncClient | None


class TemporaryClientCertificate:
    """Manage temporary files for client certificate and key materials."""

    def __init__(
        self,
        client_cert_str: str | None = None,
        client_key_str: str | None = None,
        client_key_password: str | None = None,
    ):
        self.client_cert_str = client_cert_str
        self.client_key_str = client_key_str
        self.client_key_password = client_key_password
        self._temp_files: list[tempfile._TemporaryFileWrapper] = []

    def __enter__(self) -> str | tuple[str, str] | tuple[str, str, str] | None:
        cert_path: str | None = None
        key_path: str | None = None

        if self.client_cert_str:
            cert_file = tempfile.NamedTemporaryFile(
                mode="w", delete=True, encoding="utf-8"
            )
            self._temp_files.append(cert_file)
            cert_file.write(self.client_cert_str)
            cert_file.flush()
            cert_path = cert_file.name

        if self.client_key_str:
            key_file = tempfile.NamedTemporaryFile(
                mode="w", delete=True, encoding="utf-8"
            )
            self._temp_files.append(key_file)
            key_file.write(self.client_key_str)
            key_file.flush()
            key_path = key_file.name

        if cert_path and key_path:
            if self.client_key_password:
                return (cert_path, key_path, self.client_key_password)
            return cert_path, key_path
        if cert_path:
            # PEM file containing both cert and key
            return cert_path

        return None

    def __exit__(self, exc_type, exc_val, exc_tb):
        for temp_file in self._temp_files:
            try:
                temp_file.close()
            except Exception:
                logger.error(
                    "Error closing temporary TLS certificate file",
                    temp_file=temp_file.name,
                    exc_info=True,
                )


@asynccontextmanager
async def agent_tls_clients(
    tls_config: AgentTLSConfig | None,
    *,
    include_provider: bool,
    include_mcp: bool,
    mcp_headers: dict[str, str] | None = None,
) -> AsyncIterator[TLSHttpClients]:
    """Yield TLS-enabled HTTP clients for provider and MCP connections."""

    if tls_config is None or not tls_config.is_configured():
        yield TLSHttpClients(provider=None, mcp=None)
        return

    with TemporaryClientCertificate(
        client_cert_str=tls_config.client_cert,
        client_key_str=tls_config.client_key,
        client_key_password=tls_config.client_key_password,
    ) as cert_for_httpx:
        provider_client: httpx.AsyncClient | None = None
        mcp_client: httpx.AsyncClient | None = None
        clients_to_close: list[httpx.AsyncClient] = []
        try:
            if include_provider:
                provider_client = httpx.AsyncClient(cert=cert_for_httpx)
                clients_to_close.append(provider_client)

            if include_mcp:
                mcp_client = httpx.AsyncClient(cert=cert_for_httpx, headers=mcp_headers)
                clients_to_close.append(mcp_client)

            yield TLSHttpClients(provider=provider_client, mcp=mcp_client)
        finally:
            for client in clients_to_close:
                try:
                    await client.aclose()
                except Exception:
                    logger.error(
                        "Failed to close TLS-enabled HTTP client", exc_info=True
                    )
