"""Regression coverage for the installed LiteLLM package catalog snapshot."""

from importlib.metadata import version
from pathlib import Path

import litellm
import yaml

LITELLM_PROXY_CONFIG = Path("tracecat/agent/litellm_config.yaml")
LITELLM_VERSION = version("litellm")


def test_installed_litellm_model_cost_exists() -> None:
    """The built-in catalog should source models from the installed LiteLLM package."""
    assert LITELLM_VERSION
    assert isinstance(litellm.model_cost, dict)
    assert litellm.model_cost


def test_installed_litellm_model_cost_covers_tracecat_first_class_providers() -> None:
    """The installed LiteLLM model map should include Tracecat's provider families."""
    model_prices = litellm.model_cost

    litellm_providers = {
        info["litellm_provider"]
        for info in model_prices.values()
        if isinstance(info, dict) and isinstance(info.get("litellm_provider"), str)
    }

    assert "openai" in litellm_providers
    assert "anthropic" in litellm_providers
    assert "gemini" in litellm_providers
    assert "bedrock" in litellm_providers
    assert "vertex_ai" in litellm_providers
    assert "azure" in litellm_providers
    assert "azure_ai" in litellm_providers


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
