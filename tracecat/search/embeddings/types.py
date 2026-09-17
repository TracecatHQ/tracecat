"""Provider contracts independent of database sessions and table contents."""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from tracecat.search.types import EmbeddingResult

EmbeddingProvider = Literal["openai", "gemini", "bedrock", "ollama", "vllm"]
EmbeddingModel = Literal[
    "text-embedding-3-small",
    "text-embedding-3-large",
    "gemini-embedding-001",
    "amazon.titan-embed-text-v2:0",
    "all-minilm",
    "all-minilm:latest",
    "all-minilm:22m",
    "sentence-transformers/all-MiniLM-L6-v2",
]


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
    provider: EmbeddingProvider = "openai"
    endpoint: str = "https://api.openai.com/v1/embeddings"
    tokenizer: str = "tiktoken:0.14.0:cl100k_base:ordinary:v1"
    input_token_limit: int = 8191
    batch_size_limit: int = 32
    batch_token_limit: int = 16000
    input_character_limit: int = 131072
    # Bump when adapter preprocessing or task parameters change vector meaning.
    recipe_version: int = 1


@dataclass(frozen=True, slots=True)
class PinnedConfiguration:
    """A detached snapshot; semantic settings never change within a version."""

    version: int
    spec: ModelSpec
    credential_id: uuid.UUID
    credential_environment: str
    recipe_revision: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """Short-lived decrypted provider settings; never included in repr or persisted."""

    values: dict[str, str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """Mapped results and provider-reported token usage for one bounded call."""

    results: tuple[EmbeddingResult, ...]
    prompt_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Decoded vectors in input order; missing usage stays unknown."""

    vectors: list[list[float]]
    prompt_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True, slots=True)
class ProviderConnection:
    """Permitted catalog models and their saved organization credential binding."""

    provider: EmbeddingProvider
    models: frozenset[str]
    credential_id: uuid.UUID
    environment: str
    encrypted_keys: bytes = field(repr=False)
