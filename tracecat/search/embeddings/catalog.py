"""Small supported catalog, with exact local token counting.

See https://developers.openai.com/api/docs/guides/embeddings and
https://developers.openai.com/api/reference/resources/embeddings/methods/create.
Our call limits are deliberately smaller than the provider's batch limits.
"""

import base64
import gzip
import hashlib
from functools import cache
from pathlib import Path

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


@cache
def _load_encoding() -> tiktoken.Encoding:
    """Load the pinned vocabulary locally; never download on a request thread."""
    path = Path(__file__).with_name("data") / "cl100k_base.tiktoken.gz"
    contents = gzip.decompress(path.read_bytes())
    if hashlib.sha256(contents).hexdigest() != (
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    ):
        raise ValueError("Bundled embedding vocabulary checksum mismatch")
    ranks: dict[bytes, int] = {}
    for line in contents.splitlines():
        token, rank = line.split()
        ranks[base64.b64decode(token)] = int(rank)
    # Exact cl100k_base pattern from tiktoken 0.14.0. Only ordinary encoding is
    # exposed below, so special-token handling is deliberately unnecessary.
    return tiktoken.Encoding(
        name="cl100k_base",
        pat_str=r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s""",
        mergeable_ranks=ranks,
        special_tokens={},
    )


class EmbeddingTokenCounter:
    """Exact ordinary-text counter compatible with the resumable chunker.

    Both initialization and counting are local. No special tokens are inserted
    or interpreted by these embedding models.
    """

    identity = "tiktoken:0.14.0:cl100k_base:ordinary:v1"

    def __init__(self) -> None:
        self._encoding = _load_encoding()

    def count_tokens(self, text: str) -> int:
        """Count the exact labeled input, including literal special-token text."""
        return len(self._encoding.encode_ordinary(text))
