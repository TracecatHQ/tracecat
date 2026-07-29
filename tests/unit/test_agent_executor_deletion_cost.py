from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import orjson
import pytest

from tracecat.agent.executor import deletion_cost

type AsyncRequestHandler = Callable[
    [httpx.Request],
    Coroutine[None, None, httpx.Response],
]


def _configure_enabled_publisher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    token_path = tmp_path / "token"
    ca_path = tmp_path / "ca.crt"
    token_path.write_text("initial-token")
    ca_path.write_text("test-ca")

    monkeypatch.setattr(deletion_cost, "SERVICE_ACCOUNT_TOKEN_PATH", token_path)
    monkeypatch.setattr(deletion_cost, "SERVICE_ACCOUNT_CA_PATH", ca_path)
    monkeypatch.setattr(
        deletion_cost.config,
        "TRACECAT__AGENT_EXECUTOR_POD_DELETION_COST_ENABLED",
        True,
    )
    monkeypatch.setattr(
        deletion_cost.config,
        "TRACECAT__K8S_POD_NAME",
        "agent-executor-abc",
    )
    monkeypatch.setattr(
        deletion_cost.config,
        "TRACECAT__K8S_POD_NAMESPACE",
        "tracecat",
    )
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "6443")
    return token_path, ca_path


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: AsyncRequestHandler,
) -> list[tuple[str, float]]:
    real_async_client = httpx.AsyncClient
    client_settings: list[tuple[str, float]] = []

    def client_factory(*, verify: str, timeout: float) -> httpx.AsyncClient:
        client_settings.append((verify, timeout))
        return real_async_client(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        )

    monkeypatch.setattr(deletion_cost.httpx, "AsyncClient", client_factory)
    return client_settings


@pytest.mark.parametrize(
    "disabled_condition",
    [
        "flag_off",
        "no_service_host",
        "missing_token",
        "missing_pod_name",
        "missing_pod_namespace",
    ],
)
@pytest.mark.anyio
async def test_disabled_publisher_is_noop_without_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    disabled_condition: str,
) -> None:
    token_path, _ = _configure_enabled_publisher(monkeypatch, tmp_path)

    match disabled_condition:
        case "flag_off":
            monkeypatch.setattr(
                deletion_cost.config,
                "TRACECAT__AGENT_EXECUTOR_POD_DELETION_COST_ENABLED",
                False,
            )
        case "no_service_host":
            monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
        case "missing_token":
            token_path.unlink()
        case "missing_pod_name":
            monkeypatch.setattr(
                deletion_cost.config,
                "TRACECAT__K8S_POD_NAME",
                None,
            )
        case "missing_pod_namespace":
            monkeypatch.setattr(
                deletion_cost.config,
                "TRACECAT__K8S_POD_NAMESPACE",
                None,
            )

    def fail_client(**_: object) -> httpx.AsyncClient:
        pytest.fail("disabled publisher attempted to create an HTTP client")

    monkeypatch.setattr(deletion_cost.httpx, "AsyncClient", fail_client)
    publisher = deletion_cost.PodDeletionCostPublisher()

    await publisher.increment()
    await publisher.decrement()


@pytest.mark.anyio
async def test_increment_and_decrement_publish_exact_patch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path, ca_path = _configure_enabled_publisher(monkeypatch, tmp_path)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request)

    client_settings = _install_mock_client(monkeypatch, handler)
    publisher = deletion_cost.PodDeletionCostPublisher()

    await publisher.increment()
    token_path.write_text("rotated-token")
    await publisher.decrement()

    assert len(requests) == 2
    assert all(
        str(request.url)
        == ("https://10.0.0.1:6443/api/v1/namespaces/tracecat/pods/agent-executor-abc")
        for request in requests
    )
    assert [request.method for request in requests] == ["PATCH", "PATCH"]
    assert [request.headers["content-type"] for request in requests] == [
        "application/merge-patch+json",
        "application/merge-patch+json",
    ]
    assert [request.headers["authorization"] for request in requests] == [
        "Bearer initial-token",
        "Bearer rotated-token",
    ]
    assert [orjson.loads(request.content) for request in requests] == [
        {
            "metadata": {
                "annotations": {
                    "controller.kubernetes.io/pod-deletion-cost": "1",
                }
            }
        },
        {
            "metadata": {
                "annotations": {
                    "controller.kubernetes.io/pod-deletion-cost": "0",
                }
            }
        },
    ]
    assert client_settings == [
        (str(ca_path), deletion_cost.PUBLISH_TIMEOUT_SECONDS),
        (str(ca_path), deletion_cost.PUBLISH_TIMEOUT_SECONDS),
    ]


@pytest.mark.anyio
async def test_rapid_increment_and_decrement_coalesce_to_latest_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_enabled_publisher(monkeypatch, tmp_path)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request)

    _install_mock_client(monkeypatch, handler)
    publisher = deletion_cost.PodDeletionCostPublisher()

    await asyncio.gather(
        publisher.increment(),
        publisher.decrement(),
    )

    assert len(requests) == 1
    assert orjson.loads(requests[0].content) == {
        "metadata": {
            "annotations": {
                "controller.kubernetes.io/pod-deletion-cost": "0",
            }
        }
    }


@pytest.mark.anyio
async def test_three_403_responses_disable_publisher_without_more_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_enabled_publisher(monkeypatch, tmp_path)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(403, request=request)

    _install_mock_client(monkeypatch, handler)
    test_logger = SimpleNamespace(debug=Mock(), warning=Mock())
    monkeypatch.setattr(deletion_cost, "logger", test_logger)
    publisher = deletion_cost.PodDeletionCostPublisher()

    await publisher.increment()
    await publisher.increment()
    await publisher.decrement()
    await publisher.increment()

    assert len(requests) == 3
    assert test_logger.warning.call_count == 3
    final_warning = test_logger.warning.call_args
    assert final_warning.args[0].startswith(
        "Disabling Kubernetes pod deletion cost publisher"
    )
    assert final_warning.kwargs["failures"] == 3
    assert final_warning.kwargs["status_code"] == 403


@pytest.mark.anyio
async def test_success_resets_consecutive_failure_counter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_enabled_publisher(monkeypatch, tmp_path)
    status_codes = iter([403, 200, 403, 403, 403])
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(next(status_codes), request=request)

    _install_mock_client(monkeypatch, handler)
    publisher = deletion_cost.PodDeletionCostPublisher()

    for _ in range(5):
        await publisher.increment()
    await publisher.decrement()

    assert len(requests) == 5
