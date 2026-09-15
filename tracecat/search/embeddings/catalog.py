"""Small supported catalog, with exact local token counting.

See https://developers.openai.com/api/docs/guides/embeddings and
https://developers.openai.com/api/reference/resources/embeddings/methods/create.
Our call limits are deliberately smaller than the provider's batch limits.
"""

import tiktoken

from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    ModelSpec,
)

MODELS = (
    ModelSpec(model="text-embedding-3-small", dimensions=1536),
    ModelSpec(model="text-embedding-3-large", dimensions=3072),
)


def get_model(provider: str, model: str) -> ModelSpec:
    """Resolve only an explicitly supported model; never choose a fallback."""
    for spec in MODELS:
        if spec.provider == provider and spec.model == model:
            return spec
    raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)


class EmbeddingTokenCounter:
    """Exact ordinary-text counter compatible with the resumable chunker.

    Construct outside database transactions. Tiktoken may initialize its cached
    vocabulary on first construction; count_tokens itself is entirely local.
    No special tokens are inserted or interpreted by these embedding models.
    """

    identity = "tiktoken:0.14.0:cl100k_base:ordinary:v1"

    def __init__(self) -> None:
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        """Count the exact labeled input, including literal special-token text."""
        return len(self._encoding.encode_ordinary(text))
