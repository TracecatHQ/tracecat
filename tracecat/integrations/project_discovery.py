"""Integrations with Project Discovery API.

Supported endpoints:
- Results: see and managed vulnerabilities detected by PD Cloud Platform
- (Coming soon) Scans: manage scans, scan schedules, and create new scans

Required credentials: `project_discovery` secret with `PD_API_KEY` key.

API reference: https://docs.projectdiscovery.io/api-reference/introduction
"""

import os
from typing import Any, Literal

import httpx

from tracecat.integrations._registry import registry

PD_BASE_URL = "https://api.projectdiscovery.io/v1"
# https://docs.projectdiscovery.io/introduction
PD_SEVERITIES = Literal["info", "low", "medium", "high", "critical", "none"]
PD_TIME_FILTERS = Literal["last_day", "last_week", "last_month", "none"]
PD_VULN_STATUSES = Literal["open", "closed" "false_positive", "fixed", "none"]


def create_pd_client() -> httpx.Client:
    headers = {"X-Api-Key": os.environ["PD_API_KEY"]}
    return httpx.Client(base_url=PD_BASE_URL, headers=headers)


@registry.register(description="Get all scan results", secrets=["project_discovery"])
def get_all_scan_results(
    severity: PD_SEVERITIES,
    time: PD_TIME_FILTERS,
    vuln_status: PD_VULN_STATUSES,
    offset: int | None = None,
    limit: int | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """Get all scan results.

    API reference: https://docs.projectdiscovery.io/api-reference/results/get-all-results
    """

    with create_pd_client() as client:
        response = client.get(
            "/scans/results",
            params={
                "offset": offset,
                "limit": limit,
                "severity": severity if severity != "none" else None,
                "search": search,
                "time": time if time != "none" else None,
                "vuln_status": vuln_status if vuln_status != "none" else None,
            },
        )
        response.raise_for_status()
        return response.json()
