"""Small supported catalog, with exact local token counting.

See https://developers.openai.com/api/docs/guides/embeddings and
https://developers.openai.com/api/reference/resources/embeddings/methods/create.
Our call limits are deliberately smaller than the provider's batch limits.
"""

import base64
import gzip
import hashlib
import re
from dataclasses import replace
from functools import cache
from pathlib import Path

import orjson
import tiktoken

from tracecat.search.embeddings.self_hosted import SELF_HOSTED_MODELS
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    EmbeddingProvider,
    ModelSpec,
)

MODELS = (
    ModelSpec(model="text-embedding-3-small", dimensions=1536),
    ModelSpec(model="text-embedding-3-large", dimensions=3072),
    ModelSpec(
        model="gemini-embedding-001",
        dimensions=3072,
        provider="gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents",
        tokenizer="utf8-bytes:v1",
        input_token_limit=2048,
        batch_token_limit=16000,
        input_character_limit=2048,
    ),
    ModelSpec(
        model="amazon.titan-embed-text-v2:0",
        dimensions=1024,
        provider="bedrock",
        endpoint="",
        tokenizer="utf8-bytes:v1",
        input_token_limit=8192,
        batch_size_limit=1,
        batch_token_limit=8192,
        input_character_limit=8192,
    ),
    *SELF_HOSTED_MODELS,
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


PROVIDER_ORDER: tuple[EmbeddingProvider, ...] = (
    "openai",
    "gemini",
    "bedrock",
    "ollama",
    "vllm",
)


def default_model(provider: EmbeddingProvider, region: str | None = None) -> ModelSpec:
    """Select a fixed cloud model; self-hosted models require catalog discovery."""
    if provider in {"ollama", "vllm"}:
        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)
    spec = next(spec for spec in MODELS if spec.provider == provider)
    if provider != "bedrock":
        return spec
    if region is None or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", region):
        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)
    suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
    return replace(
        spec,
        endpoint=f"https://bedrock-runtime.{region}.{suffix}/model/{spec.model}/invoke",
    )


class ByteTokenCounter:
    """Conservative local budget for providers without a bundled tokenizer.

    Counting UTF-8 bytes bounds input more tightly than their token limit.
    The chunker still traverses all source text; this only makes chunks smaller.
    """

    identity = "utf8-bytes:v1"

    def count_tokens(self, text: str) -> int:
        return len(text.encode("utf-8"))


def token_counter(spec: ModelSpec) -> EmbeddingTokenCounter | ByteTokenCounter:
    """Return the pinned counter used by both chunk preparation and embedding."""
    return EmbeddingTokenCounter() if spec.provider == "openai" else ByteTokenCounter()


def recipe_revision(spec: ModelSpec) -> str:
    """Fingerprint persisted semantics, excluding operational batch limits.

    Tokenizer/input limits affect chunk boundaries. Adapter task or preprocessing
    changes require bumping recipe_version, even if the model name stays the same.
    """
    return hashlib.sha256(
        orjson.dumps(
            {
                "provider": spec.provider,
                "model": spec.model,
                "endpoint": spec.endpoint,
                "dimensions": spec.dimensions,
                "tokenizer": spec.tokenizer,
                "input_token_limit": spec.input_token_limit,
                "input_character_limit": spec.input_character_limit,
                "recipe_version": spec.recipe_version,
            },
            option=orjson.OPT_SORT_KEYS,
        )
    ).hexdigest()
