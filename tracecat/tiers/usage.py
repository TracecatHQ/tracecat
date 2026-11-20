"""Observed usage of per-organization concurrency limits."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tracecat.tiers.enums import ConcurrencyLimitName
from tracecat.tiers.types import ConcurrencyUsage

if TYPE_CHECKING:
    from tracecat.identifiers import OrganizationID


async def read_concurrency_usage(
    org_id: OrganizationID,
) -> dict[ConcurrencyLimitName, ConcurrencyUsage]:
    """Read the observed usage of an organization's concurrency limits.

    Every usage read goes through this function, so callers never touch the
    lease store or the run ledger directly. A limit missing from the result has
    unknown usage, which callers must report as unknown, never as zero.

    Nothing records usage yet, so every limit is unknown.

    Args:
        org_id: The organization to read usage for.

    Returns:
        Usage keyed by limit, for the limits whose usage could be read.
    """
    del org_id
    return {}
