"""Tests for platform catalog loading resilience."""

import orjson
import pytest

from tracecat.agent.catalog import loader
from tracecat.agent.catalog.service import PlatformCatalogEntry


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
        b'{"models": [{"model_provider": "", "model_name": "gpt-5"}]}',
        b'{"models": [{"model_provider": "openai", "model_name": "gpt-5", "metadata": "bad"}]}',
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
        PlatformCatalogEntry(
            model_provider="openai",
            model_name="gpt-5",
            metadata={"family": "gpt"},
        )
    ]


def test_platform_catalog_includes_gpt_5_6_models() -> None:
    entries = {
        entry.model_name: entry
        for entry in loader.get_platform_catalog_models()
        if entry.model_provider == "openai"
    }

    assert {"gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}.issubset(entries)
    assert entries["gpt-5.6"].metadata["input_cost_per_token"] == 5e-06
    assert entries["gpt-5.6-sol"].metadata["max_input_tokens"] == 1050000
    assert entries["gpt-5.6-terra"].metadata["output_cost_per_token"] == 1.5e-05
    assert entries["gpt-5.6-luna"].metadata["input_cost_per_token"] == 1e-06


def test_platform_catalog_includes_gpt_6_astra_and_claude_fable_5_1() -> None:
    entries = {
        (entry.model_provider, entry.model_name): entry
        for entry in loader.get_platform_catalog_models()
    }

    astra = entries[("openai", "gpt-6-astra")]
    assert astra.metadata["input_cost_per_token"] == 1e-05
    assert astra.metadata["output_cost_per_token"] == 5e-05
    assert astra.metadata["max_input_tokens"] == 922000

    fable = entries[("anthropic", "claude-fable-5-1")]
    assert fable.metadata["input_cost_per_token"] == 1e-05
    assert fable.metadata["cache_read_input_token_cost"] == 2.5e-07
    assert fable.metadata["max_input_tokens"] == 1000000
