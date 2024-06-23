"""URLScan integration.

Authentication method: Token-based

Requires: A secret named `urlscan` with key `URLSCAN_API_KEY`.

References: https://urlscan.io/docs/api/

Supported APIs:

```python
analyze_url = {
    "endpoint": ["/v1/scan", "/v1/result"],
    "method": ["POST", "GET"],
    "ocsf_schema": "",
    "reference": [
        "https://urlscan.io/docs/api/#submission",
        "https://urlscan.io/docs/api/#result"
    ],
}
```
"""

import os
from typing import Annotated, Any, Literal

import httpx
from tenacity import retry, stop_after_delay, wait_combine, wait_fixed

from tracecat.registry import Field, registry

URLSCAN_BASE_URL = "https://urlscan.io/api/"


def create_urlscan_client() -> httpx.AsyncClient:
    headers = {"API-Key": os.environ["URLSCAN_API_KEY"]}
    return httpx.AsyncClient(base_url=URLSCAN_BASE_URL, headers=headers)


@retry(wait=wait_combine(wait_fixed(2), wait_fixed(10)), stop=stop_after_delay(120))
async def _get_scan_result(scan_id: str) -> dict[str, Any]:
    async with create_urlscan_client() as client:
        rsp = await client.get(f"result/{scan_id}/")
        if rsp.status_code == 200:
            return rsp.json()
        else:
            rsp.raise_for_status()


@registry.register(
    default_title="Get scan result",
    description="Get the scan result from URLScan by scan ID.",
    display_group="URLScan",
    namespace="integrations.urlscan",
    secrets=["urlscan"],
)
async def get_scan_result(
    scan_id: Annotated[
        str, Field(..., description="The scan ID to retrieve the result for")
    ],
) -> dict[str, Any]:
    """Get the scan result from URLScan by scan ID."""
    return await _get_scan_result(scan_id)


@registry.register(
    default_title="Analyze URL",
    description="Analyze a URL using URLScan.",
    display_group="URLScan",
    namespace="integrations.urlscan",
    secrets=["urlscan"],
)
async def analyze_url(
    url: Annotated[str, Field(..., description="The URL to analyze")],
    visibility: Annotated[
        Literal["public", "unlisted", "private"],
        Field(default="private", description="The visibility of the scan"),
    ] = "private",
) -> dict[str, Any]:
    """Analyze a URL and get report."""
    async with create_urlscan_client() as client:
        # Submit the URL for scanning
        rsp = await client.post("scan/", json={"url": url, "visibility": visibility})
        rsp.raise_for_status()
        scan_id = rsp.json().get("uuid")
        if scan_id is None:
            raise httpx.RequestError("Scan ID not found in response")
        # Wait for scan results
        result = await get_scan_result(scan_id=scan_id)
    return result
