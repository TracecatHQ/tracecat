"""Telemetry receipts carried separately from runtime ownership and diagnostics."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tracecat.runtime.errors import RuntimeErrorClassification, RuntimeErrorOwner


class PlatformErrorCapture(BaseModel):
    """Proof that the local SDK accepted a platform error for transport.

    This is an additional Temporal detail, not a new classification version.
    Older workers can still parse the unchanged classification detail. For an
    action loop, the event ID identifies one representative source capture; a
    receipt is propagated only when every platform child was captured.
    Receipts deduplicate activity wrappers, never terminal paging events.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    schema_: Literal["tracecat.sentry_capture.v1"] = Field(alias="schema")
    event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    classification: RuntimeErrorClassification

    @classmethod
    def for_error(
        cls, event_id: str, classification: RuntimeErrorClassification
    ) -> PlatformErrorCapture:
        """Associate an accepted event with the failure it covers."""
        return cls.model_validate(
            {
                "schema": "tracecat.sentry_capture.v1",
                "event_id": event_id,
                "classification": classification,
            }
        )

    @classmethod
    def for_aggregate(
        cls,
        classification: RuntimeErrorClassification,
        failures: Iterable[
            tuple[RuntimeErrorClassification, PlatformErrorCapture | None]
        ],
    ) -> PlatformErrorCapture | None:
        """Cover an aggregate only if all of its platform children are covered."""
        representative: PlatformErrorCapture | None = None
        for child_classification, capture in failures:
            if child_classification.owner is not RuntimeErrorOwner.PLATFORM:
                continue
            if capture is None or capture.classification != child_classification:
                return None
            representative = representative or capture
        if representative is None:
            return None
        return cls.for_error(representative.event_id, classification)
