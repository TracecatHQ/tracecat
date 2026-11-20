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

    Activities and child workflows inherit the fairness key from the workflow
    that schedules them, so setting it when a workflow starts covers the
    executor and agent queues. The key has no effect until fairness is enabled
    on the Temporal namespace.
    """
    if organization_id is None:
        return base
    return dataclasses.replace(base, fairness_key=str(organization_id))
