"""Hybrid Analysis integration.

Authentication method: Token-based

References: https://hybrid-analysis.com/docs/api/v2#/

Supported APIs:

```python
analyze_malware_sample = {
    "endpoint": "/v2/search/hash",
    "method": "GET",
    "ocsf_schema": "",
    "reference": "https://hybrid-analysis.com/docs/api/v2#/Search/"
}
```
"""

import os
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, registry

HA_BASE_URL = "https://www.hybrid-analysis.com/api/v2/"


def create_hybrid_analysis_client() -> httpx.AsyncClient:
    HA_API_KEY = os.getenv("HA_API_KEY")
    if HA_API_KEY is None:
        raise ValueError("HA_API_KEY is not set")
    client = httpx.AsyncClient(
        base_url=HA_BASE_URL,
        headers={"api-key": HA_API_KEY},
    )
    return client


@registry.register(
    description="Analyze a malware sample using Hybrid Analysis.",
    namespace="hybrid_analysis",
)
async def analyze_malware_sample(
    file_hash: Annotated[
        str, Field(..., description="The hash of the malware sample to analyze")
    ],
) -> dict[str, Any]:
    async with create_hybrid_analysis_client() as client:
        response = await client.get("/v2/search/hash", params={"hash": file_hash})
        response.raise_for_status()
        return response.json()
