"""Hybrid Analysis integration.

Authentication method: Token-based

Requires: A secret named `hybrid_analysis` with key `HA_API_KEY`

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

from tracecat.registry import Field, RegistrySecret, registry

HA_BASE_URL = "https://www.hybrid-analysis.com/api/v2/"

hybrid_analysis_secret = RegistrySecret(name="hybrid_analysis", keys=["HA_API_KEY"])
"""Hybrid Analysis secret.

Secret
------
- name: `hybrid_analysis`
- keys:
    - `HA_API_KEY`

Example Usage
-------------
Environment variable:
>>> os.environ["HA_API_KEY"]

Expression:
>>> ${{ SECRETS.hybrid_analysis.HA_API_KEY }}
"""


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
    default_title="Analyze malware sample",
    description="Analyze a malware sample using Hybrid Analysis.",
    display_group="Hybrid Analysis",
    namespace="integrations.hybrid_analysis",
    secrets=[hybrid_analysis_secret],
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
