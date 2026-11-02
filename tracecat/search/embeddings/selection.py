"""Select an embedding recipe from detached, permitted provider connections."""

from collections.abc import Sequence

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.search.embeddings.catalog import (
    API_KEY_FIELDS,
    PROVIDER_ORDER,
    default_model,
    recipe_revision,
)
from tracecat.search.embeddings.self_hosted import (
    SELF_HOSTED_MODELS,
    select_self_hosted_model,
)
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
    ProviderConnection,
    ResolvedCredential,
)
from tracecat.secrets.encryption import decrypt_keyvalues


def select_configuration(
    connections: Sequence[ProviderConnection],
    preferred: str | None,
) -> tuple[PinnedConfiguration, ResolvedCredential] | None:
    """Choose deterministically; a malformed chosen credential never falls back.

    Decrypt lazily: invalid credentials on a lower-priority connection must not
    break an otherwise usable provider. No database or network access occurs here.
    """
    ordered = sorted(
        connections,
        key=lambda c: (c.provider != preferred, PROVIDER_ORDER.index(c.provider)),
    )
    for connection in ordered:
        provider = connection.provider
        # Chat-only connections are not candidates. Their credentials must not
        # prevent selection of a provider that actually offers permitted embeddings.
        if provider in {"ollama", "vllm"} and not any(
            spec.provider == provider and spec.model in connection.models
            for spec in SELF_HOSTED_MODELS
        ):
            continue
        try:
            values = {
                item.key: item.value.get_secret_value()
                for item in decrypt_keyvalues(
                    connection.encrypted_keys, key=get_db_encryption_key()
                )
            }
            if (
                provider in {"openai", "gemini"}
                and not values.get(API_KEY_FIELDS[provider], "").strip()
            ):
                raise EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)
            if provider == "bedrock" and not (
                values.get("AWS_ROLE_ARN")
                or values.get("AWS_BEARER_TOKEN_BEDROCK")
                or (
                    values.get("AWS_ACCESS_KEY_ID")
                    and values.get("AWS_SECRET_ACCESS_KEY")
                )
            ):
                raise EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)
            # A proxy key must never be sent to the public OpenAI endpoint.
            if provider == "openai" and (
                base_url := values.get("OPENAI_BASE_URL", "").strip()
            ):
                if base_url.rstrip("/") != "https://api.openai.com/v1":
                    raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)
            if provider in {"ollama", "vllm"}:
                spec = select_self_hosted_model(provider, connection.models, values)
                if spec is None:
                    continue
            else:
                spec = default_model(provider, values.get("AWS_REGION"))
            return (
                PinnedConfiguration(
                    0,
                    spec,
                    connection.credential_id,
                    connection.environment,
                    recipe_revision(spec),
                ),
                ResolvedCredential(values),
            )
        except EmbeddingError as exc:
            error = EmbeddingError(exc.code)
        except Exception:
            error = EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)
        raise error
    return None
