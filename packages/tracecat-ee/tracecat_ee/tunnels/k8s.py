"""Run one tunnel gateway per tunnel as Kubernetes objects created at runtime.

Each gateway is a ``tunnel-<id>`` Secret, Deployment, and Service in the
gateway namespace. The API creates and updates them when tunnels change, and a
reconcile loop removes orphans and recreates missing objects, so Terraform and
Helm never own per-tunnel state.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import ssl
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from tracecat import config
from tracecat.logger import logger

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY_VALUE = "tracecat"
COMPONENT_LABEL = "app.kubernetes.io/component"
COMPONENT_VALUE = "tunnel-gateway"
TUNNEL_ID_LABEL = "tracecat.com/tunnel-id"
CONFIG_HASH_ANNOTATION = "tracecat.com/config-hash"
FIELD_MANAGER = "tracecat"
GATEWAY_CONTAINER = "gateway"

_SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class TunnelGatewayBackend(StrEnum):
    NONE = "none"
    KUBERNETES = "kubernetes"


def gateway_backend() -> TunnelGatewayBackend:
    try:
        return TunnelGatewayBackend(config.TRACECAT__TUNNEL_GATEWAY_BACKEND)
    except ValueError as exc:
        raise ValueError(
            "TRACECAT__TUNNEL_GATEWAY_BACKEND must be 'kubernetes' or 'none'"
        ) from exc


def gateway_name(tunnel_id: uuid.UUID) -> str:
    return f"tunnel-{tunnel_id}"


class KubernetesError(Exception):
    """A Kubernetes API request failed."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Kubernetes API error {status_code}: {message}")
        self.status_code = status_code


@dataclass(frozen=True)
class GatewaySpec:
    """Everything a gateway pod needs to run one tunnel."""

    tunnel_id: uuid.UUID
    endpoint: str
    client_id: str
    client_secret: str = field(repr=False)
    gateway_key: str = field(repr=False)
    enabled: bool

    def secret_data(self, api_url: str) -> dict[str, str]:
        return {
            "TRACECAT_TUNNEL_ID": str(self.tunnel_id),
            "TRACECAT_TUNNEL_GATEWAY_KEY": self.gateway_key,
            "TRACECAT_TUNNEL_API_URL": api_url,
            "PANGOLIN_ENDPOINT": self.endpoint,
            "PANGOLIN_CLIENT_ID": self.client_id,
            "PANGOLIN_CLIENT_SECRET": self.client_secret,
        }


@dataclass(frozen=True)
class GatewaySettings:
    namespace: str
    image: str
    api_url: str
    port: int = 8080
    tun_resource: str = "squat.ai/tun"

    @classmethod
    def from_config(cls) -> GatewaySettings:
        return cls(
            namespace=config.TRACECAT__TUNNEL_GATEWAY_NAMESPACE,
            image=config.TRACECAT__TUNNEL_GATEWAY_IMAGE,
            api_url=config.TRACECAT__TUNNEL_GATEWAY_API_URL.rstrip("/"),
            port=config.TRACECAT__TUNNEL_GATEWAY_PORT,
            tun_resource=config.TRACECAT__TUNNEL_GATEWAY_TUN_RESOURCE,
        )


def _labels(tunnel_id: uuid.UUID) -> dict[str, str]:
    return {
        MANAGED_BY_LABEL: MANAGED_BY_VALUE,
        COMPONENT_LABEL: COMPONENT_VALUE,
        TUNNEL_ID_LABEL: str(tunnel_id),
    }


def build_secret(spec: GatewaySpec, settings: GatewaySettings) -> dict[str, Any]:
    data = spec.secret_data(settings.api_url)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": gateway_name(spec.tunnel_id),
            "namespace": settings.namespace,
            "labels": _labels(spec.tunnel_id),
        },
        "type": "Opaque",
        "data": {
            k: base64.b64encode(v.encode("utf-8")).decode("ascii")
            for k, v in data.items()
        },
    }


def _config_hash(spec: GatewaySpec, settings: GatewaySettings) -> str:
    data = json.dumps(spec.secret_data(settings.api_url), sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def build_deployment(spec: GatewaySpec, settings: GatewaySettings) -> dict[str, Any]:
    name = gateway_name(spec.tunnel_id)
    labels = _labels(spec.tunnel_id)
    limits: dict[str, str] = {"cpu": "500m", "memory": "256Mi"}
    requests: dict[str, str] = {"cpu": "50m", "memory": "64Mi"}
    if settings.tun_resource:
        limits[settings.tun_resource] = "1"
        requests[settings.tun_resource] = "1"
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": settings.namespace, "labels": labels},
        "spec": {
            "replicas": 1 if spec.enabled else 0,
            # One Pangolin machine client per tunnel: never run two at once.
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {TUNNEL_ID_LABEL: str(spec.tunnel_id)}},
            "template": {
                "metadata": {
                    "labels": labels,
                    "annotations": {
                        CONFIG_HASH_ANNOTATION: _config_hash(spec, settings)
                    },
                },
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "containers": [
                        {
                            "name": GATEWAY_CONTAINER,
                            "image": settings.image,
                            "imagePullPolicy": "IfNotPresent",
                            "envFrom": [{"secretRef": {"name": name}}],
                            "env": [
                                {
                                    "name": "TRACECAT_TUNNEL_GATEWAY_PORT",
                                    "value": str(settings.port),
                                }
                            ],
                            "ports": [
                                {
                                    "name": "proxy",
                                    "containerPort": settings.port,
                                    "protocol": "TCP",
                                }
                            ],
                            "readinessProbe": {
                                "tcpSocket": {"port": "proxy"},
                                "periodSeconds": 10,
                            },
                            "resources": {"requests": requests, "limits": limits},
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "privileged": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {
                                    "drop": ["ALL"],
                                    "add": ["NET_ADMIN"],
                                },
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                        }
                    ],
                    "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
                },
            },
        },
    }


def build_service(spec: GatewaySpec, settings: GatewaySettings) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": gateway_name(spec.tunnel_id),
            "namespace": settings.namespace,
            "labels": _labels(spec.tunnel_id),
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {TUNNEL_ID_LABEL: str(spec.tunnel_id)},
            "ports": [
                {
                    "name": "proxy",
                    "port": settings.port,
                    "targetPort": "proxy",
                    "protocol": "TCP",
                }
            ],
        },
    }


_KINDS: tuple[tuple[str, str], ...] = (
    ("/api/v1", "secrets"),
    ("/apis/apps/v1", "deployments"),
    ("/api/v1", "services"),
)


class KubernetesClient:
    """Minimal async client for the in-cluster Kubernetes API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        verify: ssl.SSLContext | bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if base_url is None:
            host = os.environ.get("KUBERNETES_SERVICE_HOST")
            port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
            if not host:
                raise KubernetesError(0, "Not running inside a Kubernetes cluster")
            if ":" in host:
                host = f"[{host}]"
            base_url = f"https://{host}:{port}"
        self._token = token
        if verify is None:
            verify = ssl.create_default_context(cafile=str(_SA_DIR / "ca.crt"))
        self._client = httpx.AsyncClient(
            base_url=base_url,
            verify=verify,
            transport=transport,
            timeout=httpx.Timeout(15.0),
            trust_env=False,
        )

    async def __aenter__(self) -> KubernetesClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        # Projected tokens rotate, so read the file on every request.
        token = self._token or (_SA_DIR / "token").read_text().strip()
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        headers = self._headers()
        if content_type:
            headers["Content-Type"] = content_type
        response = await self._client.request(
            method, path, params=params, content=content, headers=headers
        )
        if response.status_code >= 400:
            try:
                message = response.json().get("message", response.text)
            except ValueError:
                message = response.text
            raise KubernetesError(response.status_code, str(message))
        return response.json() if response.content else {}

    async def apply(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        """Create or update an object with server-side apply."""
        api = "/api/v1" if manifest["apiVersion"] == "v1" else "/apis/apps/v1"
        plural = {"Secret": "secrets", "Service": "services"}.get(
            manifest["kind"], "deployments"
        )
        meta = manifest["metadata"]
        return await self._request(
            "PATCH",
            f"{api}/namespaces/{meta['namespace']}/{plural}/{meta['name']}",
            params={"fieldManager": FIELD_MANAGER, "force": "true"},
            content=json.dumps(manifest).encode("utf-8"),
            content_type="application/apply-patch+yaml",
        )

    async def delete(self, api: str, plural: str, namespace: str, name: str) -> None:
        try:
            await self._request(
                "DELETE",
                f"{api}/namespaces/{namespace}/{plural}/{name}",
                params={"propagationPolicy": "Background"},
            )
        except KubernetesError as exc:
            if exc.status_code != 404:
                raise

    async def get(
        self, api: str, plural: str, namespace: str, name: str
    ) -> dict[str, Any] | None:
        try:
            return await self._request(
                "GET", f"{api}/namespaces/{namespace}/{plural}/{name}"
            )
        except KubernetesError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def list_names(
        self, api: str, plural: str, namespace: str, label_selector: str
    ) -> dict[str, str]:
        """Return ``{name: tunnel_id_label}`` for objects matching the selector."""
        data = await self._request(
            "GET",
            f"{api}/namespaces/{namespace}/{plural}",
            params={"labelSelector": label_selector},
        )
        return {
            item["metadata"]["name"]: item["metadata"]
            .get("labels", {})
            .get(TUNNEL_ID_LABEL, "")
            for item in data.get("items", [])
        }


_MANAGED_SELECTOR = (
    f"{MANAGED_BY_LABEL}={MANAGED_BY_VALUE},{COMPONENT_LABEL}={COMPONENT_VALUE}"
)


class KubernetesGateways:
    """Create, update, and delete the gateway objects for tunnels."""

    def __init__(
        self,
        client: KubernetesClient,
        settings: GatewaySettings | None = None,
    ) -> None:
        self.client = client
        self.settings = settings or GatewaySettings.from_config()

    async def apply(self, spec: GatewaySpec) -> None:
        # Secret first so a new pod never starts without its config.
        await self.client.apply(build_secret(spec, self.settings))
        await self.client.apply(build_service(spec, self.settings))
        await self.client.apply(build_deployment(spec, self.settings))

    async def delete(self, tunnel_id: uuid.UUID) -> None:
        name = gateway_name(tunnel_id)
        for api, plural in reversed(_KINDS):
            await self.client.delete(api, plural, self.settings.namespace, name)

    async def ready(self, tunnel_id: uuid.UUID) -> bool | None:
        """Whether the gateway has a ready pod; ``None`` if it does not exist."""
        deployment = await self.client.get(
            "/apis/apps/v1",
            "deployments",
            self.settings.namespace,
            gateway_name(tunnel_id),
        )
        if deployment is None:
            return None
        return int(deployment.get("status", {}).get("readyReplicas") or 0) > 0

    async def existing(self) -> dict[str, set[str]]:
        """Return the names of Tracecat-managed gateway objects per kind."""
        result: dict[str, set[str]] = {}
        for api, plural in _KINDS:
            names = await self.client.list_names(
                api, plural, self.settings.namespace, _MANAGED_SELECTOR
            )
            result[plural] = set(names)
        return result

    async def reconcile(self, specs: list[GatewaySpec]) -> tuple[int, int]:
        """Make the namespace match ``specs``. Returns ``(applied, deleted)``.

        Objects for tunnels not in ``specs`` are deleted; every spec is
        re-applied, which recreates missing objects and is a no-op otherwise.
        """
        desired = {gateway_name(spec.tunnel_id): spec for spec in specs}
        existing = await self.existing()
        deleted = 0
        for api, plural in reversed(_KINDS):
            for name in existing.get(plural, set()) - desired.keys():
                await self.client.delete(api, plural, self.settings.namespace, name)
                deleted += 1
        applied = 0
        for spec in desired.values():
            try:
                await self.apply(spec)
                applied += 1
            except KubernetesError as exc:
                logger.warning(
                    "Failed to apply tunnel gateway",
                    tunnel_id=str(spec.tunnel_id),
                    error=str(exc),
                )
        return applied, deleted


async def reconcile_forever(
    reconcile_once: Any,
    *,
    interval: float,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Call ``reconcile_once()`` every ``interval`` seconds until stopped."""
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        try:
            await reconcile_once()
        except Exception as exc:
            logger.warning("Tunnel gateway reconcile failed", error=str(exc))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
