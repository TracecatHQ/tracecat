# Automatic search embeddings

An embedding turns a piece of text into a list of numbers. Search compares these
lists to find text with similar meaning. Documents and queries must use the same
model and preprocessing rules for those comparisons to make sense.

Search reuses existing organization AI provider connections. It prefers the
organization's default agent provider when that provider has an eligible embedding
model. Otherwise it checks OpenAI, Gemini, Bedrock, Ollama, then vLLM. Workspace
model-access rules still apply. No eligible provider means no embedding work;
literal search and ordinary table operations remain available.

| Provider | Automatically selected model | Dimensions |
| --- | --- | ---: |
| OpenAI | `text-embedding-3-small` | 1536 |
| Gemini | `gemini-embedding-001` | 3072 |
| Amazon Bedrock | `amazon.titan-embed-text-v2:0` | 1024 |
| Ollama | `all-minilm:22m`, then `all-minilm:latest`, then `all-minilm` | 384 |
| vLLM | `sentence-transformers/all-MiniLM-L6-v2` | 384 |

## Using Ollama or vLLM

The server must already serve one of the supported embedding models. Tracecat
never downloads models or turns a chat model into an embedding model.

For Ollama, install the supported model on the Ollama server:

```sh
ollama pull all-minilm:22m
```

For vLLM, run a server for the supported model:

```sh
vllm serve sentence-transformers/all-MiniLM-L6-v2 --runner pooling
```

Configure the existing Ollama or vLLM provider connection with that server's
OpenAI-compatible base URL, including `/v1`. API keys are optional for servers
that do not require authentication. The URL must be reachable from Tracecat's
backend. Private server addresses must be allowed by the deployment's existing
`TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS` policy, as with agent model discovery.

Saving the connection discovers its models. If the connection already existed,
refresh its model catalog after installing the embedding model. The embedding
model itself must be enabled for the workspace: access to a chat model on the
same server is insufficient. Selection uses that saved catalog, so reading search
status makes no network requests. If the server changes, refresh the catalog;
stale model entries produce a typed provider error until refreshed.

Ollama requests use `/api/embed` with `truncate: false`. vLLM requests use
`/v1/embeddings`. Both use a conservative 240-byte input budget to leave room for
MiniLM's special tokens. The chunker processes long source text in smaller pieces;
this budget is not a limit on the complete document. Unknown model names and
custom vLLM aliases are not selected automatically.

## Keeping vectors consistent

The saved configuration includes the provider, endpoint, model, dimensions and
input-processing recipe. Changing these advances the configuration version and
requests rebuilding. Rotating an API key alone does not rebuild vectors. Each
embedding call checks the configuration before and after the network request;
consumers must also check its version when publishing or ranking results.

A provider failure never retries against a different model. Responses must have
matching model identities, the expected number of finite, nonzero vectors and
the correct dimensions. Self-hosted calls enforce the same outbound network
policy as agent discovery and do not follow redirects.

Keep the served weights stable for a given model name. Tracecat cannot detect a
server replacing weights behind an unchanged model name and URL. Changing the
endpoint to a new model deployment creates a new configuration and rebuilds.

References: [Ollama MiniLM](https://ollama.com/library/all-minilm),
[Ollama embedding API](https://docs.ollama.com/api/embed),
[vLLM embeddings](https://docs.vllm.ai/en/latest/models/pooling_models/embed/),
[MiniLM model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).
