from __future__ import annotations

from enum import StrEnum

from temporalio.common import SearchAttributeKey, SearchAttributePair


class TriggerType(StrEnum):
    """Trigger type for a workflow execution."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    WEBHOOK = "webhook"

    def to_temporal_search_attr_pair(self) -> SearchAttributePair[str]:
        return SearchAttributePair(
            key=SearchAttributeKey.for_keyword("TracecatTriggerType"),
            value=self.value,
        )
