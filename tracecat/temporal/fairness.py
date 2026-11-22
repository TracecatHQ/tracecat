"""Temporal task queue fairness: one fairness key per organization."""

from __future__ import annotations

import dataclasses

from temporalio.common import Priority

from tracecat.identifiers import OrganizationID


def org_priority(
    organization_id: OrganizationID | None,
    base: Priority = Priority.default,
) -> Priority:
    """Return ``base`` with the organization as its fairness key.

    Activities and child workflows inherit each unset priority field from the
    workflow that schedules them, so setting the key when a workflow starts
    covers the executor and agent queues. The key has no effect until fairness
    is enabled on the Temporal namespace.

    Args:
        organization_id: The organization that owns the work, or ``None`` for
            system work, which keeps the default (empty) key.
        base: The priority to extend. Its priority key is kept.

    Returns:
        ``base`` with ``fairness_key`` set to the organization ID.
    """
    if organization_id is None:
        return base
    return dataclasses.replace(base, fairness_key=str(organization_id))
