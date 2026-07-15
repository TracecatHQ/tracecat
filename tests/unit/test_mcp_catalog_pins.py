"""Regression tests for QA-verified stdio MCP source pins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

QA_VERIFIED_STDIO_PINS = {
    "panther-mcp": "7e6eca6f4f7f790057ed860bcfac669e13898d0c",
    "google-cloud-secops-mcp": "fde561ff33fb69a1565780bd5f141f4750cfc770",
    "clickhouse-mcp": "5645bc9c2931ccba3314c29927b10e6cfafc7323",
    "crowdstrike-falcon-mcp": "5f6c0581e8f941a3a05ad884c4ae667b85b8f6b7",
    "sentinelone-mcp": "07d4992089b10affff6163f296b1f6cb5734539f",
    "jamf-mcp": "53843c4da3ef666b70de1ee793fa9e8c432b9972",
    "virustotal-mcp": "2552eeb5d0056317e352579a666c746a659e0b49",
    "okta-mcp": "f9f6157f62d3d436bfbfce84ac20f198fcb94dde",
    "aws-mcp": "9a3022410f88cf6a6800bf411504615dad1adf12",
    "zscaler-mcp": "23912913f8588c650b104d3bd30c0c755d6962cd",
    "servicenow-mcp": "06250607bdfc814f9bb56551bba16c3a7fb5a8c9",
    "pagerduty-mcp": "62c806c98c90ac13de92d8b1ad7b4aa3ecc366c4",
    "rootly-mcp": "f4f55a049dbee11c8321daf52e4d6d5e2ab4f806",
    "semgrep-mcp": "6e340f843bf82a2f42de77125ae75cfd020abf9b",
}


def _catalog_servers() -> dict[str, dict[str, Any]]:
    catalog_path = (
        Path(__file__).parents[2]
        / "packages/tracecat-ee/tracecat_ee/mcp/catalog/mcp_catalog_private.json"
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

    for slug, sha in QA_VERIFIED_STDIO_PINS.items():
        spec = _stdio_spec(servers[slug])
        packages = spec["packages"]

        assert spec["stdio_command"] == "uvx"
        assert sha in " ".join(spec["stdio_args"])
        assert packages
        assert all(package["manager"] == "uvx" for package in packages)
        assert all(sha in " ".join(package["args"]) for package in packages)
        assert all(sha in package["package"] for package in packages)
