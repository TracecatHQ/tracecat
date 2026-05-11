from pathlib import Path

import orjson

from tracecat.agent.sandbox.llm_proxy import (
    LLMRoute,
    LLMRoutingPlan,
    LLMSocketProxy,
)


def test_llm_socket_proxy_keeps_routing_plan() -> None:
    routing_plan = LLMRoutingPlan(
        managed_route=LLMRoute(
            base_url="http://litellm:4000",
            model_provider="custom-model-provider",
            mode="managed",
        ),
        direct_routes={},
    )
    proxy = LLMSocketProxy(
        socket_path=Path("/tmp/test-llm.sock"),
        routing_plan=routing_plan,
    )

    assert proxy.routing_plan.managed_route.base_url == "http://litellm:4000"


def test_route_rewrite_replaces_stale_content_length_case_insensitively() -> None:
    route = LLMRoute(
        base_url="https://api.example.com",
        model_provider="custom-model-provider",
        upstream_model_name="provider-model",
    )
    data = {"model": "synthetic-route", "messages": []}
    original_body = orjson.dumps(data)

    body, headers = route.forward_body_and_headers(
        body=original_body,
        data=data,
        headers={
            "content-length": str(len(original_body)),
            "x-request-id": "req_123",
        },
    )

    expected_body = orjson.dumps({"model": "provider-model", "messages": []})
    assert body == expected_body
    assert headers == {
        "x-request-id": "req_123",
        "Content-Length": str(len(expected_body)),
    }
