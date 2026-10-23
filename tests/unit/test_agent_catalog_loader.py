"""Tests for platform catalog loading resilience."""

import orjson
import pytest

from tracecat.agent.catalog import bundled, loader
from tracecat.agent.catalog.service import PlatformCatalogEntry
from tracecat.agent.catalog.types import ModelKey


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
        bundled.resources,
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


def test_bundled_platform_catalog_loads() -> None:
    entries = loader.get_platform_catalog_models()

    assert {entry.model_provider for entry in entries} >= {"openai", "anthropic"}


def test_parse_retired_models_keeps_valid_entries_only() -> None:
    retired = bundled.parse_retired_models(
        {
            "retired": [
                {
                    "model_provider": "openai",
                    "model_name": "gpt-5.1-codex-max",
                    "lifecycle": {
                        "retires_on": "2026-07-23",
                        "replacement": "gpt-5.6-sol",
                    },
                },
                {"model_provider": "openai", "model_name": "no-lifecycle"},
                "not-a-model",
            ]
        }
    )

    assert list(retired) == [ModelKey("openai", "gpt-5.1-codex-max")]
    assert retired[ModelKey("openai", "gpt-5.1-codex-max")].replacement == "gpt-5.6-sol"
    assert bundled.parse_retired_models({"retired": {"bad": "shape"}}) == {}
