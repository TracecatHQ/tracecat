import re
from datetime import datetime

from pydantic import UUID4, BaseModel, Field, field_validator

from tracecat.registry.actions.models import RegistryActionRead
from tracecat.registry.constants import (
    CUSTOM_REPOSITORY_ORIGIN,
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.types.exceptions import TracecatValidationError


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

    @field_validator("origin", mode="before")
    def validate_origin(cls, v: str) -> str:
        """Validates if a git+ssh URL is safe and properly formatted.

        Args:
            v: The URL string to validate

        Returns:
            str: The validated URL

        Raises:
            ValueError: If the URL is invalid or potentially unsafe
        """
        if v in (
            DEFAULT_REGISTRY_ORIGIN,
            CUSTOM_REPOSITORY_ORIGIN,
            DEFAULT_LOCAL_REGISTRY_ORIGIN,
        ):
            return v

        # Aside from the default origins, we only support git+ssh URLs
        if not v.startswith("git+ssh://"):
            raise TracecatValidationError("Only git+ssh URLs are currently supported")
        if not re.match(
            r"^git\+ssh://git@"  # Protocol and user prefix
            r"[-a-zA-Z0-9.]+"  # Hostname
            r"/[-a-zA-Z0-9._]+/"  # Organization/user
            r"[-a-zA-Z0-9._]+\.git$",  # Repository name
            v,
        ):
            raise TracecatValidationError(
                "Invalid or unsafe git SSH URL format. Expected format: "
                "git+ssh://git@host/org/repo.git"
            )
        return v


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
