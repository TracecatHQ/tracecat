from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tracecat.agent.types import AgentConfig, ModelSourceType
from tracecat.db.models import AgentCatalog, AgentModelSelectionLink, AgentSource


@dataclass(frozen=True, slots=True)
class ResolvedCatalogRecord:
    source_id: uuid.UUID | None
    model_provider: str
    model_name: str
    source_type: ModelSourceType
    source_name: str
    base_url: str | None
    last_refreshed_at: datetime | None
    metadata: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ResolvedExecutionContext:
    config: AgentConfig
    credentials: dict[str, str]
    catalog: AgentCatalog | None
    selection_link: AgentModelSelectionLink | None
    source: AgentSource | None
