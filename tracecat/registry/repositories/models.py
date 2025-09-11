from datetime import datetime

from pydantic import UUID4, BaseModel, Field, field_validator

from tracecat.git.constants import GIT_SSH_URL_REGEX
from tracecat.registry.actions.models import (
    RegistryActionRead,
    RegistryActionValidationErrorInfo,
)
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

        # Delegate to shared regex to ensure consistency across validators
        if not GIT_SSH_URL_REGEX.match(v):
            raise TracecatValidationError(
                "Must be a valid Git SSH URL (e.g., git+ssh://git@github.com/org/repo.git)"
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


class GitCommitInfo(BaseModel):
    """Git commit information for repository management."""

    sha: str = Field(
        ...,
        description="The commit SHA hash",
        min_length=40,
        max_length=40,
    )
    message: str = Field(
        ...,
        description="The commit message",
        max_length=1000,
    )
    author: str = Field(
        ...,
        description="The commit author name",
        max_length=255,
    )
    author_email: str = Field(
        ...,
        description="The commit author email",
        max_length=255,
    )
    date: str = Field(
        ...,
        description="The commit date in ISO format",
        max_length=50,
    )
    tags: list[str] = Field(
        default_factory=list,
        description="List of tags associated with this commit",
    )


class RegistryRepositorySync(BaseModel):
    """Parameters for syncing a repository to a specific commit."""

    target_commit_sha: str | None = Field(
        default=None,
        description="The specific commit SHA to sync to. If None, syncs to HEAD.",
        min_length=40,
        max_length=40,
    )


class RegistryRepositoryErrorDetail(BaseModel):
    """Error response model for registry sync failures."""

    id: str
    origin: str
    message: str
    errors: dict[str, list[RegistryActionValidationErrorInfo]]
