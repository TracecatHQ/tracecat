"""Read-only automatic embedding availability; never expose credential bindings."""

from pydantic import BaseModel

from tracecat.search.embeddings.types import (
    EmbeddingErrorCode,
    EmbeddingModel,
    EmbeddingProvider,
)
from tracecat.search.types import SearchState


class EmbeddingModelRead(BaseModel):
    """Public metadata needed for status and bounded chunk preparation."""

    provider: EmbeddingProvider
    model: EmbeddingModel
    dimensions: int
    tokenizer: str
    input_token_limit: int
    input_character_limit: int
    batch_size_limit: int
    batch_token_limit: int


class EmbeddingConfigurationRead(BaseModel):
    """Availability from existing provider settings and current indexing state."""

    available: bool
    version: int
    state: SearchState
    configuration: EmbeddingModelRead | None = None
    reindex_required: bool = False


class EmbeddingErrorRead(BaseModel):
    code: EmbeddingErrorCode
    retryable: bool
    retry_after: float | None = None


class EmbeddingErrorResponse(BaseModel):
    detail: EmbeddingErrorRead
