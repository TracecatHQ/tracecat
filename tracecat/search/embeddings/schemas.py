"""Public workspace embedding setup contracts; secret values never appear here."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tracecat.search.embeddings.types import EmbeddingErrorCode, EmbeddingModel
from tracecat.search.types import SearchState


class EmbeddingConfigurationInput(BaseModel):
    """Choose a supported model and an existing workspace secret environment."""

    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai"]
    model: EmbeddingModel
    credential_id: uuid.UUID
    credential_environment: str = Field(min_length=1, max_length=255)


class EmbeddingConfigurationSave(EmbeddingConfigurationInput):
    """Save only if the current semantic version still matches the setup form."""

    expected_version: int = Field(ge=0)


class EmbeddingConfigurationDisable(BaseModel):
    """Invalidate the current configuration without deleting historical chunks."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)


class EmbeddingModelRead(BaseModel):
    """Supported model metadata for setup forms and bounded input preparation."""

    provider: Literal["openai"]
    model: EmbeddingModel
    endpoint: str
    dimensions: int
    tokenizer: str
    input_token_limit: int
    input_character_limit: int
    batch_size_limit: int
    batch_token_limit: int


class EmbeddingConfigurationRead(BaseModel):
    """Current pointer and optional validated configuration, without secret data."""

    version: int
    state: SearchState
    configuration: EmbeddingConfigurationInput | None = None
    supported_models: tuple[EmbeddingModelRead, ...]


class EmbeddingValidationRead(BaseModel):
    """Successful synthetic probe; never expose the probe vector or credentials."""

    valid: Literal[True] = True
    dimensions: int
    prompt_tokens: int


class EmbeddingErrorRead(BaseModel):
    """Stable error metadata for clients and retry scheduling."""

    code: EmbeddingErrorCode
    retryable: bool
    retry_after: float | None = None


class EmbeddingErrorResponse(BaseModel):
    """HTTP error envelope consumed by generated API clients."""

    detail: EmbeddingErrorRead
