"""Tests for platform catalog loading resilience."""

import orjson
import pytest

from tracecat.agent.catalog import loader


class _CatalogResource:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def joinpath(self, name: str) -> "_CatalogResource":
        assert name == "platform_catalog.json"
        return self

    def read_bytes(self) -> bytes:
        return self._payload


def _stub_catalog_resource(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    monkeypatch.setattr(
        loader.resources,
        "files",
        lambda _package: _CatalogResource(payload),
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b'{"models": {"model_provider": "openai", "model_name": "gpt-5"}}',
        b'{"models": ["not-a-model", {"model_provider": "openai"}]}',
    ],
)
def test_get_platform_catalog_models_ignores_malformed_shapes(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    _stub_catalog_resource(monkeypatch, payload)

    assert loader.get_platform_catalog_models() == []


def test_get_platform_catalog_models_filters_malformed_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_catalog_resource(
        monkeypatch,
        orjson.dumps(
            {
                "models": [
                    {
                        "model_provider": "openai",
                        "model_name": "gpt-5",
                        "metadata": {"family": "gpt"},
                    },
                    {"model_provider": "anthropic"},
                    "not-a-model",
                ]
            }
        ),
    )

    assert loader.get_platform_catalog_models() == [
        {
            "model_provider": "openai",
            "model_name": "gpt-5",
            "metadata": {"family": "gpt"},
        }
    ]
