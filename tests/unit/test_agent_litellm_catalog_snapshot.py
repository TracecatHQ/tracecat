"""Regression coverage for the vendored LiteLLM catalog snapshot."""

import json
from pathlib import Path

import yaml

LITELLM_SNAPSHOT_DIR = Path("tracecat/agent/data/litellm/v1.82.1")
LITELLM_PROXY_CONFIG = Path("tracecat/agent/litellm_config.yaml")


def test_vendored_litellm_snapshot_files_exist() -> None:
    """The built-in catalog should stay pinned to a repo-vendored LiteLLM snapshot."""
    assert (LITELLM_SNAPSHOT_DIR / "model_prices_and_context_window.json").exists()
    assert (LITELLM_SNAPSHOT_DIR / "provider_endpoints_support.json").exists()


def test_vendored_litellm_snapshot_covers_tracecat_first_class_providers() -> None:
    """The pinned snapshot should include the provider families Tracecat exposes."""
    model_prices = json.loads(
        (LITELLM_SNAPSHOT_DIR / "model_prices_and_context_window.json").read_text()
    )
    provider_support = json.loads(
        (LITELLM_SNAPSHOT_DIR / "provider_endpoints_support.json").read_text()
    )

    litellm_providers = {
        info["litellm_provider"]
        for info in model_prices.values()
        if isinstance(info, dict) and isinstance(info.get("litellm_provider"), str)
    }
    provider_keys = set(provider_support["providers"])

    assert "openai" in litellm_providers
    assert "anthropic" in litellm_providers
    assert "gemini" in litellm_providers
    assert "bedrock" in litellm_providers
    assert "vertex_ai" in litellm_providers
    assert "azure" in litellm_providers
    assert "azure_ai" in provider_keys


def test_litellm_proxy_config_has_built_in_provider_wildcards() -> None:
    """The embedded LiteLLM proxy must accept provider-prefixed built-in models."""
    config = yaml.safe_load(LITELLM_PROXY_CONFIG.read_text())
    model_list = config["model_list"]
    model_names = {
        item["model_name"]
        for item in model_list
        if isinstance(item.get("model_name"), str)
    }

    assert "openai/*" in model_names
    assert "anthropic/*" in model_names
    assert "gemini/*" in model_names
    assert "vertex_ai/*" in model_names
    assert "bedrock/*" in model_names
    assert "azure/*" in model_names
    assert "azure_ai/*" in model_names


def test_litellm_proxy_config_keeps_built_in_routes_wildcard_only() -> None:
    """Built-in providers should route through provider wildcards, not exact aliases."""
    config = yaml.safe_load(LITELLM_PROXY_CONFIG.read_text())
    model_names = [
        item["model_name"]
        for item in config["model_list"]
        if isinstance(item.get("model_name"), str)
    ]

    assert model_names == [
        "openai/*",
        "anthropic/*",
        "gemini/*",
        "vertex_ai/*",
        "bedrock/*",
        "azure/*",
        "azure_ai/*",
        "*",
    ]
