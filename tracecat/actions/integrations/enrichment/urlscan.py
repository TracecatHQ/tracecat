"""URLScan integration.

Authentication method: Token-based

Requires: URLSCAN_API_KEY

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
from typing import Any, Literal

import httpx
from tenacity import retry, stop_after_delay, wait_combine, wait_fixed

URLSCAN_BASE_URL = "https://urlscan.io/api/"


def create_urlscan_client() -> httpx.Client:
    headers = {"API-Key": os.environ["URLSCAN_API_KEY"]}
    return httpx.AsyncClient(base_url=URLSCAN_BASE_URL, headers=headers)


@retry(wait=wait_combine(wait_fixed(2), wait_fixed(10)), stop=stop_after_delay(120))
async def get_scan_result(scan_id: str) -> dict[str, Any]:
    async with create_urlscan_client() as client:
        rsp = client.get(f"result/{scan_id}/")
        if rsp.status_code == 200:
            return rsp.json()
        else:
            rsp.raise_for_status()


async def analyze_url(
    url: str, visibility: Literal["public", "unlisted", "private"] = "private"
) -> dict[str, Any]:
    """Analyze a URL and get report."""
    async with create_urlscan_client() as client:
        # Submit the URL for scanning
        rsp = client.post("scan/", json={"url": url, "visibility": visibility})
        rsp.raise_for_status()
        scan_id = rsp.json().get("uuid")
        if scan_id is None:
            raise httpx.RequestError("Scan ID not found in response")
        # Wait for scan results
        result = get_scan_result(scan_id)
    return result
