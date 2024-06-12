"""Unified API for enrichemnts.

Supported Capabilities:
- `analyze_email`: `email` required.
- `analyze_url`: `url` required.
- `analyze_ip_address`: `ip_address` required.
- `analyze_malware_sample`: `file_hash` required.
"""

import os

from tracecat.actions.integrations import get_capability


async def analyze_email(email: str, vendor: str) -> list[dict]:
    secret = os.getenv(vendor)
    analyze = get_capability(
        category="enrichment", capability="analyze_email", vendor=vendor
    )
    alerts = await analyze(email=email, **secret)
    return alerts


async def analyze_url(url: str, vendor: str) -> list[dict]:
    secret = os.getenv(vendor)
    analyze = get_capability(
        category="enrichment", capability="analyze_url", vendor=vendor
    )
    alerts = await analyze(url=url, **secret)
    return alerts


async def analyze_ip_address(ip_address: str, vendor: str) -> list[dict]:
    secret = os.getenv(vendor)
    analyze = get_capability(
        category="enrichment", capability="analyze_ip_address", vendor=vendor
    )
    alerts = await analyze(ip_address=ip_address, **secret)
    return alerts


async def analyze_malware_sample(file_hash: str, vendor: str) -> list[dict]:
    secret = os.getenv(vendor)
    analyze = get_capability(
        category="enrichment", capability="analyze_malware_sample", vendor=vendor
    )
    alerts = await analyze(file_hash=file_hash, **secret)
    return alerts
