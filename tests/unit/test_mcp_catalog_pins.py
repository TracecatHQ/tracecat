"""Regression tests for QA-verified stdio MCP package pins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

QA_VERIFIED_STDIO_SOURCE_PINS = {
    "sentinelone-mcp": "07d4992089b10affff6163f296b1f6cb5734539f",
    "jamf-mcp": "53843c4da3ef666b70de1ee793fa9e8c432b9972",
}

# Source SHAs retain the QA provenance; exact registry versions are the runtime pins.
QA_VERIFIED_STDIO_PACKAGE_PINS = {
    "panther-mcp": {
        "manager": "uvx",
        "package": "mcp-panther==2.3.1",
        "source_sha": "7e6eca6f4f7f790057ed860bcfac669e13898d0c",
    },
    "google-cloud-secops-mcp": {
        "manager": "uvx",
        "package": "google-secops-mcp==0.7.0",
        "source_sha": "fde561ff33fb69a1565780bd5f141f4750cfc770",
    },
    "clickhouse-mcp": {
        "manager": "uvx",
        "package": "mcp-clickhouse==0.4.0",
        "source_sha": "5645bc9c2931ccba3314c29927b10e6cfafc7323",
    },
    "crowdstrike-falcon-mcp": {
        "manager": "uvx",
        "package": "falcon-mcp==0.13.0",
        "source_sha": "5f6c0581e8f941a3a05ad884c4ae667b85b8f6b7",
    },
    "virustotal-mcp": {
        "manager": "uvx",
        "package": "gti-mcp==0.1.2",
        "source_sha": "2552eeb5d0056317e352579a666c746a659e0b49",
    },
    "okta-mcp": {
        "manager": "uvx",
        "package": "okta-mcp-server==1.1.3",
        "source_sha": "f9f6157f62d3d436bfbfce84ac20f198fcb94dde",
    },
    "aws-mcp": {
        "manager": "uvx",
        "package": "mcp-proxy-for-aws==1.6.3",
        "source_sha": "9a3022410f88cf6a6800bf411504615dad1adf12",
    },
    "zscaler-mcp": {
        "manager": "uvx",
        "package": "zscaler-mcp==0.13.1",
        "source_sha": "23912913f8588c650b104d3bd30c0c755d6962cd",
    },
    "servicenow-mcp": {
        "manager": "uvx",
        "package": "servicenow-mcp==0.1.1",
        "source_sha": "06250607bdfc814f9bb56551bba16c3a7fb5a8c9",
    },
    "pagerduty-mcp": {
        "manager": "uvx",
        "package": "pagerduty-mcp==1.1.0",
        "source_sha": "62c806c98c90ac13de92d8b1ad7b4aa3ecc366c4",
    },
    "rootly-mcp": {
        "manager": "uvx",
        "package": "rootly-mcp-server==2.3.9",
        "source_sha": "f4f55a049dbee11c8321daf52e4d6d5e2ab4f806",
    },
    "semgrep-mcp": {
        "manager": "uvx",
        "package": "semgrep-mcp==0.9.0",
        "source_sha": "6e340f843bf82a2f42de77125ae75cfd020abf9b",
    },
    "greynoise-mcp": {
        "manager": "npx",
        "package": "@greynoise/greynoise-mcp-server@0.4.0",
        "source_sha": "017bc228439be1672da60b3f49ef902d6311ea51",
    },
    "snyk-mcp": {
        "manager": "npx",
        "package": "snyk@1.1306.0",
        "source_sha": "d4e9a98123a364a47b91770df8d86e2d31dcbc45",
    },
    "grafana-mcp": {
        "manager": "uvx",
        "package": "mcp-grafana==0.17.2",
        "source_sha": "fac7c8a312c6f6aee8330de72182dcf45bf4ae26",
    },
}


def _catalog_servers() -> dict[str, dict[str, Any]]:
    catalog_path = (
        Path(__file__).parents[2]
        / "packages/tracecat-ee/tracecat_ee/mcp/catalog/mcp_catalog_private.json"
    )
    payload = orjson.loads(catalog_path.read_bytes())
    return {server["slug"]: server for server in payload["servers"]}


def _public_catalog_servers() -> dict[str, dict[str, Any]]:
    catalog_path = (
        Path(__file__).parents[2] / "tracecat/integrations/catalog/mcp_catalog.json"
    )
    payload = orjson.loads(catalog_path.read_bytes())
    return {server["slug"]: server for server in payload["servers"]}


def _stdio_spec(server: dict[str, Any]) -> dict[str, Any]:
    if (spec := server.get("connection_spec")) and spec.get("server_type") == "stdio":
        return spec
    for option in server.get("connection_options", []):
        if (spec := option.get("connection_spec")) and spec.get(
            "server_type"
        ) == "stdio":
            return spec
    raise AssertionError(f"No stdio connection spec found for {server['slug']}")


def test_qa_verified_stdio_mcp_recipes_are_pinned_to_source_shas() -> None:
    servers = _catalog_servers()

    for slug, sha in QA_VERIFIED_STDIO_SOURCE_PINS.items():
        spec = _stdio_spec(servers[slug])
        packages = spec["packages"]

        assert spec["stdio_command"] == "uvx"
        assert sha in " ".join(spec["stdio_args"])
        assert packages
        assert all(package["manager"] == "uvx" for package in packages)
        assert all(sha in " ".join(package["args"]) for package in packages)
        assert all(sha in package["package"] for package in packages)


def test_qa_verified_registry_mcp_recipes_are_pinned_to_exact_versions() -> None:
    servers = _catalog_servers()

    for slug, expected in QA_VERIFIED_STDIO_PACKAGE_PINS.items():
        spec = _stdio_spec(servers[slug])
        packages = spec["packages"]

        assert len(packages) == 1
        assert spec["stdio_command"] == expected["manager"]
        assert expected["package"] in spec["stdio_args"]
        assert packages[0]["manager"] == expected["manager"]
        assert packages[0]["command"] == expected["manager"]
        assert packages[0]["args"] == spec["stdio_args"]
        assert packages[0]["package"] == expected["package"]
        assert len(expected["source_sha"]) == 40
        assert set(expected["source_sha"]) <= set("0123456789abcdef")


def test_unavailable_qa_integrations_are_coming_soon_without_specs() -> None:
    private_servers = _catalog_servers()
    public_servers = _public_catalog_servers()

    for slug in (
        "splunk-mcp",
        "hashicorp-vault-mcp",
        "palo-alto-mcp",
        "terraform-mcp",
    ):
        assert public_servers[slug]["status"] == "coming_soon"
        assert "connection_spec" not in private_servers[slug]
        assert private_servers[slug]["research_notes"].startswith("Coming soon")
