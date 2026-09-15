"""Provider contracts independent of database sessions and table contents."""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from tracecat.search.types import EmbeddingResult

EmbeddingModel = Literal["text-embedding-3-small", "text-embedding-3-large"]


class EmbeddingErrorCode(StrEnum):
    """Stable public failures; provider messages must never cross this boundary."""

    CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
    CONFIGURATION_INVALID = "CONFIGURATION_INVALID"
    CONFIGURATION_CHANGED = "CONFIGURATION_CHANGED"
    INPUT_INVALID = "INPUT_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    RESPONSE_INVALID = "RESPONSE_INVALID"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class EmbeddingError(Exception):
    """Safe provider failure with optional bounded retry scheduling metadata."""

    def __init__(self, code: EmbeddingErrorCode, retry_after: float | None = None):
        self.code = code
        self.retry_after = retry_after
        super().__init__(code.value)

    @property
    def retryable(self) -> bool:
        """Whether a later call may succeed without changing configuration."""
        return self.code in {
            EmbeddingErrorCode.RATE_LIMITED,
            EmbeddingErrorCode.TIMEOUT,
            EmbeddingErrorCode.UNAVAILABLE,
        }


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Embedding semantics and conservative per-call resource limits."""

    model: EmbeddingModel
    dimensions: int
    provider: Literal["openai"] = "openai"
    endpoint: str = "https://api.openai.com/v1/embeddings"
    tokenizer: str = "tiktoken:0.14.0:cl100k_base:ordinary:v1"
    input_token_limit: int = 8191
    batch_size_limit: int = 32
    batch_token_limit: int = 16000
    input_character_limit: int = 131072


@dataclass(frozen=True, slots=True)
class PinnedConfiguration:
    """A detached snapshot; semantic settings never change within a version."""

    version: int
    spec: ModelSpec
    credential_id: uuid.UUID
    credential_environment: str


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """Short-lived decrypted key and a digest to detect rotation during validation."""

    api_key: SecretStr = field(repr=False)
    fingerprint: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """Mapped results and provider-reported token usage for one bounded call."""

    results: tuple[EmbeddingResult, ...]
    prompt_tokens: int
    total_tokens: int


class ProviderVector(BaseModel):
    """Strict parsing of one indexed OpenAI embedding response."""

    model_config = ConfigDict(strict=True)
    index: int = Field(ge=0)
    embedding: list[float]


class ProviderUsage(BaseModel):
    """Nonnegative usage reported by the provider."""

    model_config = ConfigDict(strict=True)
    prompt_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ProviderResponse(BaseModel):
    """Only the documented response fields needed for validation."""

    model_config = ConfigDict(strict=True)
    model: str
    data: list[ProviderVector]
    usage: ProviderUsage
