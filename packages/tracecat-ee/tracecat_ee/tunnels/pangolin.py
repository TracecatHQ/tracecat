"""Minimal client for the Pangolin Integration API used by managed tunnels.

Only organization API keys are used, because Pangolin Cloud does not offer
root keys. Every managed tunnel's site, machine client, and private resources
live in the one configured Pangolin organization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from tracecat import config


class PangolinError(Exception):
    """Raised when the Pangolin Integration API rejects or fails a request."""


@dataclass(frozen=True)
class PangolinSettings:
    api_url: str
    endpoint: str
    org_id: str
    api_key: str

    @classmethod
    def from_config(cls) -> PangolinSettings | None:
        values = (
            config.TRACECAT__PANGOLIN_API_URL,
            config.TRACECAT__PANGOLIN_ENDPOINT,
            config.TRACECAT__PANGOLIN_ORG_ID,
            config.TRACECAT__PANGOLIN_API_KEY,
        )
        if not all(values):
            return None
        api_url, endpoint, org_id, api_key = (str(v) for v in values)
        return cls(api_url.rstrip("/"), endpoint.rstrip("/"), org_id, api_key)


@dataclass(frozen=True)
class PangolinCredentials:
    id: str
    secret: str


@dataclass(frozen=True)
class PangolinSite:
    site_id: int
    credentials: PangolinCredentials


@dataclass(frozen=True)
class PangolinClient:
    client_id: int
    credentials: PangolinCredentials


class PangolinAPI:
    def __init__(
        self,
        settings: PangolinSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.api_url,
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=30.0,
            transport=transport,
        )

    async def __aenter__(self) -> PangolinAPI:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise PangolinError(
                f"Pangolin request failed: {type(exc).__name__}"
            ) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or payload.get("success") is False:
            message = payload.get("message") or response.reason_phrase
            raise PangolinError(
                f"Pangolin {method} {path} failed ({response.status_code}): {message}"
            )
        return payload.get("data") or {}

    @property
    def _org(self) -> str:
        return f"/org/{self.settings.org_id}"

    async def create_site(self, name: str) -> PangolinSite:
        d = await self._request("GET", f"{self._org}/pick-site-defaults")
        site = await self._request(
            "PUT",
            f"{self._org}/site",
            {
                "name": name,
                "type": "newt",
                "newtId": d["newtId"],
                "secret": d["newtSecret"],
                "subnet": d["subnet"],
                "exitNodeId": d["exitNodeId"],
                "address": d.get("clientAddress"),
            },
        )
        return PangolinSite(
            site_id=int(site["siteId"]),
            credentials=PangolinCredentials(d["newtId"], d["newtSecret"]),
        )

    async def site_online(self, site_id: int) -> bool:
        site = await self._request("GET", f"/site/{site_id}")
        return bool(site.get("online"))

    async def create_machine_client(self, name: str) -> PangolinClient:
        d = await self._request("GET", f"{self._org}/pick-client-defaults")
        client = await self._request(
            "PUT",
            f"{self._org}/client",
            {
                "name": name,
                "olmId": d["olmId"],
                "secret": d["olmSecret"],
                "subnet": d["subnet"],
                "type": "olm",
            },
        )
        return PangolinClient(
            client_id=int(client["clientId"]),
            credentials=PangolinCredentials(d["olmId"], d["olmSecret"]),
        )

    async def create_host_resource(
        self,
        *,
        name: str,
        site_id: int,
        destination: str,
        tcp_ports: list[int],
        client_id: int,
    ) -> int:
        """Create a private host resource with only these TCP ports, no UDP or ICMP."""
        resource = await self._request(
            "PUT",
            f"{self._org}/private-resource",
            {
                "name": name,
                "mode": "host",
                "siteId": site_id,
                "destination": destination,
                "tcpPortRangeString": ",".join(str(p) for p in sorted(tcp_ports)),
                "udpPortRangeString": "",
                "disableIcmp": True,
                "userIds": [],
                "roleIds": [],
                "clientIds": [client_id],
            },
        )
        return int(resource["siteResourceId"])

    async def resource_client_ids(self, resource_id: int) -> set[int]:
        data = await self._request("GET", f"/private-resource/{resource_id}/clients")
        return {int(c["clientId"]) for c in data.get("clients", [])}

    async def delete_resource(self, resource_id: int) -> None:
        await self._request("DELETE", f"/private-resource/{resource_id}")

    async def delete_client(self, client_id: int) -> None:
        await self._request("DELETE", f"/client/{client_id}")

    async def delete_site(self, site_id: int) -> None:
        await self._request("DELETE", f"/site/{site_id}")
