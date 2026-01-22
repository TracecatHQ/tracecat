"""Organization management types."""

from __future__ import annotations

from dataclasses import dataclass

from tracecat.ee.compute.schemas import Tier


@dataclass
class TierChangeResult:
    """Result of a tier change operation."""

    previous_tier: Tier
    new_tier: Tier
    worker_pool_provisioned: bool
    worker_pool_deprovisioned: bool
    error: str | None = None
