"""Domain types for the agent model catalog."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, NamedTuple, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModelKey(NamedTuple):
    """Portable identity of a catalog model across deployments.

    ``catalog_id`` is a random per-environment UUID, so this pair is the stable
    identifier used to correlate an imported model with a local catalog row.
    """

    model_provider: str
    model_name: str


class ModelSelectionUpdate(TypedDict):
    """Fields a replacement rewrites on a run's model selection."""

    model_provider: str
    model_name: str
    catalog_id: UUID | None


@dataclass(frozen=True, slots=True)
class ModelReplacement:
    """Model a run moves to instead of the one it selected."""

    model_provider: str
    model_name: str
    catalog_id: UUID | None
    # Inline Literal: Temporal's dataclass decoder rejects ``type`` aliases.
    reason: Literal["retired"] = "retired"

    def selection_update(self) -> ModelSelectionUpdate:
        """Return the selection fields to overwrite, provider included."""
        return {
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "catalog_id": self.catalog_id,
        }


type ModelLifecycleStatus = Literal["deprecated", "retired"]


class ModelLifecycle(BaseModel):
    """Provider-announced deprecation of one model."""

    # Ignore unknown keys so older workers still read newer lifecycle data.
    model_config = ConfigDict(extra="ignore", frozen=True)

    deprecated_on: date | None = Field(default=None)
    retires_on: date
    replacement: str | None = Field(
        default=None,
        description="Same-provider catalog model that retired runs move to.",
    )

    def status_on(self, today: date | None = None) -> ModelLifecycleStatus:
        """Return ``retired`` from ``retires_on`` onward, else ``deprecated``."""
        today = today or datetime.now(UTC).date()
        return "retired" if today >= self.retires_on else "deprecated"

    def replacement_key(self, model_provider: str) -> ModelKey | None:
        """Return the replacement's identity; it shares the retired model's provider."""
        if self.replacement is None:
            return None
        return ModelKey(model_provider, self.replacement)


class RawRetiredModel(BaseModel):
    """Bundled ``retired`` entry: a model kept only for its lifecycle."""

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    model_provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    lifecycle: ModelLifecycle
