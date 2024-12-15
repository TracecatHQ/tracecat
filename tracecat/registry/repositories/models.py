from datetime import datetime

from pydantic import UUID4, BaseModel

from tracecat.registry.actions.models import RegistryActionRead


class RegistryRepositoryRead(BaseModel):
    id: UUID4
    origin: str
    last_synced_at: datetime | None
    commit_sha: str | None
    actions: list[RegistryActionRead]


class RegistryRepositoryReadMinimal(BaseModel):
    id: UUID4
    origin: str
    last_synced_at: datetime | None
    commit_sha: str | None


class RegistryRepositoryCreate(BaseModel):
    origin: str


class RegistryRepositoryUpdate(BaseModel):
    last_synced_at: datetime | None = None
    commit_sha: str | None = None
    origin: str | None = None
