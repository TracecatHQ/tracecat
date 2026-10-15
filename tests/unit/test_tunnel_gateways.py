"""Tunnel gateway objects, routing, and the gateway-only sandbox network."""

from __future__ import annotations

import base64
import json
import uuid
from ipaddress import IPv4Address
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest
from tracecat_ee.tunnels import k8s
from tracecat_ee.tunnels.k8s import (
    GatewaySettings,
    GatewaySpec,
    KubernetesClient,
    KubernetesGateways,
    build_deployment,
    build_secret,
    build_service,
    gateway_name,
)
from tracecat_ee.tunnels.routing import (
    TunnelRouteError,
    effective_tunnel,
    tunnel_proxy_env,
)

from tracecat import config
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.action_runner import _tunnel_route
from tracecat.executor.schemas import ResolvedContext
from tracecat.outbound import (
    TUNNEL_PROXY_ENV,
    OutboundNetworkBackend,
    create_outbound_http_client,
)
from tracecat.sandbox.networking import (
    SandboxDnsRoute,
    gateway_only_network_policy,
    nstun_user_net_config_lines,
)
from tracecat.sandbox.types import SandboxNetworkPolicy

TUNNEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
SETTINGS = GatewaySettings(
    namespace="tracecat-tunnels",
    image="ghcr.io/tracecathq/tracecat-tunnel-gateway:test",
    api_url="http://api:8000",
)


def _spec(tunnel_id: uuid.UUID = TUNNEL_ID, *, enabled: bool = True) -> GatewaySpec:
    return GatewaySpec(
        tunnel_id=tunnel_id,
        endpoint="https://pangolin.example.com",
        client_id="client-id",
        client_secret="client-secret",
        gateway_key="gateway-key",
        enabled=enabled,
    )


class TestGatewayManifests:
    def test_deployment_is_locked_down(self) -> None:
        deployment = build_deployment(_spec(), SETTINGS)
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]

        assert deployment["metadata"]["name"] == f"tunnel-{TUNNEL_ID}"
        assert deployment["metadata"]["labels"][k8s.MANAGED_BY_LABEL] == "tracecat"
        assert deployment["spec"]["replicas"] == 1
        assert deployment["spec"]["strategy"] == {"type": "Recreate"}
        assert pod["automountServiceAccountToken"] is False
        assert container["securityContext"]["capabilities"] == {
            "drop": ["ALL"],
            "add": ["NET_ADMIN"],
        }
        assert container["securityContext"]["privileged"] is False
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        assert container["resources"]["limits"]["squat.ai/tun"] == "1"
        assert container["envFrom"] == [{"secretRef": {"name": f"tunnel-{TUNNEL_ID}"}}]

    def test_disabled_tunnel_scales_to_zero(self) -> None:
        deployment = build_deployment(_spec(enabled=False), SETTINGS)
        assert deployment["spec"]["replicas"] == 0

    def test_key_rotation_restarts_pod(self) -> None:
        before = build_deployment(_spec(), SETTINGS)
        rotated = GatewaySpec(**{**_spec().__dict__, "gateway_key": "new-key"})
        after = build_deployment(rotated, SETTINGS)
        annotation = k8s.CONFIG_HASH_ANNOTATION
        assert (
            before["spec"]["template"]["metadata"]["annotations"][annotation]
            != after["spec"]["template"]["metadata"]["annotations"][annotation]
        )

    def test_secret_holds_gateway_config(self) -> None:
        secret = build_secret(_spec(), SETTINGS)
        data = {
            key: base64.b64decode(value).decode()
            for key, value in secret["data"].items()
        }
        assert data == {
            "TRACECAT_TUNNEL_ID": str(TUNNEL_ID),
            "TRACECAT_TUNNEL_GATEWAY_KEY": "gateway-key",
            "TRACECAT_TUNNEL_API_URL": "http://api:8000",
            "PANGOLIN_ENDPOINT": "https://pangolin.example.com",
            "PANGOLIN_CLIENT_ID": "client-id",
            "PANGOLIN_CLIENT_SECRET": "client-secret",
        }

    def test_spec_repr_hides_secrets(self) -> None:
        assert "client-secret" not in repr(_spec())
        assert "gateway-key" not in repr(_spec())

    def test_service_selects_tunnel_pod(self) -> None:
        service = build_service(_spec(), SETTINGS)
        assert service["spec"]["selector"] == {k8s.TUNNEL_ID_LABEL: str(TUNNEL_ID)}
        assert service["spec"]["ports"][0]["port"] == 8080

    def test_backend_rejects_unknown_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "TRACECAT__TUNNEL_GATEWAY_BACKEND", "docker")
        with pytest.raises(ValueError, match="kubernetes"):
            k8s.gateway_backend()


@pytest.mark.anyio
async def test_reconcile_deletes_orphans_and_applies_desired() -> None:
    orphan = gateway_name(uuid.UUID("00000000-0000-4000-8000-0000000000ff"))
    requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.method == "GET":
            selector = request.url.params["labelSelector"]
            assert "app.kubernetes.io/managed-by=tracecat" in selector
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"metadata": {"name": orphan}},
                        {"metadata": {"name": gateway_name(TUNNEL_ID)}},
                    ]
                },
            )
        if request.method == "PATCH":
            assert request.url.params["fieldManager"] == "tracecat"
            assert request.headers["content-type"] == "application/apply-patch+yaml"
        return httpx.Response(200, json={})

    async with KubernetesClient(
        base_url="https://k8s.test",
        token="token",
        verify=False,
        transport=httpx.MockTransport(handler),
    ) as client:
        applied, deleted = await KubernetesGateways(client, SETTINGS).reconcile(
            [_spec()]
        )

    assert (applied, deleted) == (1, 3)
    deletes = [path for method, path, _ in requests if method == "DELETE"]
    assert all(path.endswith(f"/{orphan}") for path in deletes)
    assert gateway_name(TUNNEL_ID) not in "".join(deletes)
    applies = [
        body["kind"] for method, _, body in requests if method == "PATCH" and body
    ]
    assert applies == ["Secret", "Service", "Deployment"]


@pytest.mark.anyio
async def test_delete_ignores_missing_objects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    async with KubernetesClient(
        base_url="https://k8s.test",
        token="token",
        verify=False,
        transport=httpx.MockTransport(handler),
    ) as client:
        await KubernetesGateways(client, SETTINGS).delete(TUNNEL_ID)


class TestGatewayOnlyNetwork:
    def test_only_gateway_is_reachable(self) -> None:
        policy = gateway_only_network_policy("10.100.1.2", 8080)
        dns = SandboxDnsRoute(
            guest_address=IPv4Address("10.96.0.10"),
            host_address=IPv4Address("10.96.0.10"),
        )
        text = "\n".join(nstun_user_net_config_lines(policy, (dns,)))

        assert (
            'action: ALLOW\n    proto: TCP\n    dst_ip: "10.100.1.2/32"\n    dport: 8080'
        ) in text
        assert text.count("action: ALLOW") == 1
        assert "dport: 53" not in text
        assert 'action: REJECT\n    proto: ANY\n    dst_ip: "0.0.0.0/0"' in text
        assert 'action: REJECT\n    proto: ANY\n    dst_ip: "::/0"' in text

    def test_default_action_policy_is_unchanged(self) -> None:
        text = "\n".join(nstun_user_net_config_lines(SandboxNetworkPolicy()))
        assert 'action: ALLOW\n    proto: ANY\n    dst_ip: "0.0.0.0/0"' in text


def _input(task_tunnel: str | None, default: str | None, action: str) -> RunActionInput:
    return cast(
        RunActionInput,
        SimpleNamespace(
            task=SimpleNamespace(tunnel=task_tunnel, action=action),
            run_context=SimpleNamespace(tunnel=default),
        ),
    )


class TestRouting:
    def test_action_tunnel_overrides_default(self) -> None:
        assert effective_tunnel(_input("a", "b", "core.http_request")) == "a"
        assert effective_tunnel(_input(None, "b", "core.http_request")) == "b"
        assert effective_tunnel(_input(None, None, "core.http_request")) is None

    def test_default_skips_platform_actions(self) -> None:
        assert effective_tunnel(_input(None, "b", "core.workflow.execute")) is None
        assert effective_tunnel(_input("a", "b", "core.workflow.execute")) == "a"

    def test_proxy_env_has_no_bypass(self) -> None:
        env = tunnel_proxy_env("http://tracecat:t@10.0.0.1:8080")
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            assert env[key] == "http://tracecat:t@10.0.0.1:8080"
        assert env["NO_PROXY"] == env["no_proxy"] == ""
        assert env[TUNNEL_PROXY_ENV] == "http://tracecat:t@10.0.0.1:8080"

    def test_unresolved_route_fails_closed(self) -> None:
        context = cast(
            ResolvedContext,
            SimpleNamespace(
                tunnel_proxy_url=None, tunnel_gateway_ip=None, tunnel_gateway_port=None
            ),
        )
        with pytest.raises(TunnelRouteError):
            _tunnel_route(_input("a", None, "core.http_request"), context)
        assert _tunnel_route(_input(None, None, "core.http_request"), context) is None


class TestOutboundTunnelProxy:
    def test_direct_client_keeps_ssrf_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(TUNNEL_PROXY_ENV, raising=False)
        client = create_outbound_http_client()
        pool = cast(Any, client._transport)._pool
        assert isinstance(pool._network_backend, OutboundNetworkBackend)
        assert not isinstance(pool, httpcore.AsyncHTTPProxy)

    def test_tunneled_client_uses_gateway(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TUNNEL_PROXY_ENV, "http://tracecat:t@10.0.0.1:8080")
        client = create_outbound_http_client()
        pool = cast(Any, client._transport)._pool
        assert isinstance(pool, httpcore.AsyncHTTPProxy)
        assert not isinstance(pool._network_backend, OutboundNetworkBackend)

    def test_proxy_overrides_still_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TUNNEL_PROXY_ENV, "http://tracecat:t@10.0.0.1:8080")
        with pytest.raises(ValueError):
            create_outbound_http_client(proxy="http://evil:1")


@pytest.mark.anyio
async def test_pangolin_api_key_arn_is_read_once_and_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat_ee.tunnels import pangolin

    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_API_URL", "https://p.test/v1")
    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_ENDPOINT", "https://p.test")
    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_ORG_ID", "org-test")
    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_API_KEY", "env-key")
    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_API_KEY__ARN", "arn:test")
    monkeypatch.setattr(pangolin, "_api_key_from_arn", None)
    retrieve = AsyncMock(return_value="arn-key")
    monkeypatch.setattr(pangolin, "_retrieve_api_key", retrieve)

    first = await pangolin.resolve_pangolin_settings()
    second = await pangolin.resolve_pangolin_settings()

    assert first is not None and second is not None
    assert first.api_key == "arn-key"
    assert second.api_key == "arn-key"
    retrieve.assert_awaited_once_with("arn:test")


@pytest.mark.anyio
async def test_pangolin_api_key_without_arn_uses_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat_ee.tunnels import pangolin

    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_API_URL", "https://p.test/v1")
    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_ENDPOINT", "https://p.test")
    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_ORG_ID", "org-test")
    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_API_KEY", "env-key")
    monkeypatch.setattr(config, "TRACECAT__PANGOLIN_API_KEY__ARN", None)
    retrieve = AsyncMock()
    monkeypatch.setattr(pangolin, "_retrieve_api_key", retrieve)

    settings = await pangolin.resolve_pangolin_settings()

    assert settings is not None and settings.api_key == "env-key"
    retrieve.assert_not_awaited()
