# Embedding tokenizer data

`cl100k_base.tiktoken.gz` is the unchanged OpenAI vocabulary, compressed with
zero gzip timestamp for reproducibility. Shipping it makes tokenizer startup
independent of outbound network access and writable runtime caches.

Source: https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken

Uncompressed SHA-256:
`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`

The vocabulary and matching regular expression in `catalog.py` are pinned to
`tiktoken==0.14.0`; the upstream license is included here. Verify the hash and
ordinary tokenization compatibility when updating either asset or dependency.
