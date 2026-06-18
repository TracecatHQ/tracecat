#!/usr/bin/env python3
"""Generate OpenAPI spec from FastAPI app without running server."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import and configure logger before importing the app
from httpx_oauth.oauth2 import BaseOAuth2  # noqa: E402

from tracecat.logger import logger  # noqa: E402

logger.remove()  # Remove all handlers
logger.add(lambda _: None)  # Add a no-op handler to prevent any output


class _OpenAPIOpenIDStub(BaseOAuth2[dict[str, str]]):
    """Stub OIDC client used only for OpenAPI generation.

    `httpx_oauth.clients.openid.OpenID` performs network discovery in `__init__`,
    which makes schema generation depend on a live issuer. For OpenAPI generation
    we only need route construction to succeed.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        openid_configuration_endpoint: str,
        name: str = "openid",
        base_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(
            client_id,
            client_secret,
            "https://issuer.example.com/oauth/authorize",
            "https://issuer.example.com/oauth/token",
            name=name,
            base_scopes=base_scopes,
        )


def _rewrite_octet_stream_to_binary(node: object) -> None:
    """Rewrite 3.1 ``contentMediaType`` binary schemas to 3.0 ``format: binary``.

    Recurses through an inline schema fragment, converting every
    ``{"type": "string", "contentMediaType": "application/octet-stream"}`` into
    ``{"type": "string", "format": "binary"}``.

    Recursion stops at ``$ref`` boundaries: a node containing ``$ref`` is treated
    as opaque and is neither mutated nor descended into. This keeps the rewrite
    confined to schema fragments owned by the caller and prevents leaking a
    multipart-specific conversion into shared component schemas that other
    (possibly non-multipart) usages also reference.
    """
    if isinstance(node, dict):
        if "$ref" in node:
            return
        if (
            node.get("type") == "string"
            and node.get("contentMediaType") == "application/octet-stream"
        ):
            node.pop("contentMediaType")
            node["format"] = "binary"
        for value in node.values():
            _rewrite_octet_stream_to_binary(value)
    elif isinstance(node, list):
        for item in node:
            _rewrite_octet_stream_to_binary(item)


def _normalize_multipart_binary_fields(spec: dict) -> None:
    """Pin multipart upload bodies to the binary dialect hey-api 0.48 understands.

    FastAPI on modern Starlette emits binary upload fields as
    ``{"type": "string", "contentMediaType": "application/octet-stream"}``
    (OpenAPI 3.1). Our pinned client generator (`@hey-api/openapi-ts` 0.48.3)
    only understands the 3.0 ``{"type": "string", "format": "binary"}`` shape and
    falls back to plain ``string`` otherwise, producing ``file: string`` instead
    of ``file: Blob | File`` for multipart upload bodies.

    Scope is deliberately narrow: only schemas referenced by a
    ``multipart/form-data`` request body are rewritten. Other ``octet-stream``
    string fields (e.g. base64 model payloads like ``BinaryContent.data`` or
    ``SecretRead.encrypted_keys``) are genuine strings on the frontend and must
    not become ``Blob | File``.

    This adapter pins the spec to the dialect the generator consumes, in one
    place at generation time. Remove once the generator is upgraded to a
    `contentMediaType`-aware version.
    See: TODO(tracking-issue) hey-api 0.48 -> latest migration.
    """
    schemas = spec.get("components", {}).get("schemas", {})

    # Count every $ref occurrence across the whole spec so we can tell a
    # single-use per-endpoint body schema (safe to rewrite in place) apart from
    # a shared component referenced by other, possibly non-multipart, usages.
    serialized = json.dumps(spec)

    def resolve_ref(ref: str) -> dict | None:
        prefix = "#/components/schemas/"
        if not ref.startswith(prefix):
            return None
        return schemas.get(ref[len(prefix) :])

    for methods in spec.get("paths", {}).values():
        if not isinstance(methods, dict):
            continue
        for operation in methods.values():
            if not isinstance(operation, dict):
                continue
            multipart = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("multipart/form-data")
            )
            if not isinstance(multipart, dict):
                continue
            schema = multipart.get("schema", {})
            ref = schema.get("$ref")
            if ref is None:
                # Inline body schema is owned by this operation: safe to rewrite.
                _rewrite_octet_stream_to_binary(schema)
                continue
            # Referenced component: only rewrite when this multipart body is its
            # sole reference, otherwise the conversion would leak to other
            # (possibly non-multipart) usages of the same shared schema.
            if serialized.count(f'"{ref}"') > 1:
                logger.warning(
                    "Skipping multipart binary normalization for shared schema "
                    f"{ref}: referenced by multiple usages."
                )
                continue
            target = resolve_ref(ref)
            if isinstance(target, dict):
                _rewrite_octet_stream_to_binary(target)


def main() -> None:
    """Generate OpenAPI spec and write to stdout or file."""
    os.environ["TRACECAT__AUTH_TYPES"] = "basic,oidc"
    os.environ["OIDC_ISSUER"] = "https://issuer.example.com"
    os.environ["OIDC_CLIENT_ID"] = "openapi-client"
    os.environ["OIDC_CLIENT_SECRET"] = "openapi-client-secret"
    os.environ["USER_AUTH_SECRET"] = "openapi-stub-secret"

    with patch("tracecat.auth.oidc.OpenID", _OpenAPIOpenIDStub):
        from tracecat.api.app import app

        openapi_spec = app.openapi()

    _normalize_multipart_binary_fields(openapi_spec)

    if len(sys.argv) > 1:
        # Write to file if path provided
        output_path = Path(sys.argv[1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(openapi_spec, indent=2))
        print(f"OpenAPI spec written to {output_path}", file=sys.stderr)
    else:
        # Write to stdout
        print(json.dumps(openapi_spec, indent=2))


if __name__ == "__main__":
    main()
