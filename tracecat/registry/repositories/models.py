from datetime import datetime

from pydantic import UUID4, BaseModel, Field

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
    origin: str = Field(
        ...,
        description="The origin of the repository",
        min_length=1,
        max_length=255,
    )


class RegistryRepositoryUpdate(BaseModel):
    last_synced_at: datetime | None = None
    commit_sha: str | None = Field(
        default=None,
        description="The commit SHA of the repository",
        min_length=1,
        max_length=255,
    )
    origin: str | None = Field(
        default=None,
        description="The origin of the repository",
        min_length=1,
        max_length=255,
    )
